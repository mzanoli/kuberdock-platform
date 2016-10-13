requirejs.config({
    baseUrl: "static/js",
    urlArgs: "bust=" + (new Date()).getTime(),
    paths: {
        jquery        : "lib/jquery",
        underscore    : "lib/underscore-min",
        backbone      : "lib/backbone",
        marionette    : "lib/backbone.marionette.min",
        text          : "lib/text",
        tpl           : "lib/underscore-tpl",
//        validator     : "lib/backbone-validation-amd-min",
        mocha         : 'lib/mocha',
        chai          : 'lib/chai',
        'chai-jquery' : 'lib/chai-jquery',
        sinon         : 'lib/sinon',
        'sinon-chai'  : 'lib/sinon-chai',
        squire        : 'lib/squire',
    },
    shim: {
        jquery       : {exports: "$"},
        underscore   : {exports: "_"},
        backbone     : ["underscore", "jquery"],
        marionette   : ["backbone"],
        tpl          : ["text"],
//        validator    : ["underscore", "jquery", "backbone"],
        'chai-jquery': ['chai', 'jquery'],
        'sinon-chai' : ['chai', 'sinon'],
        mocha        : {
            init: function(){
                if (typeof initMochaPhantomJS === 'function') {
                    initMochaPhantomJS();
                }
                this.mocha.setup('bdd');
                return this.mocha;
            }
        }
    }
});

define(['mocha', 'chai', 'chai-jquery', 'sinon-chai'],
       function(mocha, chai, chaiJquery, sinonChai){
    chai.use(chaiJquery);
    chai.use(sinonChai);
    require(['app_data/tests/specs'], function(){
        (window.mochaPhantomJS || mocha).run();
    })
});