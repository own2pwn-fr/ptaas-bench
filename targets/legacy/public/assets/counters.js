/* Page counts for the weekly report. It writes to our own server and nowhere else;
   the figures are read out of the access log rather than from a third party. */

(function () {
    if (!window.navigator || !window.navigator.sendBeacon) {
        return;
    }
    try {
        var seen = document.cookie.indexOf('bt_seen=') !== -1;
        if (!seen) {
            document.cookie = 'bt_seen=; path=/; max-age=2592000';
        }
    } catch (e) {
        /* a browser with cookies switched off; nothing to count */
    }
})();
