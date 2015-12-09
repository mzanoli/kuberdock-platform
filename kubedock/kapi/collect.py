"""
Module collect to gather and send usage case info back to developers
"""

import requests
import re
import subprocess
from itertools import izip
from collections import Counter
import json

from flask import current_app
import ipaddress

from kubedock.core import db, ssh_connect
from kubedock.settings import (
    AWS, ID_PATH, STAT_URL, CEPH, KUBERDOCK_INTERNAL_USER)
from kubedock.kapi.users import UserCollection
from kubedock.kapi.podcollection import PodCollection
from kubedock.updates.models import Updates
from kubedock.nodes.models import Node
from kubedock.usage.models import PersistentDiskState, PodState
from kubedock.pods.models import Pod, IPPool, PodIP
from kubedock.users.models import User
from kubedock.predefined_apps.models import PredefinedApp


def get_users_number(role='User'):
    return len([u for u in UserCollection().get()
                if u.get('rolename') == role
                    and u.get('username') != KUBERDOCK_INTERNAL_USER])

def get_pods():
    data = {}
    pods = PodCollection().get(as_json=False)
    data['total'] = len(pods)
    data['running'] = len([p for p in pods
        if p.get('status') != 'stopped'])
    return data


def get_updates():
    return [{u.fname: u.status} for u in Updates.query.all()]


def fetch_nodes():
    """
    Gets basic nodes data: IP address, cpu cores and memory
    """
    return [{'_ip': node.ip} for node in Node.get_all()]


def extend_nodes(nodes):
    """
    For every node in list make cadvisor request to get additional info
    :param nodes: list --> list of nodes
    """
    fmt = 'http://{0}:4194/api/v1.3/machine'
    cadvisor_key_map = {
        'filesystems': 'disks',
        'num_cores': 'cores',
        'cpu_frequency_khz': 'clock',
        'machine_id': 'node-id'
    }

    for node in nodes:
        try:
            _ip = node.pop('_ip')
            r = requests.get(fmt.format(_ip))
            if r.status_code != 200:
                continue
            data = r.json()
            for cadvkey, nodekey in cadvisor_key_map.iteritems():
                node[nodekey] = data.get(cadvkey)
            node['nics'] = len(data.get('network_devices', []))
        except (requests.exceptions.ConnectionError, AttributeError):
            pass

        ssh, error_message = ssh_connect(_ip)
        if error_message:
            continue
        _, o, _ = ssh.exec_command('rpm -q kuberdock')
        if o.channel.recv_exit_status() == 0:
            node['kuberdock'] = o.read()

        node['kernel'] = get_node_kernel_version(ssh)
        node['cpu'] = get_node_cpu_usage(ssh)
        extend_node_memory_info(ssh, node)
        node['containers'] = get_node_container_counts(ssh)
        node['docker'] = get_node_package_version(ssh, 'docker')
        node['la'] = get_node_load_avg(ssh)
    return nodes


def get_node_package_version(ssh, package):
    _, o, _ = ssh.exec_command(
        'rpm -q --qf "%{VERSION}-%{RELEASE}" ' + package
    )
    res = 'unknown'
    if o.channel.recv_exit_status() == 0:
        res = o.read()
    return res


def get_node_load_avg(ssh):
    _, o, _ = ssh.exec_command(
        'cat /proc/loadavg'
    )
    res = []
    if o.channel.recv_exit_status() == 0:
        res = [float(item) for item in o.read().split()[:3]]
    return res


def get_node_kernel_version(ssh):
    _, o, _ = ssh.exec_command('uname -r')
    res = 'unknown'
    if o.channel.recv_exit_status() == 0:
        res = o.read().strip('\n')
    return res


def get_node_cpu_usage(ssh):
    _, o, _ = ssh.exec_command('top -b -n1|grep "^%Cpu(s):"')
    res = {}
    if o.channel.recv_exit_status() == 0:
        top_out = o.read()
        parts = top_out.replace('%Cpu(s):', '').strip().split(', ')
        for part in parts:
            try:
                value, key = part.split()
                res[key.strip()] = float(value.replace(',', '.'))
            except ValueError:
                pass
    return res


def extend_node_memory_info(ssh, node):
    _, o, _ = ssh.exec_command('free -b')
    if o.channel.recv_exit_status() != 0:
        return
    keyline = 0
    memline = 1
    swapline = 2
    lines = o.read().split('\n')
    # '             total        used        free      shared ...'
    keys = lines[keyline].strip().split()
    # Mem:     1930043392   806088704   264581120   119083008 ...
    mem = lines[memline].split(':')[-1].strip().split()
    swap = lines[swapline].split(':')[-1].strip().split()
    node['memory'] = dict(izip(keys, mem))
    node['swap'] = dict(izip(keys, swap))


def get_node_container_counts(ssh):
    counters = {}
    _, o, _ = ssh.exec_command('docker ps|wc -l')
    if o.channel.recv_exit_status() == 0:
        try:
            counters['running'] = int(o.read())
        except:
            pass
    _, o, _ = ssh.exec_command('docker ps -a|wc -l')
    if o.channel.recv_exit_status() == 0:
        try:
            counters['total'] = int(o.read())
        except:
            pass
    return counters


