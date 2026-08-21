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
  const whoami = document.getElementById('whoami');

  function showOverlay(message) {
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
    return;
  }

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

async function signOut() {
  try {
    await fetch('/api/v1/auth/logout', { method: 'POST' });
  } finally {
    window.location.href = '/';
  }
}
