/*!
 * Calderwood first-party page-view beacon.
 *
 * Kept dependency-free and on our own origin so that the console works behind
 * customer proxies that block third-party analytics domains. It records nothing
 * but the path, the referrer and coarse viewport buckets, and it stays silent
 * until the operator has accepted the analytics cookie.
 */
(function (window, document) {
  'use strict';

  var script = document.currentScript;
  var site = (script && script.getAttribute('data-site')) || 'meridian';
  var ENDPOINT = '/api/client/metrics';
  var CONSENT_KEY = 'mrd.consent.analytics';
  var lastPath = null;

  function consented() {
    try {
      return window.localStorage.getItem(CONSENT_KEY) === 'granted';
    } catch (err) {
      return false;
    }
  }

  function viewportBucket() {
    var w = window.innerWidth || 0;
    if (w < 760) return 'sm';
    if (w < 1280) return 'md';
    if (w < 1800) return 'lg';
    return 'xl';
  }

  function send(payload) {
    var body = JSON.stringify(payload);
    if (navigator.sendBeacon) {
      navigator.sendBeacon(ENDPOINT, new Blob([body], { type: 'application/json' }));
      return;
    }
    try {
      var req = new XMLHttpRequest();
      req.open('POST', ENDPOINT, true);
      req.setRequestHeader('Content-Type', 'application/json');
      req.send(body);
    } catch (err) {
      /* metrics must never break the console */
    }
  }

  function pageView(reason) {
    if (!consented()) return;
    var path = window.location.pathname + window.location.search;
    if (path === lastPath && reason !== 'reload') return;
    lastPath = path;
    send({
      site: site,
      kind: 'page-view',
      path: path,
      referrer: document.referrer || null,
      viewport: viewportBucket(),
      language: navigator.language || null,
      at: new Date().toISOString()
    });
  }

  // The console is a single-page application, so history navigation is what we
  // actually have to listen to; the initial load only accounts for one view.
  ['pushState', 'replaceState'].forEach(function (name) {
    var original = window.history[name];
    if (typeof original !== 'function') return;
    window.history[name] = function () {
      var result = original.apply(this, arguments);
      window.setTimeout(pageView, 0);
      return result;
    };
  });

  window.addEventListener('popstate', function () {
    pageView('popstate');
  });

  window.addEventListener('mrd:consent-granted', function () {
    pageView('reload');
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      pageView('load');
    });
  } else {
    pageView('load');
  }
})(window, document);
