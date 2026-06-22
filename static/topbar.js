// Топбар: имя текущего пользователя справа + кнопка «Выйти».
// Загружается на index.html и settings.html. Не зависит от других скриптов.
(function () {
  async function init() {
    const menu = document.getElementById('userMenu');
    const nameEl = document.getElementById('userMenuName');
    const logoutBtn = document.getElementById('logoutBtn');
    if (!menu || !nameEl || !logoutBtn) return;
    let ssoLinked = false;  // SSO-юзер → полный выход из Dodo IS на логауте

    // Тянем профиль. Если 401 — middleware всё равно перебросит на /login,
    // нам беспокоиться не о чем; просто покажем «—».
    try {
      const r = await fetch('/auth/me', { credentials: 'same-origin' });
      if (!r.ok) return;
      const me = await r.json();
      ssoLinked = !!me.dodois_linked;
      // Сохраняем username глобально — используется как ключ для per-user
      // настроек в localStorage (например, выбор пиццерий, S10.2).
      window.__currentUsername = me.username || null;
      window.dispatchEvent(new CustomEvent('user-loaded', { detail: me }));
      nameEl.textContent = me.display_name || me.username || '—';
      // Тонкий индикатор админа — звёздочка перед именем
      if (me.is_admin) nameEl.textContent = '★ ' + nameEl.textContent;
      menu.classList.remove('hidden');
      buildServiceSwitch(me);
    } catch (e) {
      // Сетевая ошибка — оставляем меню скрытым
      console.warn('topbar: /auth/me failed', e);
      return;
    }

    logoutBtn.addEventListener('click', async () => {
      try {
        await fetch('/auth/logout', {
          method: 'POST', credentials: 'same-origin',
        });
      } catch (e) { /* плевать на ошибку — всё равно редиректим */ }
      // SSO-юзер → полный выход из Dodo IS (через sa OIDC end-session);
      // локальный → обычный выход на свой /login.
      window.location.href = ssoLinked
        ? 'https://sa.dodotool.ru/dodois/logout'
        : '/login';
    });
  }

  // Переключатель сервисов в платформенной шапке: Финансы / Пульс / хаб.
  // Пульс показываем только при visibility_level >= 30 (как гейт /board).
  function buildServiceSwitch(me) {
    const chip = document.getElementById('svcChipBtn');
    const menu = document.getElementById('svcMenu');
    if (!chip || !menu) return;

    const HUB = 'https://app.dodotool.ru';
    const vis = (me && typeof me.visibility_level === 'number') ? me.visibility_level : 0;
    const path = location.pathname;
    const current = path.indexOf('/board') === 0 ? 'pulse'
      : (path.indexOf('/settings') === 0 ? 'settings' : 'finance');
    const services = [
      { id: 'finance', name: 'Финансы', url: '/' },
      { id: 'pulse', name: 'Пульс', url: '/board', minVis: 30 },
    ];

    const cur = services.find(s => s.id === current);
    const nameEl = chip.querySelector('.svc-chip-name');
    if (nameEl && cur) nameEl.textContent = cur.name;

    const check = '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">'
      + '<path d="M2.5 7.5L6 11L11.5 3.5" stroke="currentColor" stroke-width="1.6" fill="none" '
      + 'stroke-linecap="round" stroke-linejoin="round"/></svg>';
    let html = '';
    services.forEach(s => {
      if (s.minVis && vis < s.minVis) return;
      const isCur = s.id === current;
      html += '<a class="svc-item' + (isCur ? ' is-current' : '') + '" href="' + s.url
        + '" role="menuitem"><span>' + s.name + '</span>' + (isCur ? check : '') + '</a>';
    });
    html += '<div class="svc-menu-sep"></div>'
      + '<a class="svc-item svc-item-hub" href="' + HUB + '" role="menuitem">Все сервисы ↗</a>';
    menu.innerHTML = html;

    const hint = document.getElementById('svcHint');
    function dismissHint() {
      if (hint) hint.classList.add('hidden');
      try { localStorage.setItem('svcHintDismissed', '1'); } catch (e) {}
    }
    const closeMenu = () => {
      menu.classList.add('hidden');
      chip.setAttribute('aria-expanded', 'false');
    };
    chip.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = menu.classList.contains('hidden');
      menu.classList.toggle('hidden', !willOpen);
      chip.setAttribute('aria-expanded', String(willOpen));
      dismissHint();
    });
    document.addEventListener('click', closeMenu);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

    // Разовая подсказка: куда «переехал» День (только на Финансах, если Пульс доступен).
    const canPulse = vis >= 30;
    if (hint && current === 'finance' && canPulse
        && localStorage.getItem('svcHintDismissed') !== '1') {
      hint.classList.remove('hidden');
    }
    const hintClose = document.getElementById('svcHintClose');
    if (hintClose) hintClose.addEventListener('click', (e) => { e.stopPropagation(); dismissHint(); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
