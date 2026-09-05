/* Small things the pages need. Nothing here talks to anything off this site. */

function btDismissCookieNote() {
    var note = document.getElementById('cookie-note');
    if (note) {
        note.style.display = 'none';
    }
    document.cookie = 'bt_cookie_note=1; path=/; max-age=31536000';
}

(function () {
    if (document.cookie.indexOf('bt_cookie_note=1') !== -1) {
        var note = document.getElementById('cookie-note');
        if (note) {
            note.style.display = 'none';
        }
    }
})();

/* The reference box accepts a code with or without the prefix. */
(function () {
    var box = document.getElementById('ref');
    if (!box || !box.form) {
        return;
    }
    box.form.addEventListener('submit', function () {
        var value = box.value.trim().toUpperCase();
        if (value && value.indexOf('BT-') !== 0 && /^[0-9]{4}$/.test(value)) {
            box.value = 'BT-' + value;
        } else {
            box.value = value;
        }
    });
})();
