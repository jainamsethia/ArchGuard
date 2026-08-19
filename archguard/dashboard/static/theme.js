// Colour theme: dark by default, overridable per browser and remembered.
//
// This file is loaded from <head> without defer so the stored preference is on
// <html> before the first paint. Deferring it, or moving it to the end of
// <body>, would paint the default dark theme first and then repaint light —
// the flash every theme toggle has to avoid.
(function () {
    var STORAGE_KEY = 'archguard-theme';
    var root = document.documentElement;

    function stored() {
        try {
            return localStorage.getItem(STORAGE_KEY);
        } catch (e) {
            // Private browsing and blocked third-party storage both throw on
            // access. The theme is a preference, not state worth failing over.
            return null;
        }
    }

    function apply(theme) {
        // Dark is the stylesheet's base, so it is expressed by the absence of
        // the attribute rather than by data-theme="dark".
        if (theme === 'light') {
            root.setAttribute('data-theme', 'light');
        } else {
            root.removeAttribute('data-theme');
        }
        syncButtons(theme);
    }

    function syncButtons(theme) {
        var buttons = document.querySelectorAll('.theme-option');
        for (var i = 0; i < buttons.length; i++) {
            var value = buttons[i].getAttribute('data-theme-value');
            buttons[i].setAttribute('aria-pressed', String(value === theme));
        }
    }

    var current = stored() === 'light' ? 'light' : 'dark';
    apply(current);

    function choose(theme) {
        current = theme;
        apply(theme);
        try {
            localStorage.setItem(STORAGE_KEY, theme);
        } catch (e) { /* preference simply will not persist */ }
    }

    function bind() {
        // Re-sync here too: at first paint the buttons did not exist yet, so
        // apply() above could only set the attribute on <html>.
        syncButtons(current);
        document.addEventListener('click', function (event) {
            var button = event.target.closest && event.target.closest('.theme-option');
            if (!button) return;
            choose(button.getAttribute('data-theme-value') === 'light' ? 'light' : 'dark');
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();
