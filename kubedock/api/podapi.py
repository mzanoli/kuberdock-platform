from flask import Blueprint
from flask.views import MethodView
from ..decorators import login_required_or_basic_or_token
from ..decorators import maintenance_protected
from ..utils import KubeUtils, register_api, APIError
from ..kapi.podcollection import PodCollection, PodNotFound
from ..pods.models import Pod
from ..system_settings.models import SystemSettings
from ..validation import check_new_pod_data, check_change_pod_data
from ..rbac import check_permission


podapi = Blueprint('podapi', __name__, url_prefix='/podapi')


class PodsAPI(KubeUtils, MethodView):
    decorators = [KubeUtils.jsonwrap, KubeUtils.pod_start_permissions,
                  login_required_or_basic_or_token]

    @check_permission('get', 'pods')
    def get(self, pod_id):
        user = self._get_current_user()
        return PodCollection(user).get(pod_id, as_json=False)

    @maintenance_protected
    @check_permission('create', 'pods')
    def post(self):
        user = self._get_current_user()
        params = self._get_params()
        check_new_pod_data(params, user)
        return PodCollection(user).add(params)

    @maintenance_protected
    # TODO: uncomment and remove hack in the next sprint
    # @check_permission('edit', 'pods')
    def put(self, pod_id):
        user = self._get_current_user()
        params = self._get_params()
        data = check_change_pod_data(params)

        # yea, it's a dirty-dirty hack, but we don't have a time for smth better
        # TODO: remove it in the next sprint
        db_pod = Pod.query.get(pod_id)
        if db_pod is None:
            raise PodNotFound()
        privileged = False
        if user.is_administrator():
            user = db_pod.owner
            privileged = True  # admin interacts with user's pod
        if (SystemSettings.get_by_name('billing_type').lower() != 'no billing' and
                user.fix_price and not privileged):
            if params.get('command') == 'set':
                # fix-price user is not allowed to change paid/unpaid status
                # and start pod directly, only through billing system
                raise APIError('Direct requests are forbidden for fixed-price users.', 403)
            kubes = db_pod.kubes_detailed
            for container in data.get('containers', []):
                if container.get('kubes') is not None:
                    kubes[container['name']] = container['kubes']
            if params.get('command') == 'redeploy' and db_pod.kubes != sum(kubes.values()):
                # fix-price user is not allowed to upgrade pod
                # directly, only through billing system
                raise APIError('Direct requests are forbidden for fixed-price users.', 403)

        pods = PodCollection(user)
        return pods.update(pod_id, data)
    patch = put

    @maintenance_protected
    @check_permission('delete', 'pods')
    def delete(self, pod_id):
        user = self._get_current_user()
        pods = PodCollection(user)
        return pods.delete(pod_id)
register_api(podapi, PodsAPI, 'podapi', '/', 'pod_id', strict_slashes=False)


@podapi.route('/<pod_id>/<container_name>/update', methods=['GET'],
              strict_slashes=False)
@KubeUtils.jsonwrap
@login_required_or_basic_or_token
@check_permission('get', 'pods')
def check_updates(pod_id, container_name):
    user = KubeUtils._get_current_user()
    return PodCollection(user).check_updates(pod_id, container_name)


@podapi.route('/<pod_id>/<container_name>/update', methods=['POST'],
              strict_slashes=False)
@KubeUtils.jsonwrap
@login_required_or_basic_or_token
@check_permission('get', 'pods')
def update_container(pod_id, container_name):
    user = KubeUtils._get_current_user()
    return PodCollection(user).update_container(pod_id, container_name)
