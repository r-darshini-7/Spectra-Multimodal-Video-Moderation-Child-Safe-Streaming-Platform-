/**
 * auth.js — Account password authentication for login page
 */

document.getElementById('loginForm')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const errorEl = document.getElementById('loginError');
  const email = document.getElementById('loginEmail').value.trim().toLowerCase();
  const password = document.getElementById('loginPassword').value;
  const csrfToken = document.getElementById('loginCsrfToken').value;
  try {
    const res = await fetch('/auth/password-auth', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken},
      body: JSON.stringify({email, password}),
    });
    const data = await res.json();
    if (data.ok) {
      window.location.href = data.redirect;
    } else {
      errorEl.textContent = data.error || 'Invalid email or password.';
      errorEl.classList.remove('d-none');
    }
  } catch {
    errorEl.textContent = 'Connection error. Please try again.';
    errorEl.classList.remove('d-none');
  }
});
