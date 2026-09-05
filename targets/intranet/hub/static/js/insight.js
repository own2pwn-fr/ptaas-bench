/* Page timing for the staff services dashboard.
 *
 * The service desk is asked "the intranet is slow" once a week and has nothing to
 * answer with, so each page posts how long it took to draw. Nothing about the person
 * is recorded: the screen, the timings and the width of the window.
 */
(function () {
  "use strict";
  var script = document.currentScript;
  var site = (script && script.getAttribute("data-site")) || "staff-services";
  var queue = [];

  function record(name, value) {
    queue.push({ site: site, screen: location.pathname, metric: name, value: Math.round(value) });
  }

  window.addEventListener("load", function () {
    var timing = performance.getEntriesByType("navigation")[0];
    if (timing) {
      record("first_byte", timing.responseStart);
      record("drawn", timing.domContentLoadedEventEnd);
    }
    record("width", window.innerWidth);
  });

  document.addEventListener("htmx:afterOnLoad", function (event) {
    if (event.detail && event.detail.requestConfig) {
      record("fragment", performance.now());
    }
  });

  window.addEventListener("pagehide", function () {
    if (!queue.length || !navigator.sendBeacon) { return; }
    try {
      navigator.sendBeacon("/parts/dashboard/summary?ping=1", JSON.stringify(queue));
    } catch (err) { /* a page that is going away does not report an error to anyone */ }
    queue = [];
  });
})();