def get_version(package, patt=re.compile(r"""(\d[\d\.\-]+\d)(?=\.?\D)""")):
    """
    Get RPM package version
    :param package: string -> RPM package name
    :param patt: object -> compiled regexp
    :return: string -> version of the given package or None if missing
    """
    try:
        rv = subprocess.check_output(['rpm', '-q', package])
        return patt.search(rv).group(1)
    except (subprocess.CalledProcessError, AttributeError):
        return 'unknown'


def read_or_write_id(data=None):
    """
    Read system ID from file or write it to file
    :param path: string -> path to file
    :param data: string -> new system ID to write
    :return: string -> retrieved id or None if
    """
    try:
        with open(ID_PATH, 'w' if data is None else 'r') as f:
            if data is None:
                return f.read().rstrip()
            f.write(data)
    except IOError:
        return

def get_installation_id():
    inst_id = read_or_write_id()
    if inst_id:
        return inst_id
    try:
        inst_id = subprocess.check_output(['dbus-uuidgen', '--get'])
        inst_id = inst_id.rstrip()
        read_or_write_id(inst_id)
        return inst_id
    except (subprocess.CalledProcessError, OSError):
        return

def get_storage():
    if CEPH:
        return 'CEPH'
    if AWS:
        return 'AWS'
    return 'local'


def get_current_platform():
    if AWS:
        return 'aws'
    return 'generic'


def get_predefined_apps_info():
    apps = [app.name for app in db.session.query(PredefinedApp.name)]
    return {
        'count': len(apps),
        'ids': apps
    }


def get_persistent_volume_info():

    cls = PersistentDiskState
    field = cls.size
    agg_result = db.session.query(
        db.func.avg(field).label('avg'),
        db.func.STDDEV(field).label('std'),
        db.func.min(field).label('min'),
        db.func.max(field).label('max'),
        db.func.count(field).label('count'),
    ).filter(
        cls.end == None,
        field > 0
    ).first()
    return {
        'count': agg_result.count,
        'min-size': agg_result.min,
        'max-size': agg_result.max,
        'avg': agg_result.avg,
        'std': agg_result.std
    }


def get_pods_info():
    total_count = db.session.query(db.func.count(Pod.id)).filter(
        Pod.status != 'deleted'
    ).scalar()
    running_count = db.session.query(db.func.count(PodState.id)).filter(
        PodState.end_time == None
    ).scalar()
    return {
        'running': running_count,
        'total': total_count
    }


def get_ip_info():
    total_hosts = 0
    for pool in db.session.query(IPPool):
        nw = ipaddress.ip_network(pool.network)
        total_hosts += 2 ** (nw.max_prefixlen - nw.prefixlen)
        total_hosts -= len(pool.get_blocked_set(as_int=True))
    used_hosts = db.session.query(db.func.count(PodIP.pod_id)).scalar()
    total_pods = db.session.query(db.func.count(Pod.id)).filter(
        Pod.status != 'deleted'
    ).scalar()
    return {
        'public': total_hosts,
        'public-used': used_hosts,
        'private-used': total_pods - used_hosts
    }


def get_containers_summary(top_number=10):
    all_containers = []
    internal_user_id = db.session.query(User.id).filter(
        User.username == KUBERDOCK_INTERNAL_USER
    ).first().id
    for pod in db.session.query(Pod.config).filter(
            Pod.owner_id != internal_user_id):
        if not pod.config:
            continue
        config = json.loads(pod.config)
        containers = config.get('containers', [])
        all_containers.extend(item['image'] for item in containers)
    counts = Counter(all_containers)
    return [
        {'image': image, 'count': count}
        for image, count in counts.most_common(top_number)
    ]


def collect(patt=re.compile(r"""-.*$""")):
    data = {}
    data['nodes'] = extend_nodes(fetch_nodes())
    for pkg in 'kubernetes-master', 'kuberdock':
        data[patt.sub('', pkg)] = get_version(pkg)
    data['installation-id'] = get_installation_id()
    data['storage'] = get_storage()
    data['users'] = get_users_number()
    data['admins'] = get_users_number(role='Admin')
    data['pods'] = get_pods()
    data['updates'] = get_updates()

    data['platform'] = get_current_platform()
    # TODO: replace with real authentication key when some licensing schema will
    # be implemented.
    data['auth-key'] = 'notimplementedyet'
    if data['nodes']:
        data['docker'] = data['nodes'][0]['docker']
    else:
        data['docker'] = 'unknown'
    data['predefined-apps'] = get_predefined_apps_info()
    data['persistent-volumes'] = get_persistent_volume_info()
    data['pods'] = get_pods_info()
    data['ips'] = get_ip_info()
    data['top-10-containers'] = get_containers_summary()
    return data

def send(data):
    r = requests.post(
        STAT_URL,
        headers={'Content-Type': 'application/json'},
        json=data
    )
    #TODO: process licensing information in server response when some licensing
    # schema will be implemented
    if r.status_code != 200:
        current_app.logger.warn('could not upload stat')
    else:
        try:
            res = r.json()
        except:
            current_app.logger.exception('Invalid answer from cln')
        if not res.get('success'):
            current_app.logger.warn(
                'Failed to upload stat. Answer: {}'.format(res)
            )
