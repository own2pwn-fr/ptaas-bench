/* Insight — first-party usage measurement. Batched, same-origin, no third parties. */
(function () {
  "use strict";
  var tag = document.currentScript || document.querySelector("script[data-site]");
  var site = (tag && tag.getAttribute("data-site")) || location.hostname;
  var ENDPOINT = "/api/client-events";
  var queue = [];
  var timer = null;

  function optedOut() {
    if (navigator.doNotTrack === "1" || window.doNotTrack === "1") return true;
    return /(?:^|;\s*)(?:insight_opt_out|cookie_choice)=(?:1|true|reject)(?:;|$)/.test(
      document.cookie
    );
  }

  function send() {
    timer = null;
    if (!queue.length || optedOut()) {
      queue = [];
      return;
    }
    var body = JSON.stringify({ site: site, sent_at: Date.now(), events: queue.splice(0, 50) });
    if (navigator.sendBeacon) {
      navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" }));
    } else {
      fetch(ENDPOINT, {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: body,
        keepalive: true,
      }).catch(function () {});
    }
  }

  function record(name, props) {
    if (optedOut()) return;
    queue.push({ name: name, path: location.pathname + location.search, props: props || {}, at: Date.now() });
    if (queue.length >= 20) send();
    else if (!timer) timer = setTimeout(send, 4000);
  }

  function view() {
    record("page_view", { title: document.title, referrer: document.referrer || null });
  }

  document.addEventListener("click", function (event) {
    var el = event.target && event.target.closest ? event.target.closest("[data-track]") : null;
    if (el) record("click", { name: el.getAttribute("data-track"), text: (el.textContent || "").trim().slice(0, 80) });
  }, true);

  ["pushState", "replaceState"].forEach(function (method) {
    var original = history[method];
    history[method] = function () {
      var out = original.apply(this, arguments);
      setTimeout(view, 0);
      return out;
    };
  });
  addEventListener("popstate", view);
  addEventListener("pagehide", send);
  window.insight = { record: record };
  view();
})();
