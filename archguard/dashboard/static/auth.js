// archguard/dashboard/static/auth.js
// Gate the page behind a token when the deployment requires one.
(async function initAuth() {
  const overlay = document.getElementById('login-overlay');
  const errEl = document.getElementById('login-error');

  let status;
  try {
    const res = await fetch('/api/v1/auth/status');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    status = await res.json();
  } catch (e) {
    // Could not reach the auth status endpoint. If the deployment requires a
    // token we have no way to confirm a session, so fall back to the login
    // overlay with an actionable message rather than leaving the page blank.
    if (overlay) {
      overlay.style.display = 'flex';
      document.querySelectorAll('body > *:not(#login-overlay)').forEach(el => el.style.visibility = 'hidden');
    }
    if (errEl) {
      errEl.textContent = 'Could not reach the authentication service. Check your connection and reload.';
      errEl.style.display = 'block';
    }
    const loginForm = document.getElementById('login-form');
    if (loginForm) loginForm.addEventListener('submit', doLogin);
    return;
  }

  const { token_required, authenticated } = status;
  if (token_required && !authenticated) {
    if (overlay) overlay.style.display = 'flex';
    document.querySelectorAll('body > *:not(#login-overlay)').forEach(el => el.style.visibility = 'hidden');
  }

  const loginForm = document.getElementById('login-form');
  if (loginForm) {
    loginForm.addEventListener('submit', doLogin);
  }
})();

async function doLogin(event) {
  event.preventDefault();
  const token = document.getElementById('token-input').value;
  const errEl = document.getElementById('login-error');
  errEl.style.display = 'none';
  const fd = new FormData();
  fd.append('token', token);
  try {
    const res = await fetch('/api/v1/auth/login', { method: 'POST', body: fd });
    if (res.ok) {
      document.getElementById('login-overlay').style.display = 'none';
      document.querySelectorAll('body > *:not(#login-overlay)').forEach(el => el.style.visibility = '');
    } else {
      errEl.textContent = 'Invalid token. Please try again.';
      errEl.style.display = 'block';
    }
  } catch (e) {
    errEl.textContent = 'Could not reach the authentication service. Check your connection and try again.';
    errEl.style.display = 'block';
  }
}
