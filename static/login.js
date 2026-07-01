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

// ── Обработчик ?sso= (перенесён из инлайна login.html: CSP `script-src 'self'`
// глушил инлайн-скрипт → состояние «request»/«nosub» после входа через Dodo IS
// не рендерилось, новый юзер видел обычную форму = «кнопка ничего не делает»).
(function () {
  var p = new URLSearchParams(location.search).get('sso');
  if (!p) return;
  // Вход через Dodo IS выполнен, но аккаунта нет → предлагаем привязать:
  // юзер вводит локальные логин/пароль, форма уходит на /auth/sso-link.
  if (p === 'noaccount') {
    window.__ssoLinkMode = true;
    var sub = document.querySelector('.login-sub');
    if (sub) sub.textContent =
      'Вход через Dodo IS выполнен, но аккаунт ещё не привязан. Есть логин и пароль? Введите их — привяжем Dodo IS к вашему аккаунту.';
    var sb = document.getElementById('loginSubmit');
    if (sb) sb.textContent = 'Привязать и войти';
    var or = document.querySelector('.login-or');
    var sso = document.querySelector('.login-sso');
    if (or) or.style.display = 'none';
    if (sso) sso.style.display = 'none';
    return;
  }
  // Незнакомый Dodo-аккаунт: сеть уже заведена ('request') или нет тенанта
  // ('nosub'). В обоих случаях оставляем форму привязки (вдруг локальный
  // аккаунт уже есть — введите логин/пароль). Для 'request' добавляем кнопку
  // «Запросить доступ» (создаст заявку админу сети).
  if (p === 'request' || p === 'nosub') {
    window.__ssoLinkMode = true;
    var subEl = document.querySelector('.login-sub');
    if (subEl) subEl.textContent = (p === 'request')
      ? 'Вход через Dodo IS выполнен. Есть логин/пароль — введите для привязки. Либо запросите доступ у администратора сети.'
      : 'Вход через Dodo IS выполнен, но подписка на сервис не найдена. Если у вас есть логин/пароль — введите их.';
    var sbtn = document.getElementById('loginSubmit');
    if (sbtn) sbtn.textContent = 'Привязать и войти';
    var or2 = document.querySelector('.login-or');
    var sso2 = document.querySelector('.login-sso');
    if (or2) or2.style.display = 'none';
    if (sso2) sso2.style.display = 'none';
    if (p === 'request') {
      var rb = document.getElementById('ssoRequestBtn');
      var rm = document.getElementById('ssoRequestMsg');
      if (rb) {
        rb.style.display = 'block';
        rb.addEventListener('click', async function () {
          rb.disabled = true; rb.textContent = 'Отправляем…';
          try {
            var r = await fetch('/auth/access-request', { method: 'POST' });
            var j = await r.json().catch(function(){return {};});
            rm.style.display = 'block';
            if (r.ok && (j.status === 'pending' || j.status === 'linked')) {
              rb.style.display = 'none';
              rm.textContent = j.status === 'linked'
                ? 'Ваш аккаунт уже привязан — просто войдите через Dodo IS.'
                : 'Запрос отправлен администратору сети. Дождитесь подтверждения и войдите снова через Dodo IS.';
            } else {
              rb.disabled = false; rb.textContent = 'Запросить доступ у администратора';
              rm.textContent = (j.detail || 'Не удалось отправить запрос.');
            }
          } catch (e) {
            rb.disabled = false; rb.textContent = 'Запросить доступ у администратора';
          }
        });
      }
    }
    return;
  }
  var msg = {
    nolicense: 'У этого аккаунта Dodo IS нет лицензии на Финансы. Обратитесь к администратору.',
    invalid: 'Сессия Dodo IS недействительна — войдите заново.',
    nosession: 'Войдите через Dodo IS.'
  }[p];
  if (msg) {
    var e = document.getElementById('loginError');
    if (e) { e.textContent = msg; e.classList.add('visible'); }
  }
})();
