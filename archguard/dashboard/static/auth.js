// archguard/dashboard/static/auth.js — new file
(async function initAuth() {
  const res = await fetch('/api/v1/auth/status');
  if (!res.ok) return;
  const { token_required, authenticated } = await res.json();
  if (token_required && !authenticated) {
    const overlay = document.getElementById('login-overlay');
    overlay.style.display = 'flex';
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
  const res = await fetch('/api/v1/auth/login', { method: 'POST', body: fd });
  if (res.ok) {
    document.getElementById('login-overlay').style.display = 'none';
    document.querySelectorAll('body > *:not(#login-overlay)').forEach(el => el.style.visibility = '');
  } else {
    errEl.textContent = 'Invalid token. Please try again.';
    errEl.style.display = 'block';
  }
}
