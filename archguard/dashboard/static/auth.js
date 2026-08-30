// archguard/dashboard/static/auth.js
// Show who is signed in, or offer a way to sign in.
//
// This used to gate the page behind a password field for a single shared
// token. Everyone who typed it got the same session and therefore the same
// access to everything anyone had ever analysed. Sign-in is per account now,
// so the overlay's job is to send the visitor to GitHub, not to collect a
// secret this page has no business handling.
(async function initAuth() {
  const overlay = document.getElementById('login-overlay');
  const errEl = document.getElementById('login-error');
  const signInBtn = document.getElementById('sign-in-button');
  const signOutBtn = document.getElementById('sign-out-button');
  const whoami = document.getElementById('whoami');

  // Only the dashboard is gated. A first-time visitor has to be able to read
  // what the product does before being asked to sign in -- covering the
  // landing page with a sign-in prompt is asking for trust the page has not
  // had a chance to earn yet.
  // Gated pages carry the overlay markup; public ones do not. Keyed off the
  // element rather than a class name so adding a page cannot accidentally gate
  // it by inheriting the wrong body class.
  const gated = overlay !== null;

  function showOverlay(message) {
    if (!gated) {
      // Public page. Surface the button in the page instead of over it.
      if (signInBtn) signInBtn.hidden = false;
      if (message && errEl) {
        errEl.textContent = message;
        errEl.style.display = 'block';
      }
      return;
    }
    if (overlay) {
      overlay.style.display = 'flex';
      // `inert` removes the hidden content from the accessibility tree as well
      // as from the tab order. `visibility: hidden`, which this used, left
      // every control behind the overlay reachable by screen reader and by Tab.
      document.querySelectorAll('body > *:not(#login-overlay)').forEach((el) => {
        el.setAttribute('inert', '');
        el.style.visibility = 'hidden';
      });
    }
    if (message && errEl) {
      errEl.textContent = message;
      errEl.style.display = 'block';
    }
  }

  function hideOverlay() {
    if (overlay) overlay.style.display = 'none';
    document.querySelectorAll('body > *:not(#login-overlay)').forEach((el) => {
      el.removeAttribute('inert');
      el.style.visibility = '';
    });
  }

  let status;
  try {
    const res = await fetch('/api/v1/auth/status');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    status = await res.json();
  } catch (e) {
    // The status endpoint is unreachable, so we cannot tell whether this
    // visitor is signed in. Say so rather than leaving a blank page.
    showOverlay('Could not reach the authentication service. Check your connection and reload.');
    return;
  }

  if (status.authenticated) {
    hideOverlay();
    if (whoami && status.user) {
      whoami.textContent = status.user.login;
      whoami.hidden = false;
    }
    if (signOutBtn) {
      signOutBtn.hidden = false;
      signOutBtn.addEventListener('click', signOut);
    }

    // Back after signing out restores this page from the back/forward cache
    // without re-running any of it, so a signed-out visitor would be looking
    // at their own dashboard again -- stale, since every fetch behind it now
    // 401s, but still on screen. Re-ask on restore.
    window.addEventListener('pageshow', (e) => {
      if (e.persisted) window.location.reload();
    });
    return;
  }

  if (signOutBtn) signOutBtn.hidden = true;

  if (signInBtn) {
    signInBtn.hidden = false;
    // A link, not a fetch: the OAuth flow is a full-page redirect to
    // github.com and back. XHR cannot follow it, and would not carry the
    // cookies GitHub sets.
    signInBtn.addEventListener('click', () => {
      window.location.href = status.sign_in_url || '/auth/github';
    });
  }
  showOverlay(
    status.sign_in_available
      ? ''
      : 'Sign-in is not configured on this instance.'
  );
})();

/**
 * End the session, then leave.
 *
 * POST, not a link: it changes state, and a GET would be followed by every
 * link prefetcher and every crawler that ever saw the URL.
 *
 * The redirect is in `finally` on purpose. Somebody signing out on a shared
 * machine has decided to stop being signed in, and leaving them on a
 * signed-in page because the network blipped is the wrong way to fail. The
 * cookie still goes with the request, so the server ends the session if it
 * ever receives it; what this guarantees is that the screen does not keep
 * showing their data either way.
 */
async function signOut() {
  try {
    await fetch('/api/v1/auth/logout', { method: 'POST' });
  } catch (e) {
    // Caught rather than left to become an unhandled rejection. It is not
    // actionable by the user and the page is leaving anyway; swallowing it
    // silently would be worse, so it goes to the console.
    console.warn('Sign-out request failed; leaving the page anyway.', e);
  } finally {
    window.location.href = '/';
  }
}
