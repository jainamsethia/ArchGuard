/**
 * The sign-in / sign-out control, on both pages.
 *
 * auth.js had a `signOut()` function with no caller anywhere in the product.
 * Nothing rendered a control, nothing bound a handler, and the dashboard had
 * no account UI at all -- so the only way to stop being signed in was to wait
 * out the 24-hour session or clear cookies by hand, while the privacy page
 * said signing out ends the session immediately.
 *
 * The server side was fine and is covered by tests/integration/test_sign_out.py.
 * These cover the half that was missing: that a control exists, that it is
 * shown to the right people, and that pressing it calls the endpoint that ends
 * the session rather than just dropping the cookie locally.
 *
 * auth.js is a plain script rather than a module -- it runs before anything
 * else so the page never renders signed-in chrome to a signed-out visitor --
 * so it is evaluated here rather than imported.
 */

import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

import { JSDOM, VirtualConsole } from 'jsdom';

const HERE = dirname(fileURLToPath(import.meta.url));
const DASHBOARD = resolve(HERE, '../../archguard/dashboard');

const AUTH_JS = readFileSync(resolve(DASHBOARD, 'static/auth.js'), 'utf8');

/**
 * jsdom seals `window.location`: it is non-configurable, `href` has no
 * redefinable descriptor, and `assign`/`reload` are read-only. So navigation
 * cannot be stubbed, only observed -- setting `href` raises a jsdomError,
 * which is the signal these tests use.
 *
 * That proves the page left, not where it went. Where it went is asserted for
 * real in tests/e2e/sign_out.spec.ts, in a browser that can actually navigate.
 */
function navigationAttempts(virtualConsole) {
  const seen = [];
  virtualConsole.on('jsdomError', (e) => {
    if (/navigation/i.test(e.message)) seen.push(e.message);
  });
  return seen;
}

/** The markup auth.js drives, as both templates carry it. */
const PAGE = `<!doctype html><html><body>
  <div id="login-overlay" hidden></div>
  <div class="account-bar">
    <span id="whoami" class="account-name" hidden></span>
    <button id="sign-out-button" type="button" class="btn-signout" hidden>Sign out</button>
    <button id="sign-in-button" type="button" class="btn-login" hidden>Sign in with GitHub</button>
    <div id="login-error" class="login-error"></div>
  </div>
  <main id="content">protected</main>
</body></html>`;

/**
 * Evaluate auth.js against a page, with /api/v1/auth/status stubbed.
 *
 * Returns the window plus the requests it made, so a test can assert on what
 * was called rather than only on what the DOM ended up looking like -- the
 * difference between signing out and appearing to.
 */
async function loadAuth({ status, html = PAGE } = {}) {
  const virtualConsole = new VirtualConsole();
  const navigations = navigationAttempts(virtualConsole);
  const dom = new JSDOM(html, {
    url: 'http://localhost/dashboard.html',
    runScripts: 'outside-only',
    virtualConsole,
  });
  const { window } = dom;

  const requests = [];
  window.fetch = (url, init) => {
    requests.push({ url: String(url), method: init?.method ?? 'GET' });
    if (String(url).includes('/auth/status')) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(status) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
  };

  window.eval(AUTH_JS);
  // auth.js begins with an async IIFE; let its fetch settle.
  await new Promise((r) => setTimeout(r, 0));

  return { window, document: window.document, requests, navigations };
}

const SIGNED_IN = {
  authenticated: true,
  sign_in_available: true,
  user: { login: 'octocat', avatar_url: null },
};
const SIGNED_OUT = {
  authenticated: false,
  sign_in_available: true,
  sign_in_url: '/auth/github',
};

