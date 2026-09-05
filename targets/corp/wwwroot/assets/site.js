(function () {
    'use strict';

    var CONSENT_KEY = 'portal_consent';

    function ready(fn) {
        if (document.readyState !== 'loading') {
            fn();
        } else {
            document.addEventListener('DOMContentLoaded', fn);
        }
    }

    function initConsent() {
        var banner = document.getElementById('consent');
        if (!banner) {
            return;
        }

        var stored = null;
        try {
            stored = window.localStorage.getItem(CONSENT_KEY);
        } catch (e) {
            stored = null;
        }

        if (!stored) {
            banner.hidden = false;
        }

        var acceptBtn = document.getElementById('consent-accept');
        var manageBtn = document.getElementById('consent-manage');

        if (acceptBtn) {
            acceptBtn.addEventListener('click', function () {
                try {
                    window.localStorage.setItem(CONSENT_KEY, 'accepted');
                } catch (e) {
                    /* storage may be unavailable in private browsing */
                }
                banner.hidden = true;
            });
        }

        if (manageBtn) {
            manageBtn.addEventListener('click', function () {
                window.location.href = '/legal/cookies';
            });
        }
    }

    function initNavToggle() {
        var toggle = document.querySelector('.navtoggle');
        var nav = document.querySelector('.mainnav');
        if (!toggle || !nav) {
            return;
        }

        toggle.addEventListener('click', function () {
            var isOpen = nav.classList.toggle('open');
            toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });
    }

    function markCurrentNavLink() {
        var links = document.querySelectorAll('.mainnav a');
        var here = window.location.pathname;

        links.forEach(function (link) {
            var target = link.getAttribute('href');
            if (target === here || (target !== '/' && here.indexOf(target) === 0)) {
                link.classList.add('active');
            }
        });
    }

    ready(function () {
        initConsent();
        initNavToggle();
        markCurrentNavLink();
    });
})();
