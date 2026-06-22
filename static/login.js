// Login form handler. Inline-script вынесен в файл, чтобы не упираться
// в CSP `script-src 'self'` и не упасть на default-submit (GET с паролем
// в URL — серьёзная утечка в логи и историю браузера).
(function () {
  // Срочный safety: если по какой-то причине username/password оказались
  // в URL (например, кто-то открыл https://host/login?username=...&password=...
  // или старая ссылка), очищаем URL до того, как начнём что-либо делать —
  // иначе пароль остаётся в адресной строке, history, Referer.
  try {
    const u = new URL(window.location.href);
    let dirty = false;
    for (const k of ['username', 'password']) {
      if (u.searchParams.has(k)) { u.searchParams.delete(k); dirty = true; }
    }
    if (dirty) history.replaceState({}, '', u.pathname + (u.search ? u.search : '') + u.hash);
  } catch (_) {}

  const form = document.getElementById('loginForm');
  const errBox = document.getElementById('loginError');
  const submit = document.getElementById('loginSubmit');

  function showError(msg) {
    errBox.textContent = msg;
    errBox.classList.add('visible');
  }
  function clearError() {
    errBox.classList.remove('visible');
    errBox.textContent = '';
  }

  // Поддержка ?next=/path — после логина возвращаем туда.
  // ВАЖНО: разрешаем только same-origin paths (/foo/bar). Иначе — open redirect:
  // ?next=https://evil.com или ?next=//evil.com → фишинг с легитимным /login.
  const params = new URLSearchParams(window.location.search);
  function safeNext(raw) {
    if (!raw) return '/';
    if (typeof raw !== 'string') return '/';
    if (!raw.startsWith('/')) return '/';
    if (raw.startsWith('//') || raw.startsWith('/\\')) return '/';
    return raw;
  }
  const next = safeNext(params.get('next'));

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearError();
    const linkMode = !!window.__ssoLinkMode;  // привязка с экрана входа (sso=noaccount)
    submit.disabled = true;
    submit.textContent = linkMode ? 'Привязываем…' : 'Вход…';

    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    try {
      const r = await fetch(linkMode ? '/auth/sso-link' : '/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
        credentials: 'same-origin',
      });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        showError(data.detail || `Ошибка ${r.status}`);
        submit.disabled = false;
        submit.textContent = 'Войти';
        return;
      }
      // Успех — редирект на ?next или /
      window.location.href = next;
    } catch (err) {
      showError('Сетевая ошибка: ' + (err.message || err));
      submit.disabled = false;
      submit.textContent = linkMode ? 'Привязать и войти' : 'Войти';
    }
  });
})();