describe('M-3: signing out', () => {
  it('shows a sign-out control to a signed-in visitor', async () => {
    const { document } = await loadAuth({ status: SIGNED_IN });

    const button = document.getElementById('sign-out-button');
    assert.ok(button, 'there is no sign-out control on the page');
    assert.equal(
      button.hidden,
      false,
      'a signed-in visitor cannot see the sign-out control',
    );
    assert.equal(document.getElementById('whoami').textContent, 'octocat');
  });

  it('does not offer sign-out to someone who is not signed in', async () => {
    const { document } = await loadAuth({ status: SIGNED_OUT });

    assert.equal(document.getElementById('sign-out-button').hidden, true);
    assert.equal(
      document.getElementById('sign-in-button').hidden,
      false,
      'a signed-out visitor should be offered sign-in instead',
    );
  });

  it('calls the endpoint that ends the session, not just the cookie', async () => {
    // The distinction the whole task turns on. Clearing document.cookie in the
    // browser would look identical on screen and leave the session usable by
    // anyone holding the value.
    const { document, requests } = await loadAuth({ status: SIGNED_IN });

    document.getElementById('sign-out-button').click();
    await new Promise((r) => setTimeout(r, 0));

    const logout = requests.find((r) => r.url.includes('/auth/logout'));
    assert.ok(logout, `no request to the logout endpoint: ${JSON.stringify(requests)}`);
    assert.equal(logout.method, 'POST', 'logout must not be a GET -- it changes state');
    assert.match(logout.url, /\/api\/v1\/auth\/logout$/);
  });

  it('leaves the page it was on', async () => {
    const { document, navigations } = await loadAuth({ status: SIGNED_IN });

    document.getElementById('sign-out-button').click();
    await new Promise((r) => setTimeout(r, 5));

    assert.equal(
      navigations.length,
      1,
      'after signing out the browser should leave the protected page, so no ' +
        'rendered content is left on screen from the session that just ended',
    );
  });

  it('leaves even if the logout request fails', async () => {
    // A user who has decided to sign out on a shared machine must not be left
    // sitting on a signed-in page because the network blipped. The session may
    // survive -- that is the server's problem and the cookie is still sent --
    // but the screen must not.
    const virtualConsole = new VirtualConsole();
    const navigations = navigationAttempts(virtualConsole);
    const dom = new JSDOM(PAGE, {
      url: 'http://localhost/dashboard.html',
      runScripts: 'outside-only',
      virtualConsole,
    });
    const { window } = dom;

    window.fetch = (url) =>
      String(url).includes('/auth/status')
        ? Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(SIGNED_IN) })
        : Promise.reject(new Error('network down'));

    window.eval(AUTH_JS);
    await new Promise((r) => setTimeout(r, 0));

    window.document.getElementById('sign-out-button').click();
    await new Promise((r) => setTimeout(r, 5));

    assert.equal(navigations.length, 1, 'a failed logout stranded the user on the page');
  });

  it('reloads when the page is restored from the back button', async () => {
    // Back after signing out restores the rendered page from the bfcache
    // without re-running any script, so a signed-out visitor would be looking
    // at their own dashboard again -- stale, since every fetch behind it now
    // 401s, but still on screen.
    //
    // A full reload rather than just re-asking who the visitor is: re-checking
    // would correct the account bar and leave the analysis on the page
    // underneath it. The point is that nothing from the ended session remains.
    const { window, navigations } = await loadAuth({ status: SIGNED_IN });

    const before = navigations.length;
    window.dispatchEvent(
      new window.PageTransitionEvent('pageshow', { persisted: true }),
    );
    await new Promise((r) => setTimeout(r, 5));

    assert.ok(
      navigations.length > before,
      'a page restored from the back/forward cache kept showing the signed-in ' +
        'view without re-checking anything',
    );
  });

  it('does not reload on an ordinary first load', async () => {
    // pageshow fires on every load, not only on a restore. Reloading when
    // `persisted` is false would put the page in a loop.
    const { window, navigations } = await loadAuth({ status: SIGNED_IN });

    const before = navigations.length;
    window.dispatchEvent(
      new window.PageTransitionEvent('pageshow', { persisted: false }),
    );
    await new Promise((r) => setTimeout(r, 5));

    assert.equal(navigations.length, before, 'a normal load triggered a reload');
  });
});
