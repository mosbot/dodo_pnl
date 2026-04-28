// Топбар: имя текущего пользователя справа + кнопка «Выйти».
// Загружается на index.html и settings.html. Не зависит от других скриптов.
(function () {
  async function init() {
    const menu = document.getElementById('userMenu');
    const nameEl = document.getElementById('userMenuName');
    const logoutBtn = document.getElementById('logoutBtn');
    if (!menu || !nameEl || !logoutBtn) return;

    // Тянем профиль. Если 401 — middleware всё равно перебросит на /login,
    // нам беспокоиться не о чем; просто покажем «—».
    try {
      const r = await fetch('/auth/me', { credentials: 'same-origin' });
      if (!r.ok) return;
      const me = await r.json();
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
      window.location.href = '/login';
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
