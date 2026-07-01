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
    const linkMode = !!window.__ssoLinkMode;  // привязка с экрана входа (sso=noaccount/request/nosub)
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
        submit.textContent = linkMode ? 'Привязать и войти' : 'Войти';
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
// глушил инлайн-скрипт → состояние после входа через Dodo IS не рендерилось,
// новый юзер видел обычную форму = «кнопка ничего не делает»).
// UX: у нового сотрудника локального логина/пароля нет — ведём с «Запросить
// доступ», форму логин/пароль прячем под ссылку.
(function () {
  var p = new URLSearchParams(location.search).get('sso');
  if (!p) return;
  var subEl = document.querySelector('.login-sub');
  var formWrap = document.getElementById('loginFormWrap');
  var orEl = document.querySelector('.login-or');
  var ssoLink = document.getElementById('ssoLink');        // «Войти через Dodo IS» (ретрай)
  var toggle = document.getElementById('showPwToggle');    // «Есть логин и пароль? Ввести вручную»
  var submit = document.getElementById('loginSubmit');
  var rq = document.getElementById('ssoRequestBtn');
  var rm = document.getElementById('ssoRequestMsg');

  function hideForm() {
    if (formWrap) formWrap.style.display = 'none';
    if (orEl) orEl.style.display = 'none';
    if (ssoLink) ssoLink.style.display = 'none';
  }
  function revealForm() {
    window.__ssoLinkMode = true;
    if (formWrap) formWrap.style.display = 'block';
    if (submit) submit.textContent = 'Привязать и войти';
    if (toggle) toggle.style.display = 'none';
    var uname = document.getElementById('username');
    if (uname) uname.focus();
  }
  function showToggle() {
    if (toggle) { toggle.style.display = 'block'; toggle.addEventListener('click', revealForm); }
  }
  function showRetry() {
    if (ssoLink) { ssoLink.style.display = 'block'; ssoLink.textContent = 'Войти через Dodo IS'; }
  }
  function successMsg(text) {
    if (!rm) return;
    rm.style.display = 'block';
    rm.style.background = '#dcfce7';
    rm.style.color = '#166534';
    rm.style.padding = '11px 12px';
    rm.style.borderRadius = '8px';
    rm.style.marginTop = '12px';
    rm.textContent = text;
  }

  // noaccount (legacy): незнакомый sub при выключенном авто-провижне → привязка.
  if (p === 'noaccount') {
    window.__ssoLinkMode = true;
    if (subEl) subEl.textContent = 'Вход через Dodo IS выполнен, но аккаунт ещё не привязан. Есть логин и пароль? Введите — привяжем Dodo IS к вашему аккаунту.';
    if (submit) submit.textContent = 'Привязать и войти';
    if (orEl) orEl.style.display = 'none';
    if (ssoLink) ssoLink.style.display = 'none';
    return;
  }

  // request — сеть уже заведена, нужен доступ у админа; nosub — нет подписки.
  if (p === 'request' || p === 'nosub') {
    hideForm();
    window.__ssoLinkMode = true;  // если раскроют форму — она в режиме привязки
    if (p === 'request') {
      if (subEl) subEl.textContent = 'Ваш аккаунт Dodo IS ещё не подключён к этой сети в Финансах. Запросите доступ у администратора — после подтверждения вы войдёте через Dodo IS.';
      if (rq) {
        rq.style.display = 'block';
        rq.addEventListener('click', function () {
          rq.disabled = true; rq.textContent = 'Отправляем…';
          fetch('/auth/access-request', { method: 'POST', credentials: 'same-origin' })
            .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
              if (res.ok && (res.j.status === 'pending' || res.j.status === 'linked')) {
                rq.style.display = 'none';
                successMsg(res.j.status === 'linked'
                  ? 'Ваш аккаунт уже привязан — просто войдите через Dodo IS.'
                  : 'Запрос отправлен администратору сети. Когда он подтвердит — нажмите «Войти через Dodo IS».');
                showRetry();
              } else {
                rq.disabled = false; rq.textContent = 'Запросить доступ у администратора';
                if (rm) { rm.style.display = 'block'; rm.textContent = (res.j.detail || 'Не удалось отправить запрос.'); }
              }
            })
            .catch(function () { rq.disabled = false; rq.textContent = 'Запросить доступ у администратора'; });
        });
      }
    } else {
      if (subEl) subEl.textContent = 'Для ваших заведений не найдено активной подписки на Финансы. Обратитесь к владельцу сети. Если подписку уже добавили — попробуйте войти снова.';
      showRetry();
    }
    showToggle();
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
