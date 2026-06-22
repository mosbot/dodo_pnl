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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
