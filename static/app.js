// P&L Dashboard — client logic.
// Использует месячный пикер: пользователь выбирает один календарный месяц,
// это превращается в [YYYY-MM-01 .. YYYY-MM-<last>] для API.
// Учитывает projects_config — показывает только активные проекты.

const state = {
  projects: [],                 // только активные, которые пришли в /api/pnl.projects
  allProjects: [],              // полный список из /api/projects для сайдбара
  selectedProjects: new Set(),  // ручной фильтр пользователя
  pnl: null,
  revHistory: null,             // 12-месячная история выручки (фоновая)
  loadCounter: 0,               // race-protect для асинхронных загрузок
  charts: {},
  currentMonth: null,
  // S13.1: режим «Месяц» (по умолчанию) или «Период» (диапазон месяцев).
  // В период-режиме скрываем LFL, ops_freshness, ⟳ Метрики, график 12 мес.
  mode: 'month',
  periodFrom: null,  // 'YYYY-MM'
  periodTo: null,    // 'YYYY-MM'
};

const el = (id) => document.getElementById(id);

function toast(msg, kind = '') {
  const t = el('toast');
  t.className = 'toast ' + kind;
  t.textContent = msg;
  setTimeout(() => t.classList.add('hidden'), 2800);
  t.classList.remove('hidden');
}

const fmt = (n, prefix = '') => {
  if (n === null || n === undefined || isNaN(n)) return '<span class="dash">—</span>';
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  return prefix + sign + abs.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
};

const fmtPctAbs = (n) => {
  if (n === null || n === undefined || isNaN(n)) return '—';
  return (n * 100).toFixed(1).replace('.', ',') + '%';
};

const fmtNum = (n, digits = 2) => {
  if (n === null || n === undefined || isNaN(n)) return '—';
  return Number(n).toLocaleString('ru-RU', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
};

// PlanFact возвращает дату как ISO 'YYYY-MM-DD' или ISO datetime
// 'YYYY-MM-DDTHH:MM:SS'. Превращаем в человеческий 'dd.mm.yyyy' для drill-in.
const fmtDateRu = (s) => {
  if (!s) return '';
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : String(s);
};

// --- Month picker ---
function initMonthSelect() {
  const now = new Date();
  const opts = [];
  for (let i = 0; i < 24; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    const label = d.toLocaleDateString('ru-RU', { year: 'numeric', month: 'long' });
    opts.push({ key, label: label.charAt(0).toUpperCase() + label.slice(1) });
  }
  const optsHtml = opts.map(o => `<option value="${o.key}">${o.label}</option>`).join('');
  for (const id of ['monthSelect', 'monthSelectFrom', 'monthSelectTo']) {
    const sel = el(id);
    if (sel) sel.innerHTML = optsHtml;
  }

  // Текущий месяц — дефолт. Период по умолчанию — текущий + предыдущий
  // (с = текущий-1, по = текущий).
  state.currentMonth = opts[0].key;
  el('monthSelect').value = state.currentMonth;
  state.periodTo = opts[0].key;
  state.periodFrom = opts[1] ? opts[1].key : opts[0].key;

  // Восстанавливаем сохранённые значения и режим (per-user, выставляются
  // позже в applyUserPrefs() — после прихода /auth/me).
  el('monthSelectFrom').value = state.periodFrom;
  el('monthSelectTo').value = state.periodTo;
  syncDateRangeFromMode();

  el('monthSelect').addEventListener('change', (e) => {
    state.currentMonth = e.target.value;
    syncDateRangeFromMode();
    loadPnl();
  });
  // В режиме «Период» смена месяца НЕ дёргает бэк сразу — ждём, пока юзер
  // выберет оба конца и нажмёт «Применить» (иначе выбор «с» уже грузил
  // данные, не дождавшись «по»). Селекторы только обновляют state +
  // подсвечивают кнопку как «есть несохранённые изменения».
  el('monthSelectFrom').addEventListener('change', (e) => {
    state.periodFrom = e.target.value;
    if (state.periodFrom > state.periodTo) {
      state.periodTo = state.periodFrom;
      el('monthSelectTo').value = state.periodTo;
    }
    savePeriodRange();
    syncDateRangeFromMode();
    markPeriodDirty();
  });
  el('monthSelectTo').addEventListener('change', (e) => {
    state.periodTo = e.target.value;
    if (state.periodTo < state.periodFrom) {
      state.periodFrom = state.periodTo;
      el('monthSelectFrom').value = state.periodFrom;
    }
    savePeriodRange();
    syncDateRangeFromMode();
    markPeriodDirty();
  });
  el('periodApplyBtn')?.addEventListener('click', () => {
    el('periodApplyBtn').classList.remove('is-dirty');
    loadPnl();
  });

  // Toggle День / Месяц / Период
  document.querySelectorAll('#periodModeToggle .mode-toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const m = btn.dataset.mode;
      // День — навигация на /board (drawer-выбор сохраняется через localStorage)
      if (m === 'day') {
        window.location.href = '/board';
        return;
      }
      if (m === state.mode) return;
      applyMode(m);
      loadPnl();
    });
  });
}

// Per-user persistence для режима и диапазона.
function _modeKey() {
  const u = window.__currentUsername || 'default';
  return `pnlDashboard.periodMode.${u}`;
}
function _periodRangeKey() {
  const u = window.__currentUsername || 'default';
  return `pnlDashboard.periodRange.${u}`;
}
function saveMode() {
  try { localStorage.setItem(_modeKey(), state.mode); } catch {}
}
function savePeriodRange() {
  try {
    localStorage.setItem(_periodRangeKey(),
      JSON.stringify([state.periodFrom, state.periodTo]));
  } catch {}
}
function loadModeAndRangeFromStorage() {
  try {
    // По умолчанию всегда «Месяц». «Период» НЕ запоминается между сессиями
    // — при загрузке / пользователь видит Месяц, даже если в прошлый раз
    // переключал в Период. Чтобы попасть в Период — явный клик на toggle.
    // Исключение: URL ?mode=period — для перехода с /board (тоже не sticky).
    const urlMode = new URLSearchParams(window.location.search).get('mode');
    if (urlMode === 'period') {
      state.mode = 'period';
    } else {
      state.mode = 'month';
    }
    const raw = localStorage.getItem(_periodRangeKey());
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr) && arr.length === 2) {
        state.periodFrom = arr[0];
        state.periodTo = arr[1];
      }
    }
  } catch {}
}

// Применяет режим: переключает кнопки, видимость полей и контролов
// (LFL/⟳ Метрики/freshness/график 12мес скрыты в Период), синхронизирует
// Подсветить кнопку «Применить» — диапазон изменён, но ещё не загружен.
function markPeriodDirty() {
  el('periodApplyBtn')?.classList.add('is-dirty');
}

// dateStart/dateEnd. Не вызывает loadPnl — это решение caller'а.
function applyMode(mode) {
  state.mode = mode;
  saveMode();
  // Переключаем визуальное состояние кнопок toggle
  document.querySelectorAll('#periodModeToggle .mode-toggle-btn').forEach(b => {
    const active = b.dataset.mode === mode;
    b.classList.toggle('is-active', active);
    b.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  // Поля периода
  el('monthFieldSingle')?.classList.toggle('hidden', mode === 'period');
  el('monthFieldFrom')?.classList.toggle('hidden', mode !== 'period');
  el('monthFieldTo')?.classList.toggle('hidden', mode !== 'period');
  // Кнопка «Применить» — только в режиме Период. При входе сбрасываем dirty.
  const applyBtn = el('periodApplyBtn');
  if (applyBtn) {
    applyBtn.classList.toggle('hidden', mode !== 'period');
    applyBtn.classList.remove('is-dirty');
  }
  // В Период: прячем LFL toggle, freshness, ⟳ Метрики, график 12мес.
  // Они все привязаны к концепции «один месяц».
  el('lflToggleWrap')?.classList.toggle('hidden', mode === 'period');
  if (mode === 'period') {
    // Безусловно скрываем — renderOpsFreshness не должен их открывать.
    el('opsRefreshBtn')?.classList.add('hidden');
    el('opsFreshness')?.classList.add('hidden');
    // График 12мес — тоже скрываем.
    const histBox = document.querySelector('[data-chart-id="revHistory12m"]');
    if (histBox) histBox.style.display = 'none';
  } else {
    // График вернётся согласно chartsHidden пользователя (applyChartsVisibility).
    applyChartsVisibility?.();
    // freshness/refresh-кнопку пересчитает renderOpsFreshness.
  }
  syncDateRangeFromMode();
}

function syncDateRangeFromMode() {
  if (state.mode === 'period') {
    const [fs] = monthToRange(state.periodFrom);
    const [, te] = monthToRange(state.periodTo);
    el('dateStart').value = fs;
    el('dateEnd').value = te;
    el('periodMonth').value = '';  // backend поймёт что multi-month
  } else {
    syncPeriodFromMonth();
  }
}

function syncPeriodFromMonth() {
  // YYYY-MM → YYYY-MM-01 / YYYY-MM-<last day>
  const [y, m] = state.currentMonth.split('-').map(Number);
  const last = new Date(y, m, 0).getDate();
  el('dateStart').value = `${state.currentMonth}-01`;
  el('dateEnd').value = `${state.currentMonth}-${String(last).padStart(2, '0')}`;
  el('periodMonth').value = state.currentMonth;
}

function previousYearKey(key) {
  // Тот же месяц прошлого года (LFL)
  const [y, m] = key.split('-').map(Number);
  return `${y - 1}-${String(m).padStart(2, '0')}`;
}

function monthLabel(key) {
  const [y, m] = key.split('-').map(Number);
  const d = new Date(y, m - 1, 1);
  const label = d.toLocaleDateString('ru-RU', { year: 'numeric', month: 'short' });
  return label.replace('.', '');
}

function monthToRange(key) {
  const [y, m] = key.split('-').map(Number);
  const last = new Date(y, m, 0).getDate();
  return [`${key}-01`, `${key}-${String(last).padStart(2, '0')}`];
}

// --- API ---
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (r.status === 401) {
    // Сессия истекла или отсутствует — редирект на /login с возвратом на текущий URL
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = '/login?next=' + next;
    // Возвращаем неразрешающийся promise чтобы остановить дальнейший код
    return new Promise(() => {});
  }
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status}: ${text}`);
  }
  return r.json();
}

async function loadProjects() {
  const { projects } = await api('/api/projects');
  state.allProjects = (projects || []).map(p => ({
    id: String(p.id),
    name: p.name || p.planfact_name || String(p.id),
    is_active: p.is_active !== false,
    // Поля группы из PlanFact — нужны для группировки в сайдбаре.
    project_group_id: p.project_group_id ?? null,
    project_group_title: p.project_group_title ?? null,
    project_group_is_undistributed: !!p.project_group_is_undistributed,
  }));
  // S10.2 + S10.3: восстанавливаем выбор пользователя из localStorage.
  // По умолчанию — пусто (юзер сам выбирает что смотреть). У франчайзи
  // с 30+ пиццериями включать всё разом — это десятки секунд PF-запроса
  // на каждом первом открытии. Пусть лучше юзер один раз выберет нужное,
  // дальше его выбор будет помниться.
  const activeIds = state.allProjects.filter(p => p.is_active).map(p => p.id);
  const saved = loadSavedSelection();
  let initial;
  if (saved !== null) {
    const activeSet = new Set(activeIds);
    initial = saved.filter(id => activeSet.has(id));
    // Если все сохранённые проекты пропали (редко) — оставляем пусто,
    // юзер видит подсказку выбрать.
  } else {
    initial = [];
  }
  state.selectedProjects = new Set(initial);
  state.appliedSelection = new Set(initial);
  renderProjectsSidebar();
}

// Per-user ключ — на одном браузере могут логиниться разные юзеры.
// state.user.username проставляется в topbar.js через /auth/me.
function _selectionKey() {
  const u = window.__currentUsername || 'default';
  return `pnlDashboard.selectedProjects.${u}`;
}
function loadSavedSelection() {
  try {
    const raw = localStorage.getItem(_selectionKey());
    if (!raw) return null;
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.map(String) : null;
  } catch { return null; }
}
function saveSelection(set) {
  try {
    localStorage.setItem(_selectionKey(), JSON.stringify([...set]));
  } catch {}
}

// Состояние свёрнутости групп — сохраняем в localStorage чтобы не сбрасывалось
// при перезагрузке. Дефолт — все развёрнуты, кроме «Проекты без группы»
// (служебная PlanFact-группа), которые сворачиваются.
const GROUP_COLLAPSED_KEY = 'pnlDashboard.collapsedGroups';
function loadCollapsedGroups() {
  try {
    const raw = localStorage.getItem(GROUP_COLLAPSED_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch { return new Set(); }
}
function saveCollapsedGroups(s) {
  try { localStorage.setItem(GROUP_COLLAPSED_KEY, JSON.stringify([...s])); } catch {}
}

// Группируем массив проектов по project_group_title. Возвращает массив
// {title, projects[]} в стабильном порядке: «Текущий бизнес» сверху, затем
// остальные по алфавиту, «Проекты без группы» (isUndistributed) в самом низу.
function groupProjects(projects) {
  const buckets = new Map();
  for (const p of projects) {
    const key = p.project_group_title || 'Без группы';
    if (!buckets.has(key)) {
      buckets.set(key, {
        title: key,
        is_undistributed: !!p.project_group_is_undistributed,
        projects: [],
      });
    }
    buckets.get(key).projects.push(p);
  }
  const arr = [...buckets.values()];
  arr.sort((a, b) => {
    // «Проекты без группы» / undistributed — в конец
    if (a.is_undistributed !== b.is_undistributed) return a.is_undistributed ? 1 : -1;
    // «Текущий бизнес» — наверх (если он точно так называется)
    if (a.title === 'Текущий бизнес') return -1;
    if (b.title === 'Текущий бизнес') return 1;
    return a.title.localeCompare(b.title, 'ru');
  });
  return arr;
}

// Сравнение двух Set'ов на равенство (по элементам).
function _setsEqual(a, b) {
  if (!a || !b) return a === b;
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}


function renderProjectsSidebar() {
  const box = el('projectsList');
  const active = state.allProjects.filter(p => p.is_active);
  const collapsed = loadCollapsedGroups();
  const groups = groupProjects(active);

  // appliedSelection — то, что сейчас отрисовано на дашборде. Кнопка
  // «Применить» подтягивает его к selectedProjects и зовёт loadPnl.
  if (state.appliedSelection === undefined) {
    state.appliedSelection = new Set(state.selectedProjects);
  }

  let html = '';
  groups.forEach(g => {
    const isCollapsed = collapsed.has(g.title);
    const onN = g.projects.filter(p => state.selectedProjects.has(p.id)).length;
    const total = g.projects.length;
    const allOn = onN === total;
    const noneOn = onN === 0;
    const indeterminate = !allOn && !noneOn;
    html += `
      <div class="proj-group" data-group="${esc(g.title)}">
        <div class="proj-group-head" data-toggle="${esc(g.title)}">
          <label class="switch js-stop">
            <input type="checkbox" class="js-grp-toggle" data-grp="${esc(g.title)}"
              ${allOn ? 'checked' : ''} ${indeterminate ? 'data-indeterminate="1"' : ''}>
            <span class="slider"></span>
          </label>
          <span class="proj-group-caret">${isCollapsed ? '▸' : '▾'}</span>
          <span class="proj-group-title">${esc(g.title)}</span>
          <span class="proj-group-count">${onN}/${total}</span>
        </div>
        <div class="proj-group-body" ${isCollapsed ? 'hidden' : ''}>
          ${g.projects.map(p => `
            <label class="proj-row">
              <span class="switch">
                <input type="checkbox" data-pid="${p.id}"
                  ${state.selectedProjects.has(p.id) ? 'checked' : ''}>
                <span class="slider"></span>
              </span>
              <span class="proj-name">${esc(p.name)}</span>
            </label>
          `).join('')}
        </div>
      </div>
    `;
  });

  html += `
    <div id="projApplyBar" class="proj-apply-bar">
      <div class="bar-text">Изменения не применены</div>
      <div class="bar-actions">
        <button type="button" class="btn-cancel" id="projApplyReset">Сбросить</button>
        <button type="button" class="btn-apply" id="projApplyBtn">Применить</button>
      </div>
    </div>
    <a href="/settings" class="muted" style="display:block;margin-top:10px;text-align:center;font-size:11px;">
      Настроить проекты →
    </a>
  `;

  box.innerHTML = html;

  // Восстановить indeterminate (атрибутом не выставляется)
  box.querySelectorAll('input[data-indeterminate="1"]').forEach(cb => cb.indeterminate = true);

  refreshApplyBar();

  // Тумблер группы (включить/выключить все проекты группы)
  box.querySelectorAll('input.js-grp-toggle').forEach(cb => {
    // Чтобы клик на тумблер не сворачивал группу
    cb.closest('.js-stop')?.addEventListener('click', e => e.stopPropagation());
    cb.addEventListener('change', () => {
      const grp = cb.dataset.grp;
      const target = cb.checked;
      const grpProj = active.filter(p => (p.project_group_title || 'Без группы') === grp);
      grpProj.forEach(p => {
        if (target) state.selectedProjects.add(p.id);
        else state.selectedProjects.delete(p.id);
      });
      renderProjectsSidebar();
    });
  });

  // Свёртка/разворот по клику на заголовок группы (не по тумблеру)
  box.querySelectorAll('.proj-group-head').forEach(h => {
    h.addEventListener('click', e => {
      if (e.target.closest('.js-stop, input, label')) return;
      const t = h.dataset.toggle;
      const c = loadCollapsedGroups();
      if (c.has(t)) c.delete(t); else c.add(t);
      saveCollapsedGroups(c);
      renderProjectsSidebar();
    });
  });

  // Тумблер отдельного проекта
  box.querySelectorAll('input[data-pid]').forEach(cb => {
    cb.addEventListener('change', () => {
      const pid = cb.dataset.pid;
      if (cb.checked) state.selectedProjects.add(pid);
      else state.selectedProjects.delete(pid);
      renderProjectsSidebar();
    });
  });

  // Применить — подтянуть applied к selected, сохранить выбор в
  // localStorage (S10.2) и загрузить P&L.
  document.getElementById('projApplyBtn')?.addEventListener('click', () => {
    state.appliedSelection = new Set(state.selectedProjects);
    saveSelection(state.selectedProjects);
    refreshApplyBar();
    updateOnboardingHints();
    loadPnl();
    // На мобиле — после «Применить» закрываем drawer, чтобы юзер увидел
    // дашборд. На десктопе drawer-open не выставляется, no-op.
    if (document.body.classList.contains('drawer-open')) {
      document.body.classList.remove('drawer-open');
      const bd = document.getElementById('drawerBackdrop');
      if (bd) bd.hidden = true;
    }
  });
  // Сбросить — откатить selected к applied (отказ от изменений)
  document.getElementById('projApplyReset')?.addEventListener('click', () => {
    state.selectedProjects = new Set(state.appliedSelection);
    renderProjectsSidebar();
  });
}

function refreshApplyBar() {
  const bar = document.getElementById('projApplyBar');
  if (!bar) return;
  const dirty = !_setsEqual(state.selectedProjects, state.appliedSelection);
  bar.classList.toggle('visible', dirty);
}

async function loadPnl() {
  if (state.selectedProjects.size === 0) {
    renderEmptyState();
    destroyCharts();
    showLoading(false);
    return;
  }
  const ds = el('dateStart').value;
  const de = el('dateEnd').value;
  if (!ds || !de) { toast('Укажи месяц', 'error'); return; }

  const params = new URLSearchParams();
  params.set('date_start', ds);
  params.set('date_end', de);
  // period_month отправляем только в режиме «Месяц». В Период бэк увидит
  // отсутствие параметра и не будет применять month-specific логику
  // (cache_history, ops_freshness и т.п.).
  if (state.mode === 'month') {
    params.set('period_month', state.currentMonth);
  } else {
    // S13.2/S13.4: в Период просим помесячный breakdown — детализация
    // по статьям отрисуется с колонками-месяцами.
    params.set('group_by', 'month');
  }
  state.selectedProjects.forEach(p => params.append('project_ids', p));

  // LFL только в режиме «Месяц». В Период LFL мы прячем целиком.
  if (state.mode === 'month' && el('compareToggle').checked) {
    const [ps, pe] = monthToRange(previousYearKey(state.currentMonth));
    params.set('compare_start', ps);
    params.set('compare_end', pe);
    params.set('compare_mode', 'lfl');
  }

  // S10.1 + S9.4: skeleton + прогресс-бар сразу, основной /api/pnl,
  // потом фоновая загрузка /api/revenue-history (она тяжелее, потому
  // что 12 месяцев), отдельным запросом — чтобы основной отчёт
  // отрисовался без задержки. loadId защищает от race-condition:
  // юзер быстро переключил месяц → старый ответ не перезапишет свежий.
  const loadId = ++state.loadCounter;
  state.revHistory = null;
  showLoading(true);
  renderSkeleton();
  showRevHistoryLoading(true);

  try {
    state.pnl = await api('/api/pnl?' + params.toString());
    if (loadId !== state.loadCounter) return;  // юзер уже переключил период
    render();
  } catch (e) {
    toast('Ошибка загрузки: ' + e.message, 'error');
    showLoading(false);
    showRevHistoryLoading(false);
    return;
  } finally {
    // Прогресс-бар гасим только когда основной /api/pnl ответил.
    // 12-месячная история продолжит грузиться в фоне со своим спиннером.
    showLoading(false);
  }

  // Фон: тянем revenue-history, по приходу — только перерисовываем графики.
  // В Период-режиме график 12 мес скрыт, грузить нечего — просто гасим
  // спиннер и рендерим оставшиеся графики (если они видимы).
  if (state.mode === 'period') {
    showRevHistoryLoading(false);
    renderCharts();
    return;
  }
  const histParams = new URLSearchParams();
  histParams.set('anchor', state.currentMonth);
  // 12 месяцев заканчивая текущим (например, май'25..апр'26).
  histParams.set('months', '12');
  state.selectedProjects.forEach(p => histParams.append('project_ids', p));
  // LY-разбивка только при включённом LFL — иначе график выручки показывает
  // одиночный стек без парных баров и YoY-аннотаций.
  if (el('compareToggle').checked) histParams.set('include_ly', 'true');

  try {
    const hist = await api('/api/revenue-history?' + histParams.toString());
    if (loadId !== state.loadCounter) return;
    state.revHistory = hist;
  } catch {
    // fail-open: 12-месячный график просто останется пустым.
    if (loadId !== state.loadCounter) return;
    state.revHistory = null;
  }
  showRevHistoryLoading(false);
  renderCharts();
}

function showLoading(on) {
  const bar = el('loadingBar');
  if (bar) bar.hidden = !on;
}

// Спиннер на карточке revHistory12m. Не нужен на других — они рендерятся
// сразу из /api/pnl.
function showRevHistoryLoading(on) {
  const box = document.querySelector('[data-chart-id="revHistory12m"]');
  if (!box) return;
  let spinner = box.querySelector('.chart-loading');
  if (on) {
    if (!spinner) {
      spinner = document.createElement('div');
      spinner.className = 'chart-loading';
      spinner.textContent = 'Загрузка истории выручки…';
      box.appendChild(spinner);
    }
  } else if (spinner) {
    spinner.remove();
  }
}

// Модалка увеличенного графика. Переиспускаем config исходного Chart
// (тип/данные/опции) в большом canvas. datasets shallow-клонируем, чтобы
// Chart.js не путал meta двух инстансов с общим config.data.
let modalChart = null;
function openChartModal(chartId) {
  const src = state.charts?.[chartId];
  if (!src || !src.config) return;
  const box = document.querySelector(`.chart-box[data-chart-id="${chartId}"]`);
  el('chartModalTitle').textContent = box?.querySelector('h3')?.textContent || '';
  el('chartModal').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  if (modalChart) { modalChart.destroy(); modalChart = null; }
  const cfg = src.config;
  modalChart = new Chart(el('chartModalCanvas'), {
    type: cfg.type,
    data: {
      labels: cfg.data.labels,
      datasets: (cfg.data.datasets || []).map(d => ({ ...d })),
    },
    options: { ...cfg.options, maintainAspectRatio: false, responsive: true },
  });
}
function closeChartModal() {
  if (modalChart) { modalChart.destroy(); modalChart = null; }
  el('chartModal')?.classList.add('hidden');
  document.body.style.overflow = '';
}

// Дружелюбный empty-state — когда ни одной пиццерии не выбрано.
// 3-шаговая визуальная инструкция с пометкой «выполнен» для первого
// шага (период всегда задан по умолчанию). Параллельно поднимаем
// hint-пилюли в топбаре и сайдбаре через updateOnboardingHints().
function renderEmptyState() {
  const cards = el('kpiCards');
  if (cards) {
    const available = state.allProjects.filter(p => p.is_active).length;
    const periodIsSet = state.mode === 'period'
      ? !!(state.periodFrom && state.periodTo)
      : !!state.currentMonth;
    // Сайдбар проектов теперь всегда off-canvas за бургером ☰ (и на
    // десктопе), а период — в топбаре сверху. Тексты шагов единые для
    // десктопа и мобилы.
    const step1Title = 'Сверху — выбери период';
    const step2Title = 'Открой ☰ — отметь пиццерии';
    cards.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-title">Дашборд P&amp;L · с чего начать</div>
        <div class="empty-state-sub">
          Чтобы увидеть отчёт, нужно выбрать период и хотя бы одну пиццерию.
          ${available ? `В меню ☰ доступно <strong>${available}</strong> проект(ов).` : ''}
        </div>
        <div class="empty-state-steps">
          <div class="empty-state-step ${periodIsSet ? 'is-done' : ''}">
            <span class="empty-state-step-num">1</span>
            <div>
              <div class="empty-state-step-title">${step1Title}</div>
              <div class="empty-state-step-sub">Один месяц или диапазон. По умолчанию — текущий месяц.</div>
            </div>
          </div>
          <div class="empty-state-step">
            <span class="empty-state-step-num">2</span>
            <div>
              <div class="empty-state-step-title">${step2Title}</div>
              <div class="empty-state-step-sub">Можно сразу несколько. Чекбоксы группируются по сети.</div>
            </div>
          </div>
          <div class="empty-state-step">
            <span class="empty-state-step-num">3</span>
            <div>
              <div class="empty-state-step-title">Нажми «Применить»</div>
              <div class="empty-state-step-sub">P&amp;L и метрики появятся через секунду — данные кэшируются.</div>
            </div>
          </div>
        </div>
      </div>
    `;
  }
  const table = el('pnlTable');
  if (table) table.innerHTML = '';
  updateOnboardingHints();
  // На пустом state скрываем тулбары попапов и сами графики/таблицу —
  // они без данных бесполезны и крадут вертикальное место.
  document.body.classList.toggle('is-empty-state', state.selectedProjects.size === 0);
}

// Поднимает/прячет hint-пилюли в топбаре и сайдбаре. Видимость зависит
// от того, есть ли применённый выбор пиццерий (state.selectedProjects).
function updateOnboardingHints() {
  const empty = state.selectedProjects.size === 0;
  const periodHint = el('tbHintPeriod');
  const sbHint = el('sbHintProjects');
  if (periodHint) periodHint.classList.toggle('hidden', !empty);
  if (sbHint) sbHint.classList.toggle('hidden', !empty);
}

// Заполняет блоки карточек и таблицы skeleton-плейсхолдерами
// в форме реального контента. Вызывается перед запросом /api/pnl.
function renderSkeleton() {
  const cards = el('kpiCards');
  if (cards) {
    // Сколько проектов выбрано — столько и карточек-плейсхолдеров.
    const n = Math.max(1, state.selectedProjects.size);
    cards.innerHTML = Array.from({length: n}, () => `
      <div class="card-skel">
        <span class="skel skel-title"></span>
        <span class="skel skel-section"></span>
        <div class="skel-fin">
          <div><span class="skel skel-l"></span><span class="skel skel-v"></span></div>
          <div><span class="skel skel-l"></span><span class="skel skel-v"></span></div>
          <div><span class="skel skel-l"></span><span class="skel skel-v"></span></div>
          <div><span class="skel skel-l"></span><span class="skel skel-v"></span></div>
        </div>
        <span class="skel skel-section"></span>
        <div class="skel-tiles">
          <div><span class="skel skel-l"></span><span class="skel skel-v"></span></div>
          <div><span class="skel skel-l"></span><span class="skel skel-v"></span></div>
          <div><span class="skel skel-l"></span><span class="skel skel-v"></span></div>
          <div><span class="skel skel-l"></span><span class="skel skel-v"></span></div>
        </div>
      </div>
    `).join('');
  }
  const table = el('pnlTable');
  if (table) {
    // 8 пустых строк имитируют будущую детализацию.
    const cols = Math.max(2, state.selectedProjects.size + 2);
    const cells = Array.from({length: cols}, () =>
      '<td><span class="skel-cell" style="width:80%;"></span></td>').join('');
    table.innerHTML = Array.from({length: 8}, () =>
      `<tr class="table-skel-row">${cells}</tr>`).join('');
  }
}

// --- Rendering ---
function render() {
  // Сошли с empty-state — снимаем флаг (CSS прячет тулбары/графики/таблицу
  // когда .is-empty-state на body).
  document.body.classList.remove('is-empty-state');
  renderOpsFreshness();
  // Список метрик в попапе строится из state.pnl — обновляем после
  // каждой загрузки данных (могут добавиться/убраться доступные коды).
  renderMetricsConfigList();
  renderCards();
  renderCharts();
  renderTable();
  updateOnboardingHints();
}

// S3.6: бейдж + кнопка «⟳ Метрики» рядом с пикером периода.
// Логика:
//   - Кнопка скрыта если месяц «заморожен» (полный синк + > N дней с конца).
//   - Бейдж окрашен по возрасту синка:
//       зелёный — < 6ч || (текущий месяц без синка ⟶ красный отдельно),
//       жёлтый  — 6–24ч,
//       красный — > 24ч || never || partial sync.
//   - Заморожен / no_dodois → серый (или скрыт совсем).
function renderOpsFreshness() {
  const btn = el('opsRefreshBtn');
  const badge = el('opsFreshness');
  if (!btn || !badge) return;

  const f = state.pnl?.ops_freshness;
  if (!f || !f.period_end) {
    btn.classList.add('hidden');
    badge.classList.add('hidden');
    return;
  }

  const last = f.last_synced_at ? new Date(f.last_synced_at) : null;
  const now = new Date();
  const ageMs = last ? (now - last) : null;
  const ageH = ageMs != null ? ageMs / 3600000 : null;

  let cls = 'f-gray';
  let text = '';
  let showBtn = !f.is_frozen;

  // S11.9: пока в фоне идёт sync — приоритетно показываем «синхронизация…»,
  // чтобы юзер не недоумевал почему цифры старые. Кнопка disabled (она же
  // вызвала этот синк) — повторный клик ничем не поможет.
  // S12.2: оставляем кнопку видимой (просто disabled) чтобы топбар не
  // прыгал между состояниями.
  if (f.is_syncing) {
    badge.className = 'ops-freshness f-amber syncing';
    badge.textContent = 'синхронизация…';
    badge.classList.remove('hidden');
    btn.classList.remove('hidden');
    btn.disabled = true;
    btn.title = 'Идёт синхронизация…';
    return;
  }

  if (last == null) {
    cls = 'f-red';
    text = 'не синхронизировано';
  } else if (f.is_partial_sync) {
    // Синк был до конца месяца — данные неполные. Показываем дату синка.
    cls = 'f-red';
    text = 'снято ' + last.toLocaleDateString('ru-RU');
  } else if (f.is_frozen) {
    // Полный + >N дней — серый, без кнопки.
    cls = 'f-gray';
    text = 'снято ' + last.toLocaleDateString('ru-RU');
  } else if (f.is_current_month) {
    // Текущий месяц — окрашиваем по возрасту синка.
    if (ageH < 6) {
      cls = 'f-green';
      text = 'обновлено ' + relTimeRu(ageMs);
    } else if (ageH < 24) {
      cls = 'f-amber';
      text = 'обновлено ' + relTimeRu(ageMs);
    } else {
      cls = 'f-red';
      text = 'обновлено ' + relTimeRu(ageMs);
    }
  } else {
    // Прошлый месяц, полный синк, в live-окне (< N дней с конца).
    cls = 'f-green';
    text = 'снято ' + last.toLocaleDateString('ru-RU');
  }

  badge.className = 'ops-freshness ' + cls;
  badge.textContent = text;
  badge.classList.remove('hidden');
  // S12.2: кнопка всегда видима (чтобы топбар не прыгал при переключении
  // месяцев). Теперь она ВСЕГДА enabled — даже для frozen-месяцев: ops sync
  // может ничего не сделать (Dodo IS не отдаёт глубокую историю), но кнопка
  // дополнительно сбрасывает PlanFact-кэш и инвалидирует snapshot
  // cache_history, что нужно когда PF-проводки правят задним числом.
  btn.classList.remove('hidden');
  btn.disabled = false;
  btn.title = f.is_frozen
    ? 'Заморожен. Кнопка перечитает P&L из PlanFact (если правили проводки)'
    : 'Обновить P&L из PlanFact и ops-метрики из Dodo IS';
}

// Авто-обновление /api/pnl пока идёт фоновый ops-синк. Останавливаемся
// либо по таймауту (~3 мин), либо когда is_syncing=false на бэкенде.
let _opsPollTimer = null;
function _pollOpsSync(period) {
  if (_opsPollTimer) clearTimeout(_opsPollTimer);
  const startedAt = Date.now();
  const MAX_MS = 3 * 60 * 1000;
  const STEP_MS = 8000;

  const tick = async () => {
    if (state.currentMonth !== period) return;  // юзер уже сменил месяц
    if (Date.now() - startedAt > MAX_MS) {
      toast('Синхронизация дольше обычного — данные подтянутся при ручном обновлении');
      return;
    }
    try {
      // Лёгкий статус-запрос: НЕ дёргаем /api/pnl (не тянем PlanFact и не
      // перерисовываем всю страницу каждые 8с). Полную перерисовку делаем
      // ОДИН раз — когда синк завершился.
      const r = await fetch(`/api/ops-metrics/sync-status?period=${period}`,
        { credentials: 'same-origin' });
      if (r.ok) {
        const st = await r.json();
        if (!st.is_syncing) {
          if (state.currentMonth !== period) return;
          await loadPnl();           // единственная финальная перерисовка
          toast('Метрики обновлены');
          return;
        }
      }
    } catch { /* ignore */ }
    _opsPollTimer = setTimeout(tick, STEP_MS);
  };
  _opsPollTimer = setTimeout(tick, STEP_MS);
}

// «5 минут назад», «3 ч назад», «2 дня назад»
function relTimeRu(ms) {
  const min = Math.round(ms / 60000);
  if (min < 60) return `${Math.max(1, min)} мин назад`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} ч назад`;
  const d = Math.round(h / 24);
  return `${d} ${d === 1 ? 'день' : (d < 5 ? 'дня' : 'дней')} назад`;
}

function findLine(code) {
  return state.pnl.lines.find(l => l.code === code);
}

// ---- Tile builders ----

// Плитка % от выручки с таргетом (UC/LC/DC/TC). Ceiling: actual <= target = ok.
// При включённом LFL дописываем «· Δ −1,2пп» (percentage points). Для
// cost-ratio меньше = лучше, поэтому отрицательная дельта зелёная.
function pctTile(label, proj, target, opts = {}) {
  const pct = proj?.pct_of_revenue;
  const hasVal = typeof pct === 'number' && !isNaN(pct);
  let stateCls = '';
  if (hasVal && typeof target === 'number' && target > 0) {
    stateCls = pct > target ? 'tile-bad' : 'tile-ok';
  }
  const valueStr = hasVal
    ? (pct * 100).toFixed(1).replace(/\.0$/, '').replace('.', ',')
    : '—';
  // toFixed(1) даёт ≤1 знака после запятой; для целых (UC=32) trailing .0
  // снимаем, чтобы было «32%», а не «32,0%». Для долей <1% (LOSSES=0.6)
  // получается «0,6%», а не округление до 1%.
  const targetStr = (typeof target === 'number' && target > 0)
    ? `цель ${(target * 100).toFixed(1).replace(/\.0$/, '').replace('.', ',')}%`
    : '&nbsp;';
  // LFL-дельта в percentage points (Δпп). Меньше — лучше у cost-ratio.
  // Рендерим отдельной строкой под «цель» — иначе на узкой плитке обрезается.
  let deltaRow = '';
  if (
    typeof pct === 'number'
    && typeof proj?.previous_pct_of_revenue === 'number'
  ) {
    const pp = (pct - proj.previous_pct_of_revenue) * 100;
    const cls = pp <= 0 ? 'pos' : 'neg';
    const sign = pp > 0 ? '+' : (pp < 0 ? '−' : '');
    const ppStr = Math.abs(pp).toFixed(1).replace('.', ',');
    deltaRow = `<div class="tile-hint"><span class="tile-delta ${cls}">Δ ${sign}${ppStr}пп</span></div>`;
  }
  const hint = opts.hint ? ` <span class="tile-sublabel">${esc(opts.hint)}</span>` : '';
  // UX-4: tile-label обрезается ellipsis на узких плитках («ВЫРУЧКА НА Ч…»);
  // даём нативный tooltip с полным текстом.
  const fullLabel = opts.hint ? `${label} ${opts.hint}` : label;
  return `
    <div class="tile tile-metric ${stateCls}" title="${esc(fullLabel)}">
      <div class="tile-label">${esc(label)}${hint}</div>
      <div class="tile-value">${valueStr}<span class="tile-unit">%</span></div>
      <div class="tile-hint">${targetStr}</div>
      ${deltaRow}
    </div>`;
}

// Плитка ops. direction: 'higher' | 'lower'. Если у метрики есть count_field,
// под основным значением рендерим количество в скобках (например процент сертификатов
// + абс. число шт).
function opsTile(meta, val, target, opsRow) {
  const hasVal = val != null && !isNaN(val);
  const dir = meta.direction || 'higher';
  const digits = (typeof meta.digits === 'number') ? meta.digits : 2;
  let stateCls = '';
  if (hasVal && target != null) {
    const ok = dir === 'lower' ? val <= target : val >= target;
    stateCls = ok ? 'tile-ok' : 'tile-bad';
  }
  // S16.2: meta.format='mm_ss' — значение в секундах, на выводе mm:ss.
  // Применяется для AOT и COOK_TIME_{DELIVERY,RESTAURANT}.
  const fmtVal = (v) => {
    if (meta.format === 'mm_ss') {
      const sec = Math.round(Math.abs(v));
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      return `${m}:${String(s).padStart(2, '0')}`;
    }
    return fmtNum(v, digits);
  };
  const valueStr = hasVal ? fmtVal(val) : '—';
  // Абсолютное количество (для сертификатов — N штук). Рендерим инлайн
  // справа от значения. NBSP перед скобкой, чтобы не разъезжалось на 2
  // строки. При overflow JS-auto-fit ужмёт ТОЛЬКО шрифт .tile-sub —
  // основное значение «11,2%» останется того же размера, что у соседей.
  let countStr = '';
  if (meta.count_field && opsRow && opsRow[meta.count_field] != null) {
    countStr = `&nbsp;<span class="tile-sub">(${fmtNum(opsRow[meta.count_field], 0)})</span>`;
  }
  // Единицу с слешами («₽/ч», «зак/ч», «шт/ч») заворачиваем в .nb,
  // чтобы браузер не ломал её на «₽/» + «ч» при узкой плитке.
  // NBSP между «цель» и значением + nowrap-обёртка — чтобы вся подпись
  // «цель 3 500 ₽/ч» влезала ровно в одну строку (раньше переносилась
  // на 2 на узких плитках 1280px).
  const targetStr = target != null
    ? `<span class="nb">цель&nbsp;${fmtVal(target)}${meta.unit ? '&nbsp;' + esc(meta.unit) : ''}</span>`
    : '&nbsp;';
  // UX-4: tooltip с полным названием — на узких ops-плитках label обрезается
  // («ЗАКАЗОВ НА КУ…», «ПРОДУКТОВ В Ч…»).
  // Связываем «на» неразрывным пробелом со следующим словом — чтобы
  // «Заказов на курьера» / «Выручка на человека» переносилось как
  // «Заказов» / «на курьера», а не «Заказов» / «на» / «курьера» (3 строки).
  const labelDisplay = esc(meta.label).replace(/ на /g, ' на ');
  return `
    <div class="tile tile-metric ${stateCls}" title="${esc(meta.label)}">
      <div class="tile-label">${labelDisplay}</div>
      <div class="tile-value">${valueStr}<span class="tile-unit">${esc(meta.unit)}</span>${countStr}</div>
      <div class="tile-hint">${targetStr}</div>
    </div>`;
}

// Большая финансовая плитка (выручка, EBITDA и т.п.). При LFL заменяем
// pct-хинт на «Δ +12,5% · LY 3 635 524 ₽» (для rub-метрик больше = лучше).
function finTile(label, proj, opts = {}) {
  const amt = proj?.amount;
  const pct = proj?.pct_of_revenue;
  const hasVal = typeof amt === 'number' && !isNaN(amt);
  const cls = opts.colorize === false ? '' :
    (hasVal && amt < 0 ? 'tile-neg' : (hasVal && amt > 0 ? 'tile-pos' : ''));

  let hintHTML;
  if (proj?.previous_amount != null && typeof proj.amount === 'number') {
    // S12.3: % от отрицательной базы вводит в заблуждение
    // («рост от убытка не есть рост»). Если прошлый период был ≤ 0 —
    // показываем абсолютную дельту в ₽ вместо процента.
    const prevAmt = proj.previous_amount;
    const curAmt = proj.amount;
    const lyStr = `<span class="tile-ly muted">LY ${fmt(prevAmt)} ₽</span>`;
    if (prevAmt <= 0) {
      const absDelta = curAmt - prevAmt;
      const dCls = absDelta >= 0 ? 'pos' : 'neg';
      const sign = absDelta > 0 ? '+' : (absDelta < 0 ? '−' : '');
      const dStr = `<span class="tile-delta ${dCls}">Δ ${sign}${fmt(Math.abs(absDelta))} ₽</span>`;
      hintHTML = `${dStr} · ${lyStr}`;
    } else if (typeof proj.delta_pct === 'number') {
      const d = proj.delta_pct * 100;
      const dCls = d >= 0 ? 'pos' : 'neg';
      const sign = d > 0 ? '+' : (d < 0 ? '−' : '');
      const dStr = `<span class="tile-delta ${dCls}">Δ ${sign}${Math.abs(d).toFixed(1).replace('.', ',')}%</span>`;
      hintHTML = `${dStr} · ${lyStr}`;
    } else {
      hintHTML = lyStr;
    }
  } else if (typeof pct === 'number' && !isNaN(pct)) {
    hintHTML = `${(pct * 100).toFixed(1).replace('.', ',')}% от выручки`;
  } else {
    hintHTML = '&nbsp;';
  }
  return `
    <div class="tile tile-fin ${cls}" title="${esc(label)}">
      <div class="tile-label">${esc(label)}</div>
      <div class="tile-value">${fmt(amt)}<span class="tile-unit">₽</span></div>
      <div class="tile-hint">${opts.hideSub && !proj?.previous_amount ? '&nbsp;' : hintHTML}</div>
    </div>`;
}

function renderCards() {
  const box = el('kpiCards');
  box.innerHTML = '';
  const revenue = findLine('REVENUE');
  // EBITDA нужен для определения цвета рамки карточки (profit/loss)
  // независимо от того, видит ли его юзер.
  const ebitda = findLine('EBITDA');
  if (!revenue) return;

  const defT = state.pnl?.default_targets || {};
  const projT = {};
  (state.pnl?.targets || []).forEach(t => {
    if (t.project_id && t.metric && typeof t.target_pct === 'number') {
      projT[`${t.project_id}|${t.metric}`] = t.target_pct;
    }
  });
  const targetFor = (pid, code) =>
    projT[`${pid}|${code}`] ?? defT[code] ?? null;

  const opsMeta = state.pnl?.ops_metrics_meta || [];
  const opsTargets = state.pnl?.ops_targets || {};
  const opsProjTargets = state.pnl?.ops_project_targets || {};
  // Per-project override > сетевой дефолт. Симметрично targetFor для P&L.
  const opsTargetFor = (pid, code) =>
    opsProjTargets[pid]?.[code] ?? opsTargets[code] ?? null;

  // S8.9: набор плиток на карточке настраивается через pnl_metrics.is_visible
  // + sort_order. format='rub' → фин-блок (большие плитки в ₽), 'pct'/'x' →
  // блок метрик (компактные плитки %). Хинт «от дост.» для DC хардкодим.
  const allMetrics = (state.pnl?.metrics || [])
    .filter(m => m.is_visible !== false)
    .slice();
  // Legacy fallback: MARGIN считается в build_pnl как computed_row, но в
  // seed_metrics его нет (формула зависит от шаблона). Чтобы не потерять
  // плитку у существующих юзеров, добавляем её если такой код не настроен
  // в pnl_metrics. Если юзер заведёт MARGIN-метрику с is_visible=false —
  // плитка скроется (его явная воля побеждает legacy-дефолт).
  const LEGACY_FALLBACK = [
    { code: 'MARGIN', label: 'Маржин. прибыль', format: 'rub', sort_order: 50 },
  ];
  LEGACY_FALLBACK.forEach(lv => {
    if (!state.pnl?.metrics?.find(m => m.code === lv.code)) {
      allMetrics.push(lv);
    }
  });
  allMetrics.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
  // Per-user фильтр: пользователь может скрыть метрики через попап
  // «⚙ Метрики». Хидден-сет не пускает показывать.
  const metricsHidden = loadMetricsHidden();
  // Per-user порядок: применяется ВНУТРИ группы (fin/pct/ops). То есть
  // юзер не может «впихнуть» Сертификаты между Выручкой и EBITDA, что
  // визуально странно, но может переставить плитки внутри своего блока.
  const userOrder = loadMetricsOrder();
  const finMetrics = applyUserOrder(
    allMetrics.filter(m => m.format === 'rub'),
    userOrder,
  ).filter(m => !metricsHidden.has(m.code));
  const pctMetrics = applyUserOrder(
    allMetrics.filter(m => m.format !== 'rub'),
    userOrder,
  ).filter(m => !metricsHidden.has(m.code));

  state.pnl.projects.forEach(p => {
    const rev = revenue.projects[p.id]?.amount || 0;
    const ebP = ebitda?.projects[p.id] || {};
    const ops = p.ops || {};

    let cls = '';
    if (rev > 0) cls = (ebP.amount ?? 0) >= 0 ? 'profit' : 'loss';

    // Финансовый блок: визуально первая плитка — Выручка (большая, без
    // подзаголовка %). Остальные format='rub' метрики после неё с %.
    // Если юзер скрыл REVENUE из настроек, она просто не попадает сюда.
    const finTiles = finMetrics.map(m => {
      const ln = findLine(m.code);
      if (!ln) return '';   // строка фильтруется backend'ом по visibility
      const isRevenue = m.code === 'REVENUE';
      return finTile(
        m.label,
        ln.projects[p.id],
        isRevenue ? { colorize: false, hideSub: true } : {},
      );
    }).filter(Boolean).join('');

    // Блок метрик: format=pct/x → плитка %, плюс ops-плитки в конце.
    const pctTiles = pctMetrics.map(m => {
      const ln = findLine(m.code);
      if (!ln) return '';
      const opts = m.code === 'DC' ? { hint: 'от дост.' } : {};
      return pctTile(m.label, ln.projects[p.id], targetFor(p.id, m.code), opts);
    }).filter(Boolean).join('');

    // S13.3: ops-метрики берутся из Dodo IS per period_month и не имеют
    // смысла за многомесячный период — прячем плитки целиком в Период-режиме.
    // Также применяем per-user фильтр + порядок (попап «⚙ Метрики»).
    // S16.5: добавляем виртуальные sep-items (__SEP_*__) из userOrder и
    // рендерим их как div.tile-row-break — переносит следующую плитку
    // на новую строку через grid-column: 1/-1.
    const opsItems = (state.mode === 'period') ? [] : (() => {
      const seps = userOrder
        .filter(c => c.startsWith('__SEP_'))
        .map(c => ({ code: c, isSep: true }));
      return applyUserOrder([...opsMeta, ...seps], userOrder)
        .filter(om => om.isSep || !metricsHidden.has(om.code));
    })();
    const opsTiles = opsItems.map(om =>
      om.isSep
        ? '<div class="tile-row-break" aria-hidden="true"></div>'
        : opsTile(om, ops[om.field], opsTargetFor(p.id, om.code), ops)
    ).join('');

    const metricTiles = pctTiles + opsTiles;

    const div = document.createElement('div');
    div.className = 'card ' + cls;
    div.innerHTML = `
      <div class="card-title">${esc(p.name)}</div>
      <div class="card-block">
        <div class="card-block-head">Финансовые показатели</div>
        <div class="tile-grid tile-grid-fin">${finTiles}</div>
      </div>
      <div class="card-block">
        <div class="card-block-head">Метрики</div>
        <div class="tile-grid tile-grid-metrics">${metricTiles}</div>
      </div>
    `;
    box.appendChild(div);
  });

  // Auto-fit плиток после рендера. Стратегии:
  // - .tile-value: если в нём есть .tile-sub (например «11,2 % (309)»),
  //   ужимаем ТОЛЬКО шрифт .tile-sub, чтобы основное значение оставалось
  //   того же размера, что у соседей. Если .tile-sub нет — ужимаем
  //   значение целиком (запасной случай для очень длинных значений).
  // - .tile-hint: ужимаем целиком, минимум 9px.
  // - .tile-label: НЕ ужимаем шрифт. Если не влезает — заменяем
  //   текст на короткий вариант из LABEL_ABBR (см. ниже).
  // requestAnimationFrame — чтобы layout успел применить размеры.
  requestAnimationFrame(() => {
    box.querySelectorAll('.tile-value').forEach(fitTileValue);
    box.querySelectorAll('.tile-hint').forEach(e => fitTextInTile(e, 9));
    box.querySelectorAll('.tile-label').forEach(fitTileLabel);
  });
}

// Сокращения для подписей плиток. Применяются, если оригинал не
// влезает в ширину (используем визуально вместо «…», чтобы значение
// и текст оставались читаемыми, без уменьшения шрифта).
const LABEL_ABBR = {
  'Сертификаты': 'Сер-ты',
};

// Уменьшает font-size элемента, пока scrollWidth > clientWidth.
// minPx — нижняя граница шрифта, чтобы не падало в нечитаемое.
function fitTextInTile(elem, minPx = 9) {
  if (!elem) return;
  // Сбрасываем кастомный размер (на случай повторного рендера).
  elem.style.fontSize = '';
  let size = parseFloat(getComputedStyle(elem).fontSize);
  let guard = 32;  // защита от бесконечного цикла
  while (elem.scrollWidth > elem.clientWidth + 1 && size > minPx && guard-- > 0) {
    size -= 0.5;
    elem.style.fontSize = size + 'px';
  }
}

// Если значение не влезает в плитку и в нём есть .tile-sub
// («11,2 % (309)»), ужимаем шрифт ТОЛЬКО у .tile-sub. Иначе
// ужимаем шрифт всего значения (запасной случай).
function fitTileValue(elem) {
  if (!elem) return;
  const sub = elem.querySelector('.tile-sub');
  // Сбрасываем — на случай повторного рендера.
  elem.style.fontSize = '';
  if (sub) sub.style.fontSize = '';

  if (elem.scrollWidth <= elem.clientWidth + 1) return;  // всё ок

  if (sub) {
    let size = parseFloat(getComputedStyle(sub).fontSize);
    let guard = 24;
    while (elem.scrollWidth > elem.clientWidth + 1 && size > 7 && guard-- > 0) {
      size -= 0.5;
      sub.style.fontSize = size + 'px';
    }
    if (elem.scrollWidth <= elem.clientWidth + 1) return;
  }
  // Sub-only не помог (или sub нет) — fallback: ужимаем значение
  // целиком, минимум 11px чтобы не превратилось в нечитаемое.
  fitTextInTile(elem, 11);
}

// Если подпись не влезает в плитку, пробуем заменить на сокращённый
// вариант из LABEL_ABBR. Шрифт не уменьшаем — иначе значение и
// заголовок выглядели бы разнокалиберно по сравнению с соседями.
function fitTileLabel(elem) {
  if (!elem) return;
  if (elem.scrollWidth <= elem.clientWidth + 1) return;
  const original = elem.textContent.trim();
  const abbr = LABEL_ABBR[original];
  if (abbr) elem.textContent = abbr;
}

function destroyCharts() {
  Object.values(state.charts).forEach(c => c?.destroy?.());
  state.charts = {};
}

// === Каталог графиков ===
// Единственный источник правды для блока графиков. Чтобы добавить новый
// график:
//   1) добавить запись сюда (id уникальный, title — для UI попапа,
//      defaultVisible — показывается ли по умолчанию у нового пользователя);
//   2) добавить <div class="chart-box" data-chart-id="{id}">…</div> в
//      index.html с canvas внутри;
//   3) добавить ветку рендера в renderCharts(), обёрнутую в isChartVisible(id).
// Тогда чекбокс в попапе «⚙ Графики» появится автоматически.
// requires — список pnl_code, без которых график не имеет смысла.
// Если у юзера visibility_level не пускает к этим кодам, бэкенд не
// присылает их в state.pnl.lines → график автоматически прячется
// (см. chartHasAccess + applyChartsVisibility).
const CHARTS = [
  { id: 'revProfit',       title: 'Выручка vs Чистая прибыль',        defaultVisible: true,
    requires: ['REVENUE', 'NET_PROFIT'] },
  { id: 'margins',         title: 'Маржинальность по уровням, %',     defaultVisible: true,
    requires: ['MARGIN', 'EBITDA', 'NET_PROFIT'] },
  { id: 'costShare',       title: 'Структура затрат',                 defaultVisible: true,
    requires: ['UC'] },
  { id: 'revHistory12m',   title: 'Выручка по месяцам · YoY',         defaultVisible: true,
    requires: ['REVENUE'] },
  { id: 'revHistoryLines', title: 'Выручка · линии год к году',       defaultVisible: true,
    requires: ['REVENUE'] },
];

// Проверяем, есть ли у юзера доступ к данным для графика. Бэкенд
// фильтрует state.pnl.lines по visibility_level — если требуемого
// pnl_code нет в lines, значит юзер его не видит.
function chartHasAccess(chartId) {
  const meta = CHARTS.find(c => c.id === chartId);
  if (!meta || !meta.requires || !meta.requires.length) return true;
  const lines = state.pnl?.lines || [];
  if (!lines.length) return true;  // ещё не загружено — не прячем заранее
  const codes = new Set(lines.map(l => l.code));
  return meta.requires.every(c => codes.has(c));
}

// Храним set СКРЫТЫХ id (а не видимых), чтобы при добавлении нового графика
// он автоматически становился видимым у уже состоявшихся пользователей —
// если их выбора скрытия в нём нет, то он показывается.
// Ключ — per-user, как и selectedProjects: на одном браузере могут логиниться
// разные юзеры, и каждому удобно видеть свой набор графиков.
function _chartsHiddenKey() {
  const u = window.__currentUsername || 'default';
  return `pnlDashboard.chartsHidden.${u}`;
}

function loadChartsHidden() {
  try {
    const raw = localStorage.getItem(_chartsHiddenKey());
    return new Set(raw ? JSON.parse(raw) : []);
  } catch { return new Set(); }
}

function saveChartsHidden(set) {
  try { localStorage.setItem(_chartsHiddenKey(), JSON.stringify([...set])); } catch {}
}

// Per-user порядок графиков. Массив id графиков в нужной последовательности.
function _chartsOrderKey() {
  const u = window.__currentUsername || 'default';
  return `pnlDashboard.chartsOrder.${u}`;
}
function loadChartsOrder() {
  try {
    const raw = localStorage.getItem(_chartsOrderKey());
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}
function saveChartsOrder(arr) {
  try { localStorage.setItem(_chartsOrderKey(), JSON.stringify(arr)); } catch {}
}

// Переставляем .chart-box элементы в DOM по сохранённому порядку.
// Не указанные в userOrder — в конце по исходному (CHARTS-каталог) порядку.
function applyChartsOrder() {
  const grid = document.getElementById('chartsGrid');
  if (!grid) return;
  const userOrder = loadChartsOrder();
  if (!userOrder.length) return;
  const known = new Set(CHARTS.map(c => c.id));
  const orderIds = [
    ...userOrder.filter(id => known.has(id)),
    ...CHARTS.map(c => c.id).filter(id => !userOrder.includes(id)),
  ];
  for (const id of orderIds) {
    const box = grid.querySelector(`[data-chart-id="${id}"]`);
    if (box) grid.appendChild(box);  // переносит в конец → итоговый порядок = orderIds
  }
}

function isChartVisible(id) {
  // Дефолт — defaultVisible из каталога. Если пользователь добавил id в
  // hidden-set — скрываем. Также прячем, если у юзера нет доступа
  // к нужным метрикам (visibility_level).
  if (!chartHasAccess(id)) return false;
  const hidden = loadChartsHidden();
  if (hidden.has(id)) return false;
  const meta = CHARTS.find(c => c.id === id);
  return meta ? meta.defaultVisible !== false : true;
}

function applyChartsVisibility() {
  // Скрываем/показываем сами обёртки .chart-box. Делаем это до renderCharts(),
  // чтобы Chart.js не считал размеры на скрытом canvas (иначе он рендерится
  // 0×0 и при показе остаётся пустым).
  // Сначала применяем per-user порядок (DOM reorder), потом видимость.
  applyChartsOrder();
  const hidden = loadChartsHidden();
  for (const c of CHARTS) {
    const box = document.querySelector(`[data-chart-id="${c.id}"]`);
    if (!box) continue;
    // S13.1: график 12 мес имеет смысл только в режиме «Месяц» — в Период
    // его принудительно прячем независимо от пользовательского set'а.
    const forcedHidden = (state.mode === 'period' && (c.id === 'revHistory12m' || c.id === 'revHistoryLines'));
    // Прячем график, если у юзера нет доступа к нужным метрикам
    // (visibility_level не пускает к коду из requires).
    const noAccess = !chartHasAccess(c.id);
    box.style.display = (hidden.has(c.id) || forcedHidden || noAccess) ? 'none' : '';
  }
}

function renderChartsConfigList() {
  const box = el('chartsConfigList');
  if (!box) return;
  const hidden = loadChartsHidden();
  // ВАЖНО: чекбокс не использует data-chart-id (он есть на самих
  // chart-box'ах в #chartsGrid), иначе querySelector('[data-chart-id="X"]')
  // находит чекбокс, а не блок графика — и всё переключение видимости
  // ломается. Используем отдельное имя data-chart-cb.
  // Графики, к которым у юзера нет доступа, в попапе не показываем —
  // нет смысла предлагать включить то, что всё равно скроется.
  // Применяем user-порядок (как в DOM).
  const userOrder = loadChartsOrder();
  const accessible = CHARTS.filter(c => chartHasAccess(c.id));
  const ordered = applyUserOrder(accessible.map(c => ({ ...c, code: c.id })), userOrder);
  box.innerHTML = ordered.map((c, i) => `
    <label class="charts-config-row">
      <input type="checkbox" data-chart-cb="${c.id}" ${hidden.has(c.id) ? '' : 'checked'}>
      <span class="cfg-row-label">${esc(c.title)}</span>
      <span class="cfg-row-arrows">
        <button type="button" class="cfg-arrow" data-chart-up="${c.id}" ${i === 0 ? 'disabled' : ''} title="Выше" aria-label="Выше">▲</button>
        <button type="button" class="cfg-arrow" data-chart-down="${c.id}" ${i === ordered.length - 1 ? 'disabled' : ''} title="Ниже" aria-label="Ниже">▼</button>
      </span>
    </label>
  `).join('');
  box.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const set = loadChartsHidden();
      const id = cb.dataset.chartCb;
      if (cb.checked) set.delete(id);
      else set.add(id);
      saveChartsHidden(set);
      applyChartsVisibility();
      renderCharts();
    });
  });
  box.querySelectorAll('[data-chart-up]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      moveChart(btn.dataset.chartUp, -1);
    });
  });
  box.querySelectorAll('[data-chart-down]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      moveChart(btn.dataset.chartDown, +1);
    });
  });
}

// Сдвинуть график на ±1 позицию.
function moveChart(id, dir) {
  const accessible = CHARTS.filter(c => chartHasAccess(c.id))
    .map(c => ({ ...c, code: c.id }));
  const items = applyUserOrder(accessible, loadChartsOrder());
  const idx = items.findIndex(x => x.id === id);
  const newIdx = idx + dir;
  if (idx < 0 || newIdx < 0 || newIdx >= items.length) return;
  [items[idx], items[newIdx]] = [items[newIdx], items[idx]];
  saveChartsOrder(items.map(x => x.id));
  renderChartsConfigList();
  applyChartsVisibility();
  renderCharts();
}

function setAllChartsVisible(visible) {
  const set = visible ? new Set() : new Set(CHARTS.map(c => c.id));
  saveChartsHidden(set);
  renderChartsConfigList();
  applyChartsVisibility();
  renderCharts();
}

// === Per-user видимость метрик-плиток на карточках ===
// По аналогии с CHARTS — храним сет СКРЫТЫХ кодов в localStorage
// (per-user, как и chartsHidden). При первом заходе всё видно, юзер
// может скрыть лишнее через попап «⚙ Метрики» над карточками.
function _metricsHiddenKey() {
  const u = window.__currentUsername || 'default';
  return `pnlDashboard.metricsHidden.${u}`;
}
function loadMetricsHidden() {
  try {
    const raw = localStorage.getItem(_metricsHiddenKey());
    return new Set(raw ? JSON.parse(raw) : []);
  } catch { return new Set(); }
}
function saveMetricsHidden(set) {
  try { localStorage.setItem(_metricsHiddenKey(), JSON.stringify([...set])); } catch {}
}

// Per-user порядок плиток. Массив кодов в нужной юзеру последовательности.
// Метрики, не указанные в массиве (новые), идут после в глобальном порядке.
function _metricsOrderKey() {
  const u = window.__currentUsername || 'default';
  return `pnlDashboard.metricsOrder.${u}`;
}
function loadMetricsOrder() {
  try {
    const raw = localStorage.getItem(_metricsOrderKey());
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}
function saveMetricsOrder(arr) {
  try { localStorage.setItem(_metricsOrderKey(), JSON.stringify(arr)); } catch {}
}

// Сортирует массив объектов с .code так, чтобы коды из userOrder
// шли первыми в указанной последовательности; остальные — в исходном
// (глобальном) порядке после.
function applyUserOrder(items, userOrder) {
  if (!userOrder || !userOrder.length) return items.slice();
  const pos = new Map(userOrder.map((c, i) => [c, i]));
  const named = items.filter(x => pos.has(x.code))
    .sort((a, b) => pos.get(a.code) - pos.get(b.code));
  const rest = items.filter(x => !pos.has(x.code));
  return [...named, ...rest];
}

// Полный список метрик, которые можно скрывать/показывать.
// Источники: state.pnl.metrics (P&L: UC/LC/DC/MARGIN/EBITDA/...) +
// state.pnl.ops_metrics_meta (ops: ORD_PER_COURIER_H/...).
// Возвращаем массив { code, label, group, isSep? } для рендера попапа.
function buildMetricsCatalog() {
  const out = [];
  const seen = new Set();
  // P&L-метрики (включая фин-блок rub-плиток).
  for (const m of (state.pnl?.metrics || [])) {
    if (m.is_visible === false) continue;  // global off — не настраиваем
    if (seen.has(m.code)) continue;
    seen.add(m.code);
    out.push({ code: m.code, label: m.label || m.code,
               group: m.format === 'rub' ? 'fin' : 'pct' });
  }
  // Legacy MARGIN — добавляется в renderCards если в metrics нет.
  if (!seen.has('MARGIN')) {
    out.push({ code: 'MARGIN', label: 'Маржин. прибыль', group: 'fin' });
    seen.add('MARGIN');
  }
  // Ops-метрики (только если есть в данных — иначе не показываем,
  // чтобы не предлагать «Заказов на курьера» без доступа к Dodo IS).
  for (const om of (state.pnl?.ops_metrics_meta || [])) {
    if (seen.has(om.code)) continue;
    seen.add(om.code);
    out.push({ code: om.code, label: om.label || om.code, group: 'ops' });
  }
  // S16.5: разделители-переносы. Виртуальные item'ы для попапа: они живут
  // только в userOrder (loadMetricsOrder), генерятся как __SEP_<timestamp>__.
  // Группа всегда 'ops' — это блок с auto-fit, где имеет смысл делить ряды.
  const userOrder = loadMetricsOrder();
  for (const code of userOrder) {
    if (code.startsWith('__SEP_') && !seen.has(code)) {
      seen.add(code);
      out.push({ code, label: '', group: 'ops', isSep: true });
    }
  }
  return out;
}

function newSeparatorCode() {
  return '__SEP_' + Date.now().toString(36) + '_' +
    Math.random().toString(36).slice(2, 5) + '__';
}

function renderMetricsConfigList() {
  const box = el('metricsConfigList');
  if (!box) return;
  const hidden = loadMetricsHidden();
  const userOrder = loadMetricsOrder();
  const catalog = buildMetricsCatalog();
  if (!catalog.length) {
    box.innerHTML = '<div class="charts-config-row" style="opacity:0.6">Нет доступных метрик</div>';
    return;
  }
  // Группируем визуально: финансовые → процентные → ops.
  const groupOrder = ['fin', 'pct', 'ops'];
  const groupTitle = { fin: 'Финансовые', pct: 'P&L %', ops: 'Ops' };
  let html = '';
  for (const g of groupOrder) {
    const items = applyUserOrder(catalog.filter(x => x.group === g), userOrder);
    if (!items.length && g !== 'ops') continue;
    html += `<div class="charts-config-head" style="font-size:10px;margin-top:6px">${groupTitle[g]}</div>`;
    html += items.map((m, i) => {
      // S16.5: разделитель — особый pешн в попапе. Не имеет чекбокса,
      // показывается как горизонтальная линия с кнопкой удалить.
      if (m.isSep) {
        return `
          <div class="charts-config-row charts-config-sep" data-group="${g}" data-sep="${m.code}"
               style="display:flex;align-items:center;gap:8px;padding:6px 8px">
            <span style="flex:1;border-top:1px dashed var(--border, #d0d7de);height:0"></span>
            <span class="muted" style="font-size:11px;letter-spacing:0.04em">↵ перенос</span>
            <span style="flex:1;border-top:1px dashed var(--border, #d0d7de);height:0"></span>
            <button type="button" class="cfg-arrow" data-metric-up="${m.code}" ${i === 0 ? 'disabled' : ''} title="Выше">▲</button>
            <button type="button" class="cfg-arrow" data-metric-down="${m.code}" ${i === items.length - 1 ? 'disabled' : ''} title="Ниже">▼</button>
            <button type="button" class="cfg-arrow" data-sep-remove="${m.code}" title="Удалить разделитель" aria-label="Удалить">×</button>
          </div>
        `;
      }
      return `
        <label class="charts-config-row" data-group="${g}">
          <input type="checkbox" data-metric-cb="${m.code}" ${hidden.has(m.code) ? '' : 'checked'}>
          <span class="cfg-row-label">${esc(m.label)}</span>
          <span class="cfg-row-arrows">
            <button type="button" class="cfg-arrow" data-metric-up="${m.code}" ${i === 0 ? 'disabled' : ''} title="Выше" aria-label="Выше">▲</button>
            <button type="button" class="cfg-arrow" data-metric-down="${m.code}" ${i === items.length - 1 ? 'disabled' : ''} title="Ниже" aria-label="Ниже">▼</button>
          </span>
        </label>
      `;
    }).join('');
    // Кнопка «+ Разделитель» только в ops-группе — там auto-fit grid
    // с переменным числом плиток, где перенос имеет смысл.
    if (g === 'ops') {
      html += `
        <div style="padding:6px 8px;text-align:center">
          <button type="button" id="addSepBtn" class="charts-config-btn"
                  title="Добавить разделитель: следующая плитка начнётся с новой строки">
            + разделитель ↵
          </button>
        </div>
      `;
    }
  }
  box.innerHTML = html;
  box.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const set = loadMetricsHidden();
      const code = cb.dataset.metricCb;
      if (cb.checked) set.delete(code);
      else set.add(code);
      saveMetricsHidden(set);
      renderCards();
    });
  });
  // Стрелки ↑↓ — двигают код в пределах его группы.
  box.querySelectorAll('[data-metric-up]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      moveMetric(btn.dataset.metricUp, -1);
    });
  });
  box.querySelectorAll('[data-metric-down]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      moveMetric(btn.dataset.metricDown, +1);
    });
  });
  // S16.5: добавить разделитель — кладём новый __SEP_*__ в конец
  // ops-блока user order.
  const addBtn = document.getElementById('addSepBtn');
  if (addBtn) {
    addBtn.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      const order = loadMetricsOrder().slice();
      order.push(newSeparatorCode());
      saveMetricsOrder(order);
      renderMetricsConfigList();
      renderCards();
    });
  }
  // Удалить разделитель.
  box.querySelectorAll('[data-sep-remove]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      const code = btn.dataset.sepRemove;
      const order = loadMetricsOrder().filter(c => c !== code);
      saveMetricsOrder(order);
      renderMetricsConfigList();
      renderCards();
    });
  });
}

// Сдвинуть метрику на ±1 позицию в её группе.
function moveMetric(code, dir) {
  const catalog = buildMetricsCatalog();
  const meta = catalog.find(x => x.code === code);
  if (!meta) return;
  const group = meta.group;
  // Текущий порядок группы (с учётом user-order и оставшихся в конце).
  const items = applyUserOrder(
    catalog.filter(x => x.group === group),
    loadMetricsOrder(),
  );
  const idx = items.findIndex(x => x.code === code);
  const newIdx = idx + dir;
  if (idx < 0 || newIdx < 0 || newIdx >= items.length) return;
  // Свапаем
  [items[idx], items[newIdx]] = [items[newIdx], items[idx]];
  // Объединяем со всеми остальными группами в новый общий порядок.
  // Берём текущий порядок остальных групп, чтобы не сбросить.
  const groupCodes = new Set(items.map(x => x.code));
  const otherGroups = applyUserOrder(
    catalog.filter(x => !groupCodes.has(x.code)),
    loadMetricsOrder(),
  ).map(x => x.code);
  const newOrder = [...items.map(x => x.code), ...otherGroups];
  saveMetricsOrder(newOrder);
  renderMetricsConfigList();
  renderCards();
}

function setAllMetricsVisible(visible) {
  const catalog = buildMetricsCatalog();
  const set = visible ? new Set() : new Set(catalog.map(m => m.code));
  saveMetricsHidden(set);
  renderMetricsConfigList();
  renderCards();
}

// Утилита: по массиву lines найти строку по коду.
function findIn(lines, code) {
  return lines.find(l => l.code === code);
}

function renderCharts() {
  destroyCharts();
  applyChartsVisibility();
  if (!state.pnl.projects.length) return;
  const labels = state.pnl.projects.map(p => p.name);
  const pids = state.pnl.projects.map(p => p.id);
  const revenue = findLine('REVENUE');
  const net = findLine('NET_PROFIT');
  const margin = findLine('MARGIN');
  const ebitda = findLine('EBITDA');

  // LY-линии (если есть compare)
  const cmp = state.pnl.compare;
  const hasCmp = !!cmp && Array.isArray(cmp.lines);
  const revLY = hasCmp ? findIn(cmp.lines, 'REVENUE') : null;
  const netLY = hasCmp ? findIn(cmp.lines, 'NET_PROFIT') : null;
  const marginLY = hasCmp ? findIn(cmp.lines, 'MARGIN') : null;
  const ebitdaLY = hasCmp ? findIn(cmp.lines, 'EBITDA') : null;

  // Подписи периодов для легенды
  const curLbl = monthLabel(state.currentMonth);
  const lyLbl = hasCmp ? monthLabel(previousYearKey(state.currentMonth)) : '';

  const baseOpts = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { position: 'bottom', labels: { font: { size: 11 } } } },
    scales: {
      x: { grid: { display: false }, ticks: { font: { size: 10 } } },
      y: { grid: { color: '#f1f3f5' }, ticks: { font: { size: 10 }, callback: v => v.toLocaleString('ru-RU') } }
    }
  };

  const pctOpts = { ...baseOpts, scales: { ...baseOpts.scales, y: { ...baseOpts.scales.y, ticks: { ...baseOpts.scales.y.ticks, callback: v => v + '%' } } } };

  // --- График 1: Выручка vs Чистая прибыль ---
  if (isChartVisible('revProfit')) {
    const netAmounts = pids.map(pid => net?.projects[pid]?.amount || 0);
    const revProfitDatasets = [
      { label: `Выручка · ${curLbl}`, data: pids.map(pid => revenue.projects[pid]?.amount || 0), backgroundColor: '#3b82f6', borderRadius: 4 },
      { label: `Чистая прибыль · ${curLbl}`, data: netAmounts, backgroundColor: netAmounts.map(v => v >= 0 ? '#10b981' : '#ef4444'), borderRadius: 4 },
    ];
    if (hasCmp) {
      revProfitDatasets.push(
        { label: `Выручка · ${lyLbl}`, data: pids.map(pid => revLY?.projects[pid]?.amount || 0), backgroundColor: 'rgba(59, 130, 246, 0.35)', borderRadius: 4 },
        { label: `Чистая прибыль · ${lyLbl}`, data: pids.map(pid => netLY?.projects[pid]?.amount || 0), backgroundColor: 'rgba(16, 185, 129, 0.35)', borderRadius: 4 },
      );
    }
    state.charts.revProfit = new Chart(el('revProfit'), {
      type: 'bar',
      data: { labels, datasets: revProfitDatasets },
      options: baseOpts
    });
  }

  // --- График 2: Маржинальность, % ---
  if (isChartVisible('margins')) {
    const marginDatasets = [
      { label: `Маржинальность · ${curLbl}`, data: pids.map(pid => (margin?.projects[pid]?.pct_of_revenue || 0) * 100), backgroundColor: '#60a5fa', borderRadius: 4 },
      { label: `EBITDA · ${curLbl}`, data: pids.map(pid => (ebitda?.projects[pid]?.pct_of_revenue || 0) * 100), backgroundColor: '#34d399', borderRadius: 4 },
      { label: `Net · ${curLbl}`, data: pids.map(pid => (net?.projects[pid]?.pct_of_revenue || 0) * 100), backgroundColor: '#f59e0b', borderRadius: 4 },
    ];
    if (hasCmp) {
      marginDatasets.push(
        { label: `Маржинальность · ${lyLbl}`, data: pids.map(pid => (marginLY?.projects[pid]?.pct_of_revenue || 0) * 100), backgroundColor: 'rgba(96, 165, 250, 0.35)', borderRadius: 4 },
        { label: `EBITDA · ${lyLbl}`, data: pids.map(pid => (ebitdaLY?.projects[pid]?.pct_of_revenue || 0) * 100), backgroundColor: 'rgba(52, 211, 153, 0.35)', borderRadius: 4 },
        { label: `Net · ${lyLbl}`, data: pids.map(pid => (netLY?.projects[pid]?.pct_of_revenue || 0) * 100), backgroundColor: 'rgba(245, 158, 11, 0.35)', borderRadius: 4 },
      );
    }
    state.charts.margins = new Chart(el('margins'), {
      type: 'bar',
      data: { labels, datasets: marginDatasets },
      options: pctOpts
    });
  }

  // --- График 3: Структура затрат (кольцевая, агрегат сети) ---
  // Doughnut по сумме затрат всех выбранных проектов. Раньше был stacked
  // bar по проектам — при нескольких точках бары узкие и нечитаемые.
  // Кольцо показывает доли статей затрат друг от друга; tooltip — ₽ и
  // % от выручки сети. Не зависит от числа выбранных пиццерий.
  if (isChartVisible('costShare')) {
    const costCodes = ['UC','LC','DC','RENT','MARKETING','FRANCHISE','OTHER_OPEX'];
    const colors = ['#6366f1','#8b5cf6','#ec4899','#f97316','#14b8a6','#fb7185','#94a3b8'];
    const revTotal = Math.abs(findLine('REVENUE')?.total?.amount || 0);
    const items = costCodes.map((code, i) => {
      const line = findLine(code);
      return { label: line?.label || code, amt: Math.abs(line?.total?.amount || 0), color: colors[i] };
    }).filter(it => it.amt > 0);  // нулевые статьи в кольцо не выводим

    state.charts.costShare = new Chart(el('costShare'), {
      type: 'doughnut',
      data: {
        labels: items.map(it => it.label),
        datasets: [{
          data: items.map(it => it.amt),
          backgroundColor: items.map(it => it.color),
          borderColor: '#ffffff',
          borderWidth: 1.5,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '58%',
        plugins: {
          legend: {
            position: 'bottom',
            labels: { boxWidth: 12, padding: 8, font: { size: 11 } },
          },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const amt = ctx.parsed || 0;
                const pct = revTotal ? (amt / revTotal * 100) : 0;
                return `${ctx.label}: ${fmt(amt)} ₽ (${pct.toFixed(1).replace('.', ',')}% от выручки)`;
              },
            },
          },
        },
      },
    });
  }

  // --- График 4: Выручка по месяцам · 12 мес ---
  // С LFL: парные стек-бары (текущий + LY) с YoY-аннотациями над парой
  // и расширенным tooltip (% сегмента, итог месяца, YoY канала).
  // Без LFL: одиночный стек по каналам, без процентов.
  if (isChartVisible('revHistory12m') && state.revHistory && state.revHistory.months) {
    const hist = state.revHistory;
    const histLabels = hist.months.map(m => monthLabel(m));
    // Динамический заголовок — отражает режим: при LFL добавляем «· YoY».
    const histTitleEl = document.querySelector('[data-chart-id="revHistory12m"] h3');
    if (histTitleEl) {
      histTitleEl.textContent = (hist.ly && hist.ly.by_channel)
        ? 'Выручка по месяцам · YoY (текущий + год назад)'
        : 'Выручка по месяцам · 12 мес';
    }
    const channelMeta = [
      // Доставка/Ресторан/Самовывоз/Прочее. Для LY используем тот же hue,
      // но более бледный (40% opacity) — так глаз сразу понимает «это год
      // назад», и каналы остаются распознаваемы.
      { key: 'delivery',   label: 'Доставка',  color: '#3b82f6', colorLY: 'rgba(59,130,246,0.42)' },
      { key: 'restaurant', label: 'Ресторан',  color: '#10b981', colorLY: 'rgba(16,185,129,0.42)' },
      { key: 'takeaway',   label: 'Самовывоз', color: '#f59e0b', colorLY: 'rgba(245,158,11,0.42)' },
      { key: 'other',      label: 'Прочее',    color: '#9ca3af', colorLY: 'rgba(156,163,175,0.45)' },
    ];

    const hasLy = !!(hist.ly && hist.ly.by_channel);
    // Скрываем каналы, у которых ноль ВО ВСЕХ месяцах обоих периодов —
    // иначе легенда забита нерелевантными нулями.
    const channelsActive = channelMeta.filter(ch => {
      const curHas = hist.months.some(m => (hist.by_channel?.[m]?.[ch.key] || 0) !== 0);
      const lyHas = hasLy && (hist.ly.months || []).some(
        m => (hist.ly.by_channel?.[m]?.[ch.key] || 0) !== 0
      );
      return curHas || lyHas;
    });

    // Суммы по месяцам — нужны и для tooltip, и для дельта-аннотации.
    const curTotals = hist.months.map(
      m => channelsActive.reduce((s, ch) => s + (hist.by_channel?.[m]?.[ch.key] || 0), 0)
    );
    // LY данные индексируются по своим месяцам (Apr'24..Apr'25, если
    // current — Apr'25..Apr'26). Парим по индексу: hist.months[i] ↔ hist.ly.months[i].
    const lyTotals = hist.months.map((_, i) => {
      if (!hasLy || !hist.ly.months || !hist.ly.months[i]) return 0;
      const lyMonth = hist.ly.months[i];
      return channelsActive.reduce(
        (s, ch) => s + (hist.ly.by_channel?.[lyMonth]?.[ch.key] || 0), 0
      );
    });

    // Build datasets: сначала все каналы текущего (stack='cur'),
    // потом — LY (stack='ly'). Chart.js рисует разные stacks как
    // соседние группы — получаем парные бары.
    const histDatasets = [];
    channelsActive.forEach(ch => {
      histDatasets.push({
        type: 'bar', label: ch.label,
        data: hist.months.map(m => hist.by_channel?.[m]?.[ch.key] || 0),
        backgroundColor: ch.color,
        stack: 'cur', borderRadius: 0,
        // мета — пригодится в tooltip, чтобы не путать cur/ly.
        _channelKey: ch.key, _periodKey: 'cur', _periodLabel: 'текущий',
      });
    });
    if (hasLy) {
      channelsActive.forEach(ch => {
        histDatasets.push({
          type: 'bar', label: ch.label + ' · LY',
          data: hist.months.map((_, i) => {
            const lyMonth = hist.ly.months?.[i];
            return lyMonth ? (hist.ly.by_channel?.[lyMonth]?.[ch.key] || 0) : 0;
          }),
          backgroundColor: ch.colorLY,
          stack: 'ly', borderRadius: 0,
          _channelKey: ch.key, _periodKey: 'ly', _periodLabel: 'год назад',
        });
      });
    }

    // Plugin: над каждой парой пишем общий YoY-процент (компактный бейдж).
    const yoyTotalPlugin = {
      id: 'yoyTotalAnnotation',
      afterDatasetsDraw(chart) {
        if (!hasLy) return;
        const { ctx, scales: { x, y } } = chart;
        ctx.save();
        ctx.font = '500 11px -apple-system, BlinkMacSystemFont, Inter, system-ui, sans-serif';
        ctx.textAlign = 'center';
        for (let i = 0; i < hist.months.length; i++) {
          const cur = curTotals[i], ly = lyTotals[i];
          if (!ly) continue;
          const d = (cur - ly) / Math.abs(ly) * 100;
          const txt = (d >= 0 ? '+' : '−') + Math.abs(d).toFixed(0) + '%';
          ctx.fillStyle = d >= 0 ? '#047857' : '#b91c1c';
          // Метим над более высоким из двух стеков, с отступом 10px.
          const top = Math.max(cur, ly);
          const px = x.getPixelForValue(i);
          const py = y.getPixelForValue(top) - 10;
          ctx.fillText(txt, px, py);
        }
        ctx.restore();
      }
    };

    const fmtRub = v => Number(v).toLocaleString('ru-RU') + ' ₽';

    state.charts.revHistory12m = new Chart(el('revHistory12m'), {
      data: { labels: histLabels, datasets: histDatasets },
      options: {
        ...baseOpts,
        layout: { padding: { top: 22 } },
        scales: {
          ...baseOpts.scales,
          x: { ...baseOpts.scales.x, stacked: true, ticks: { ...baseOpts.scales.x.ticks, autoSkip: false } },
          y: { ...baseOpts.scales.y, stacked: true },
        },
        plugins: {
          ...baseOpts.plugins,
          tooltip: {
            callbacks: {
              // Заголовок tooltip — название месяца (или ЛУ-месяц для LY-сегментов).
              title: (items) => {
                if (!items.length) return '';
                const it = items[0];
                const isLy = it.dataset._periodKey === 'ly';
                const month = isLy
                  ? hist.ly.months[it.dataIndex]
                  : hist.months[it.dataIndex];
                const label = month ? monthLabel(month) : '';
                return label + (isLy ? ' · год назад' : '');
              },
              // Каждая строка — сегмент: название + сумма + % от месяца + YoY дельта самого канала.
              label: (ctx) => {
                const ch = ctx.dataset._channelKey;
                const period = ctx.dataset._periodKey;
                const i = ctx.dataIndex;
                const v = ctx.parsed.y || 0;
                const total = period === 'cur' ? curTotals[i] : lyTotals[i];
                const pct = total > 0 ? (v / total * 100).toFixed(1) : '0';
                let line = `${ctx.dataset.label}: ${fmtRub(v)} (${pct}%)`;
                if (period === 'cur' && hasLy) {
                  const lyMonth = hist.ly.months?.[i];
                  const lyVal = lyMonth ? (hist.ly.by_channel?.[lyMonth]?.[ch] || 0) : 0;
                  if (lyVal > 0) {
                    const d = (v - lyVal) / lyVal * 100;
                    const sign = d >= 0 ? '+' : '−';
                    line += `   YoY ${sign}${Math.abs(d).toFixed(0)}%`;
                  }
                }
                return line;
              },
              // В footer — итог месяца текущего и LY + общий YoY.
              footer: (items) => {
                if (!items.length) return '';
                const i = items[0].dataIndex;
                const cur = curTotals[i], ly = lyTotals[i];
                const lines = [`Итого текущий: ${fmtRub(cur)}`];
                if (hasLy && ly) {
                  lines.push(`Итого год назад: ${fmtRub(ly)}`);
                  const d = (cur - ly) / Math.abs(ly) * 100;
                  lines.push(`Динамика: ${(d >= 0 ? '+' : '−')}${Math.abs(d).toFixed(1)}%`);
                }
                return lines.join('\n');
              },
            },
          },
          legend: {
            position: 'bottom',
            labels: {
              font: { size: 11 },
              // Скрываем LY-дубликаты в легенде — оставляем только базовые
              // 3-4 канала, чтобы не было «Доставка / Доставка · LY / Ресторан / …».
              filter: (item, data) => {
                const ds = data.datasets[item.datasetIndex];
                return ds && ds._periodKey !== 'ly';
              },
            },
          },
        },
      },
      plugins: [yoyTotalPlugin],
    });
  }

  // --- График 5: Выручка · линии год к году (Dodo IS-style) ---
  // Две гладкие линии (текущий / прошлый год), 12 месяцев. Использует те же
  // hist данные, что и revHistory12m: суммируем по каналам, чтобы получить
  // месячный итог. Скрывается в режиме «Период» вместе с revHistory12m.
  if (isChartVisible('revHistoryLines') && state.revHistory && state.revHistory.months) {
    const hist = state.revHistory;
    const histLabels = hist.months.map(m => monthLabel(m));
    const hasLy = !!(hist.ly && hist.ly.by_channel);

    const sumChannels = (byCh) => {
      if (!byCh) return 0;
      return (byCh.delivery || 0) + (byCh.restaurant || 0)
           + (byCh.takeaway || 0) + (byCh.other || 0);
    };
    const curMonthly = hist.months.map(m => sumChannels(hist.by_channel?.[m]));
    const lyMonthly  = hist.months.map((_, i) => {
      if (!hasLy || !hist.ly.months || !hist.ly.months[i]) return null;
      return sumChannels(hist.ly.by_channel?.[hist.ly.months[i]]);
    });

    const fmtRubLine = v => Number(v).toLocaleString('ru-RU');
    // Год берём из 'YYYY-MM' первого месяца. Текущая линия идёт в одном
    // календарном году не всегда (например 12-мес окно май 2025 → апр 2026
    // охватывает 2025 и 2026). Для точечного тултипа берём год именно того
    // месяца, на который наведён курсор.
    const yearOf = (ym) => (ym && /^\d{4}/.test(ym)) ? ym.slice(0, 4) : '';
    const curYears = hist.months.map(yearOf);
    const lyYears  = (hasLy ? (hist.ly.months || []) : []).map(yearOf);

    const lineDatasets = [
      {
        label: 'Текущий год',
        data: curMonthly,
        borderColor: '#60a5fa', backgroundColor: '#60a5fa',
        pointBackgroundColor: '#60a5fa', pointRadius: 4, pointHoverRadius: 6,
        tension: 0.4, borderWidth: 2, fill: false,
        _years: curYears, _periodKey: 'cur',
      },
    ];
    if (hasLy) {
      lineDatasets.push({
        label: 'Прошлый год',
        data: lyMonthly,
        borderColor: '#9b8cc7', backgroundColor: '#9b8cc7',
        pointBackgroundColor: '#9b8cc7', pointRadius: 4, pointHoverRadius: 6,
        tension: 0.4, borderWidth: 2, fill: false,
        _years: lyYears, _periodKey: 'ly',
      });
    }

    state.charts.revHistoryLines = new Chart(el('revHistoryLines'), {
      type: 'line',
      data: { labels: histLabels, datasets: lineDatasets },
      options: {
        ...baseOpts,
        // hover-режим index — показываем в тултипе обе линии при наведении
        // на любую точку месяца (как в Dodo IS).
        interaction: { mode: 'index', intersect: false },
        scales: {
          ...baseOpts.scales,
          x: { ...baseOpts.scales.x, ticks: { ...baseOpts.scales.x.ticks, autoSkip: false } },
          y: { ...baseOpts.scales.y, beginAtZero: true,
               ticks: { ...baseOpts.scales.y.ticks,
                        callback: v => Number(v).toLocaleString('ru-RU') } },
        },
        plugins: {
          ...baseOpts.plugins,
          tooltip: {
            // Цветной квадратик слева от строки — как на скрине Dodo IS.
            usePointStyle: false, boxWidth: 12, boxHeight: 12,
            padding: 10,
            callbacks: {
              // Заголовок — полное русское название месяца (без года), как
              // на референсе Dodo IS. Год показывается в каждой строке.
              title: (items) => {
                if (!items.length) return '';
                const ym = hist.months[items[0].dataIndex] || '';
                const m = parseInt(ym.slice(5, 7), 10);
                const names = ['январь','февраль','март','апрель','май','июнь',
                               'июль','август','сентябрь','октябрь','ноябрь','декабрь'];
                return names[m - 1] || '';
              },
              // Каждая строка: «{год} — {сумма}», без подписи серии.
              label: (ctx) => {
                const v = ctx.parsed.y;
                if (v == null) return null;
                const yr = ctx.dataset._years?.[ctx.dataIndex] || '';
                return `${yr} — ${fmtRubLine(v)}`;
              },
            },
          },
          legend: { position: 'bottom', labels: { font: { size: 12 } } },
        },
      },
    });
  }
}


function renderTable() {
  // Если есть импортированный шаблон ПланФакт — рисуем полное дерево;
  // иначе fallback на 17-строчный агрегат по PNL_CODES.
  const tpl = state.pnl.template_lines;
  if (Array.isArray(tpl) && tpl.length) {
    renderTemplateTable(tpl);
  } else {
    renderAggregateTable();
  }
}

function renderAggregateTable() {
  const table = el('pnlTable');
  const projects = state.pnl.projects;

  let thead = '<thead><tr><th>Статья</th>';
  projects.forEach(p => thead += `<th>${esc(p.name)}</th>`);
  thead += '<th>Итого</th></tr></thead>';

  let tbody = '<tbody>';
  state.pnl.lines.forEach(line => {
    const cls = line.kind === 'summary' ? 'summary-row'
             : line.kind === 'final' ? 'final-row'
             : line.kind === 'header' ? 'lvl-1'
             : 'lvl-' + line.level;
    const drillable = line.kind === 'detail' && line.code !== 'REVENUE';
    tbody += `<tr class="${cls}" data-code="${esc(line.code)}">`;
    tbody += `<td>${esc(line.label)}</td>`;
    let totalAmt = 0;
    projects.forEach(p => {
      const proj = line.projects[p.id] || {};
      const amt = proj.amount || 0;
      totalAmt += amt;
      const pct = proj.pct_of_revenue;
      const delta = proj.delta_pct;
      const valCls = line.kind === 'final' ? (amt < 0 ? 'neg' : 'pos') : (amt < 0 ? 'neg' : '');
      let cell = `<span class="${valCls}">${fmt(amt)}</span>`;
      if (pct !== null && pct !== undefined && line.code !== 'REVENUE' && line.kind === 'detail') {
        cell += `<span class="pct">${fmtPctAbs(pct)}</span>`;
      }
      if (delta !== undefined && delta !== null) {
        const d = delta * 100;
        cell += `<span class="delta ${d < 0 ? 'pos' : 'neg'}">Δ ${d.toFixed(1).replace('.',',')}%</span>`;
      }
      const cellCls = drillable ? 'cell-clickable' : '';
      const cellAttrs = drillable ? ` data-pid="${p.id}" data-pname="${esc(p.name)}"` : '';
      tbody += `<td class="${cellCls}"${cellAttrs}>${cell}</td>`;
    });
    const total = line.total || {};
    let totalCell = `<strong>${fmt(total.amount ?? totalAmt)}</strong>`;
    if (total.pct_of_revenue !== null && total.pct_of_revenue !== undefined && line.code !== 'REVENUE' && line.kind === 'detail') {
      totalCell += `<span class="pct">${fmtPctAbs(total.pct_of_revenue)}</span>`;
    }
    const totalCellCls = drillable ? 'cell-clickable' : '';
    tbody += `<td class="${totalCellCls}" data-pid="" data-pname="Итого">${totalCell}</td>`;
    tbody += '</tr>';
  });
  tbody += '</tbody>';

  table.innerHTML = thead + tbody;

  table.querySelectorAll('td.cell-clickable').forEach(td => {
    td.addEventListener('click', () => {
      const tr = td.closest('tr');
      const code = tr.dataset.code;
      const labelTxt = tr.querySelector('td').textContent;
      // В агрегатной таблице у нас нет точного списка category_ids — открываем
      // drill без category-фильтра (поведение как было до фикса).
      openDrillDown(code, labelTxt, td.dataset.pid || null, td.dataset.pname || '', [], null);
    });
  });
}

// --- Template-tree table ---
// Состояние раскрытия по id узла, хранится в localStorage. Дефолт:
// depth ≤ 1 раскрыты, глубже — свёрнуто. После любых правок пользователя —
// его выбор побеждает.
const TPL_EXPAND_KEY = 'pnlDashboard.tplExpanded';
const TPL_HIDE_ZEROS_KEY = 'pnlDashboard.tplHideZeros';

function loadTplExpanded() {
  try {
    const raw = localStorage.getItem(TPL_EXPAND_KEY);
    if (!raw) return null;
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr : []);
  } catch { return null; }
}

function saveTplExpanded(set) {
  try { localStorage.setItem(TPL_EXPAND_KEY, JSON.stringify([...set])); } catch {}
}

function defaultTplExpanded(nodes) {
  const out = new Set();
  for (const n of nodes) if ((n.depth ?? 0) <= 1) out.add(n.id);
  return out;
}

function isAmtZero(amt) {
  return amt == null || Math.abs(amt) < 0.005;
}

function renderTemplateTable(nodes) {
  const table = el('pnlTable');
  const projects = state.pnl.projects;

  // childrenOf для скрытия потомков, если родитель свёрнут.
  const childrenOf = new Map();
  for (const n of nodes) {
    const arr = childrenOf.get(n.parent_id) || [];
    arr.push(n.id);
    childrenOf.set(n.parent_id, arr);
  }
  const hasChildren = (id) => (childrenOf.get(id) || []).length > 0;

  // Состояние expand: либо пользовательское из localStorage, либо дефолт.
  let expanded = loadTplExpanded();
  if (!expanded) expanded = defaultTplExpanded(nodes);

  // hide-zeros тоггл: скрываем строки, где у всех проектов amount=0.
  const hideZeros = (() => {
    try { return localStorage.getItem(TPL_HIDE_ZEROS_KEY) === '1'; }
    catch { return false; }
  })();

  // Автоскрытие пустых верхнеуровневых секций. Если у топ-уровневой секции
  // (depth=0, не is_calc) и всех её потомков total.amount == 0 — секция
  // структурно не используется этими юнитами в этом периоде, прячем её
  // целиком вместе с детьми. is_calc не трогаем — итоги показываем всегда.
  const nodeByIdAll = new Map(nodes.map(n => [n.id, n]));
  const autoHidden = (() => {
    const hidden = new Set();
    function subtreeAllZero(n) {
      if (!isAmtZero(n.total?.amount)) return false;
      for (const cid of (childrenOf.get(n.id) || [])) {
        const c = nodeByIdAll.get(cid);
        if (c && !subtreeAllZero(c)) return false;
      }
      return true;
    }
    function markHidden(n) {
      hidden.add(n.id);
      for (const cid of (childrenOf.get(n.id) || [])) {
        const c = nodeByIdAll.get(cid);
        if (c) markHidden(c);
      }
    }
    for (const n of nodes) {
      const depth = n.depth ?? 0;
      // Не скрываем строки с pnl_code (DIVIDENDS, INTEREST и т.п.) —
      // они часть стандартного P&L и должны быть видимы даже с amount=0.
      // Через тоггл «скрыть нули» юзер может скрыть их сам.
      if (depth === 0 && !n.is_calc && !n.pnl_code && subtreeAllZero(n)) markHidden(n);
    }
    return hidden;
  })();

  // === thead с тулбаром ===
  const expandAllBtn = `<button id="tplExpandAll" type="button" class="tree-toolbtn" title="Раскрыть все">⊞ всё</button>`;
  const collapseAllBtn = `<button id="tplCollapseAll" type="button" class="tree-toolbtn" title="Свернуть всё">⊟ всё</button>`;
  const hideZerosBtn = `<label class="tree-toolbtn-check"><input type="checkbox" id="tplHideZeros" ${hideZeros ? 'checked' : ''}> скрыть нули</label>`;
  const toolbar = `<div class="tree-toolbar">${expandAllBtn}${collapseAllBtn}${hideZerosBtn}</div>`;

  // S13.4: в Период детализация группируется по месяцам, а не по проектам.
  // Колонки = месяцы из state.pnl.months_in_range + Итого. Per-project
  // breakdown в Период не отображается (юзер так попросил).
  const periodView = state.mode === 'period' && Array.isArray(state.pnl?.months_in_range);
  const periodMonths = periodView ? state.pnl.months_in_range : [];
  const monthlyData = periodView ? (state.pnl.monthly || {}) : {};

  let thead = '<thead><tr>';
  thead += `<th class="tree-col-head">Статья${toolbar}</th>`;
  if (periodView) {
    periodMonths.forEach(m => {
      thead += `<th title="${esc(monthLabel(m))}">${esc(monthLabel(m))}</th>`;
    });
  } else {
    projects.forEach(p => thead += `<th title="${esc(p.name)}">${esc(p.name)}</th>`);
  }
  thead += '<th title="Сумма за весь период">Итого</th></tr></thead>';

  // Видимость строки = все её предки в expanded.
  const nodeById = new Map(nodes.map(n => [n.id, n]));
  function isVisible(n) {
    let pid = n.parent_id;
    while (pid != null) {
      if (!expanded.has(pid)) return false;
      const parent = nodeById.get(pid);
      if (!parent) break;
      pid = parent.parent_id;
    }
    return true;
  }

  let tbody = '<tbody>';
  for (const n of nodes) {
    if (autoHidden.has(n.id)) continue;
    if (!isVisible(n)) continue;
    if (hideZeros && isAmtZero(n.total?.amount)) continue;

    const depth = n.depth ?? 0;
    const isOpen = expanded.has(n.id);
    const branch = hasChildren(n.id);
    // Семантика стилей — по depth + is_calc:
    //   depth 0  → header (жирный, фон)
    //   is_calc  → summary (итоговая)
    //   leaf     → detail (кликабельный для drill)
    let cls;
    // is_calc проверяем РАНЬШЕ depth — иначе расчётные строки на depth=0
    // (Маржинальная прибыль, Операционная прибыль, EBITDA, Чистая прибыль)
    // получают стиль секции и сливаются с группами.
    if (n.is_calc) cls = 'tpl-row tpl-calc';
    else if (depth === 0) cls = 'tpl-row tpl-h0';
    else if (branch) cls = `tpl-row tpl-h${Math.min(depth, 3)}`;
    else cls = 'tpl-row tpl-leaf';
    // Финальная итоговая строка («Чистая прибыль (убыток)») — особое
    // тёмное оформление, чтобы выделять самый низ P&L.
    if (n.is_calc && /чистая\s+прибыль/i.test(n.title || '')) {
      cls += ' tpl-final';
    }

    const clickable = !branch && !n.is_calc && n.pnl_code !== 'REVENUE' ? ' clickable' : '';
    const toggle = branch
      ? `<button class="tpl-toggle ${isOpen ? 'open' : ''}" data-toggle="${n.id}" type="button" aria-label="${isOpen ? 'Свернуть' : 'Раскрыть'}">${isOpen ? '▾' : '▸'}</button>`
      : `<span class="tpl-toggle-spacer"></span>`;
    const indent = `<span class="tpl-indent" style="--d:${depth}"></span>`;
    const titleHtml = `${indent}${toggle}<span class="tpl-title">${esc(n.title)}</span>`;

    // Drill-down кликабельны только листья, у которых есть amount-значения.
    // is_calc и REVENUE-узлы в drill пока не отдаём.
    const isDrillable = !branch && !n.is_calc && n.pnl_code !== 'REVENUE';

    tbody += `<tr class="${cls}${clickable}" data-node-id="${n.id}" data-code="${n.pnl_code || ''}">`;
    tbody += `<td class="tpl-name">${titleHtml}</td>`;

    // Процентные calc-строки (Маржинальность, Рентабельность по EBITDA, ...)
    // рисуем как «X,X%» в основной ячейке, без денежного значения и без второй строки %.
    const isPctRow = n.display_kind === 'pct';

    // Список конкретных category_id, которые откатываются на этот узел
    // (включая всех детей). Кодируем как JSON в data-cat-ids — чтобы при
    // клике передать в drill и получить операции только по этой статье.
    const catIds = Array.isArray(n.category_ids) ? n.category_ids : [];
    const catIdsJson = esc(JSON.stringify(catIds));

    if (periodView) {
      // S13.4: одна колонка на каждый месяц периода. Значения берём из
      // monthly[m].by_node[n.id]. Per-project breakdown в Период не показываем.
      // Для calc-pct-строк (Маржинальность %, Рентабельность %) у нас
      // нет помесячного процента — пишем «—», т.к. среднее % за месяц
      // не совпадает с месячным фактом.
      periodMonths.forEach(m => {
        const byNode = (monthlyData[m] && monthlyData[m].by_node) || {};
        const amt = byNode[String(n.id)];
        let cell;
        if (isPctRow) {
          cell = `<span>—</span>`;
        } else {
          const valCls = (amt != null && amt < 0) ? 'neg' : '';
          cell = `<span class="${valCls}">${fmt(amt)}</span>`;
        }
        // Drill за этот месяц: data-month=YYYY-MM, dates переопределим
        // в обработчике клика. data-pid пуст — period-режим без проектной разбивки.
        const cellCls = isDrillable ? 'cell-clickable' : '';
        const cellAttrs = isDrillable
          ? ` data-month="${m}" data-pid="" data-pname="Итого · ${esc(monthLabel(m))}" data-amt="${amt ?? ''}" data-cat-ids="${catIdsJson}"`
          : '';
        tbody += `<td class="${cellCls}"${cellAttrs}>${cell}</td>`;
      });
    } else {
      projects.forEach(p => {
        const proj = (n.projects || {})[p.id] || {};
        const amt = proj.amount;
        const pct = proj.pct_of_revenue;
        let cell;
        if (isPctRow) {
          const valCls = (pct != null && pct < 0) ? 'neg' : '';
          cell = `<span class="${valCls}">${fmtPctAbs(pct)}</span>`;
        } else {
          const valCls = (amt != null && amt < 0) ? 'neg' : '';
          cell = `<span class="${valCls}">${fmt(amt)}</span>`;
          // % показываем для расходных строк (не выручки), и НЕ для
          // расчётных итогов (Маржин/Op.Profit/EBITDA/Чистая прибыль) —
          // у них % дублируется в следующей строке «Рентабельность».
          if (pct != null && n.pnl_code !== 'REVENUE' && !n.is_calc) {
            cell += `<span class="pct">${fmtPctAbs(pct)}</span>`;
          }
        }
        const cellCls = isDrillable ? 'cell-clickable' : '';
        const cellAttrs = isDrillable
          ? ` data-pid="${p.id}" data-pname="${esc(p.name)}" data-amt="${amt ?? ''}" data-cat-ids="${catIdsJson}"`
          : '';
        tbody += `<td class="${cellCls}"${cellAttrs}>${cell}</td>`;
      });
    }

    const tot = n.total || {};
    let totalCell;
    if (isPctRow) {
      const valCls = (tot.pct_of_revenue != null && tot.pct_of_revenue < 0) ? 'neg' : '';
      totalCell = `<strong class="${valCls}">${fmtPctAbs(tot.pct_of_revenue)}</strong>`;
    } else {
      totalCell = `<strong>${fmt(tot.amount)}</strong>`;
      // Без % для calc-итогов (Маржин/EBITDA/Op.Profit/Чистая прибыль):
      // % уже есть в следующей строке «Рентабельность».
      if (tot.pct_of_revenue != null && n.pnl_code !== 'REVENUE' && !n.is_calc) {
        totalCell += `<span class="pct">${fmtPctAbs(tot.pct_of_revenue)}</span>`;
      }
    }
    const totalCls = isDrillable ? 'cell-clickable' : '';
    const totalAttrs = isDrillable
      ? ` data-pid="" data-pname="Итого" data-amt="${tot.amount ?? ''}" data-cat-ids="${catIdsJson}"`
      : '';
    tbody += `<td class="${totalCls}"${totalAttrs}>${totalCell}</td></tr>`;
  }
  tbody += '</tbody>';

  table.innerHTML = thead + tbody;

  // === wire toolbar ===
  el('tplExpandAll')?.addEventListener('click', () => {
    expanded = new Set(nodes.map(n => n.id));
    saveTplExpanded(expanded);
    renderTemplateTable(nodes);
  });
  el('tplCollapseAll')?.addEventListener('click', () => {
    expanded = new Set();
    saveTplExpanded(expanded);
    renderTemplateTable(nodes);
  });
  el('tplHideZeros')?.addEventListener('change', (e) => {
    try { localStorage.setItem(TPL_HIDE_ZEROS_KEY, e.target.checked ? '1' : '0'); } catch {}
    renderTemplateTable(nodes);
  });

  // === wire toggles ===
  table.querySelectorAll('button[data-toggle]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset.toggle);
      if (expanded.has(id)) expanded.delete(id);
      else expanded.add(id);
      saveTplExpanded(expanded);
      renderTemplateTable(nodes);
    });
  });

  // === wire drill on leaf-cells ===
  // Кликабельны отдельные ячейки, а не вся строка — чтобы фильтр по проекту
  // совпадал с колонкой, по которой кликнули. Ячейка «Итого» — без фильтра по проекту.
  // S13.4: в Период колонки = месяцы, drill ограничивается этим месяцем
  // (data-month). Клик на «Итого» → drill за весь период.
  table.querySelectorAll('td.cell-clickable').forEach(td => {
    td.addEventListener('click', (e) => {
      e.stopPropagation();
      const tr = td.closest('tr');
      const code = tr.dataset.code;
      const title = tr.querySelector('.tpl-title')?.textContent || '';
      let catIds = [];
      try { catIds = JSON.parse(td.dataset.catIds || '[]') || []; } catch {}
      const rawAmt = td.dataset.amt;
      const expectedAmt = rawAmt !== '' && rawAmt != null ? Number(rawAmt) : null;
      // Если ячейка из колонки месяца (Period mode) — переопределяем диапазон.
      let dateOverride = null;
      const cellMonth = td.dataset.month;
      if (cellMonth) {
        const [y, m] = cellMonth.split('-').map(Number);
        const last = new Date(y, m, 0).getDate();
        dateOverride = {
          start: `${cellMonth}-01`,
          end: `${cellMonth}-${String(last).padStart(2, '0')}`,
        };
      }
      openDrillDown(code, title, td.dataset.pid || null, td.dataset.pname || '', catIds, expectedAmt, dateOverride);
    });
  });
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// --- Drill-down ---
// projectId = null → показываем по всем выбранным проектам (Итого).
// projectName — для заголовка модалки ("Кубинка-1 · Сырьё" vs "Итого · Сырьё").
// categoryIds — конкретные id статей PlanFact, по которым фильтруем операции.
//   Если пусто — фильтра по статье нет (fallback для агрегатной таблицы).
// expectedAmount — сумма из таблицы P&L (для подсветки расхождения с операциями).
async function openDrillDown(code, label, projectId = null, projectName = '', categoryIds = [], expectedAmount = null, dateOverride = null) {
  const prefix = projectName ? `${projectName} · ` : '';
  el('drillTitle').textContent = `Операции · ${prefix}${label}`;
  el('drillBody').innerHTML = '<p class="muted">Загрузка…</p>';
  el('drillModal').classList.remove('hidden');

  // S13.4: dateOverride переопределяет период drill — клик на ячейку
  // конкретного месяца в Период-режиме открывает только этот месяц,
  // а не весь диапазон.
  const ds = dateOverride?.start || el('dateStart').value;
  const de = dateOverride?.end || el('dateEnd').value;
  const params = new URLSearchParams({ date_start: ds, date_end: de, limit: '500' });
  if (projectId) {
    params.set('project_id', projectId);
  } else {
    // «Итого» (Месяц-режим) или ячейка месяца (Период) → ограничиваем выборкой
    // пользователя. Иначе backend отдаст все проекты ключа PF, что неверно
    // (юзер видит итог по своим выбранным пиццериям, а в drill — по всем).
    state.selectedProjects.forEach(p => params.append('project_ids', p));
  }
  for (const cid of (categoryIds || [])) {
    if (cid) params.append('category_ids', cid);
  }

  // S13.6: запоминаем контекст для кнопки «📥 .xlsx» в шапке модалки.
  state._drillCtx = {
    dateStart: ds, dateEnd: de,
    projectId,
    projectIds: projectId ? [] : [...state.selectedProjects],
    categoryIds: [...(categoryIds || [])],
    label: `${prefix}${label}`,
  };

  try {
    const data = await api('/api/operations?' + params.toString());
    const items = data.items || [];

    // Шапка-итог как в ПланФакте: Проект / Период / Сумма операций.
    const periodTxt = `${ds} — ${de}`;
    const projTxt = projectName || 'Все проекты';
    const sumTxt = fmt(data.sum_value);
    const sumCls = (typeof data.sum_value === 'number' && data.sum_value < 0) ? 'neg' : '';
    let mismatch = '';
    if (expectedAmount != null && Number.isFinite(expectedAmount) && typeof data.sum_value === 'number') {
      const diff = Math.abs(expectedAmount - data.sum_value);
      // Сравниваем по абсолютной сумме операции, а не по знаку: P&L Outcome = +,
      // а в детализации Outcome возвращается со знаком минус. Маленькая дельта — ок.
      if (diff > 1) {
        mismatch = `<span class="muted" style="margin-left:8px">· в таблице P&L: ${fmt(expectedAmount)}</span>`;
      }
    }

    let summary = `
      <div class="drill-summary">
        <div class="drill-sum-row"><span class="drill-sum-k">Проект</span><span class="drill-sum-v">${esc(projTxt)}</span></div>
        <div class="drill-sum-row"><span class="drill-sum-k">Статья</span><span class="drill-sum-v">${esc(label)}</span></div>
        <div class="drill-sum-row"><span class="drill-sum-k">Период</span><span class="drill-sum-v">${esc(periodTxt)}</span></div>
        <div class="drill-sum-row"><span class="drill-sum-k">Операций</span><span class="drill-sum-v">${data.filtered_count ?? items.length}</span></div>
        <div class="drill-sum-row"><span class="drill-sum-k">Сумма операций</span><span class="drill-sum-v ${sumCls}"><strong>${sumTxt}</strong>${mismatch}</span></div>
      </div>`;

    if (!items.length) {
      el('drillBody').innerHTML = summary + '<p class="muted">Операций не найдено.</p>';
      return;
    }

    let html = summary + `<table class="drill-table">
      <colgroup>
        <col class="c-date"><col class="c-cat"><col class="c-proj">
        <col class="c-ca"><col class="c-comment"><col class="c-sum">
      </colgroup>
      <thead><tr>
        <th>Дата</th><th>Статья</th><th>Проект</th>
        <th>Контрагент</th><th>Комментарий</th><th class="th-sum">Сумма</th>
      </tr></thead><tbody>`;
    items.slice(0, 500).forEach(op => {
      const v = op.value;
      const vCls = (typeof v === 'number' && v < 0) ? 'neg' : '';
      const cat = op.category || '';
      const proj = op.project || '';
      const ca = op.contrAgent || '';
      const comm = op.comment || '';
      // title-tooltip с полным текстом — чтобы клиппинг ellipsis не терял инфу
      html += `<tr>
        <td>${esc(fmtDateRu(op.date))}</td>
        <td title="${esc(cat)}">${esc(cat)}</td>
        <td title="${esc(proj)}">${esc(proj)}</td>
        <td title="${esc(ca)}">${esc(ca)}</td>
        <td title="${esc(comm)}">${esc(comm)}</td>
        <td class="td-sum ${vCls}">${fmt(v)}</td>
      </tr>`;
    });
    html += '</tbody></table>';
    el('drillBody').innerHTML = html;
  } catch (e) {
    el('drillBody').innerHTML = `<p class="neg">Ошибка: ${esc(e.message)}</p>`;
  }
}

// --- Wire up events ---
document.addEventListener('DOMContentLoaded', async () => {
  initMonthSelect();

  // Drawer (мобильный сайдбар проектов) — открывается hamburger-ом,
  // закрывается по клику на backdrop или по Escape.
  const _toggleDrawer = (open) => {
    document.body.classList.toggle('drawer-open',
      typeof open === 'boolean' ? open : !document.body.classList.contains('drawer-open'));
    const bd = el('drawerBackdrop');
    if (bd) bd.hidden = !document.body.classList.contains('drawer-open');
  };
  el('drawerToggle')?.addEventListener('click', () => _toggleDrawer());
  el('drawerBackdrop')?.addEventListener('click', () => _toggleDrawer(false));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.classList.contains('drawer-open')) _toggleDrawer(false);
  });

  // На мобиле топбар может быть 2-строчным (grid 2x). .panel под ним
  // позиционируется по var(--topbar-h). Обновляем переменную при resize.
  const _setTopbarHVar = () => {
    const tb = document.querySelector('.topbar');
    if (!tb) return;
    const h = tb.offsetHeight;
    if (h > 0) document.documentElement.style.setProperty('--topbar-h', h + 'px');
  };
  _setTopbarHVar();
  window.addEventListener('resize', _setTopbarHVar);
  // Шрифты могут догрузиться — ещё раз через тик
  setTimeout(_setTopbarHVar, 100);

  // Клик по карточке графика → увеличенная версия в модалке.
  el('chartsGrid')?.addEventListener('click', (e) => {
    const box = e.target.closest('.chart-box[data-chart-id]');
    if (box) openChartModal(box.dataset.chartId);
  });
  el('chartModalBackdrop')?.addEventListener('click', closeChartModal);
  el('chartModalClose')?.addEventListener('click', closeChartModal);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !el('chartModal')?.classList.contains('hidden')) closeChartModal();
  });

  // Попап «⚙ Графики» — список + кнопки «Показать все / Скрыть все».
  // Применяем visibility сразу, чтобы скрытые графики не мелькали при первом
  // рендере перед загрузкой данных.
  applyChartsVisibility();
  renderChartsConfigList();
  el('chartsConfigShowAll')?.addEventListener('click', () => setAllChartsVisible(true));
  el('chartsConfigHideAll')?.addEventListener('click', () => setAllChartsVisible(false));

  // Попап «⚙ Метрики» — список плиток + кнопки.
  renderMetricsConfigList();
  el('metricsConfigShowAll')?.addEventListener('click', () => setAllMetricsVisible(true));
  el('metricsConfigHideAll')?.addEventListener('click', () => setAllMetricsVisible(false));

  // UX-5: обновляем подпись «вкл/выкл» внутри pill — JS-handler рядом
  // с триггером загрузки данных, чтобы было одно место истины.
  const _updateLflPill = () => {
    const stateEl = document.querySelector('.lfl-pill-state');
    if (stateEl) {
      stateEl.textContent = el('compareToggle').checked ? 'вкл' : 'выкл';
    }
  };
  el('compareToggle').addEventListener('change', () => {
    _updateLflPill();
    loadPnl();
  });
  _updateLflPill();

  // S3.6/S11.9/S15.1: «⟳ Обновить» — три действия одной кнопкой:
  //   1. backend сбрасывает in-memory PlanFact-кэш текущего юзера;
  //   2. если месяц был closed (snapshot в cache_history) — snapshot
  //      удаляется, при следующем /api/pnl данные пересоберутся живьём;
  //   3. стартует фоновый синк ops-метрик из Dodo IS (для live-месяцев).
  // Backend возвращается мгновенно (202 scheduled). После этого фронт сразу
  // дёргает /api/pnl — он попадёт на чистый кэш и принесёт свежие данные.
  // Если ops sync был стартован — крутим авто-poll до 3 минут с шагом 8с.
  el('opsRefreshBtn')?.addEventListener('click', async () => {
    const btn = el('opsRefreshBtn');
    if (!state.currentMonth) return;
    btn.disabled = true;
    btn.classList.add('spinning');
    try {
      const r = await fetch(
        `/api/ops-metrics/sync?period=${state.currentMonth}`,
        { method: 'POST' },
      );
      if (!r.ok) {
        const t = await r.text();
        throw new Error(`${r.status}: ${t}`);
      }
      const res = await r.json();
      const snap = res.snapshot_invalidated;
      if (res.status === 'already_running') {
        toast(snap
          ? 'PnL пересобираем, синк ops уже идёт'
          : 'Синхронизация уже идёт');
      } else {
        toast(snap
          ? 'Обновляем P&L и ops-метрики…'
          : 'Обновляем данные…');
      }
      // Один сразу — UI покажет либо свежие данные (если snapshot был
      // удалён и cache_history miss), либо is_syncing=true (badge сменится).
      await loadPnl();
      // Авто-poll только если ops-синк реально стартовал.
      if (res.status === 'scheduled') {
        _pollOpsSync(state.currentMonth);
      }
    } catch (e) {
      toast('Ошибка обновления: ' + e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.classList.remove('spinning');
    }
  });

  document.querySelectorAll('[data-close]').forEach(b => {
    b.addEventListener('click', () => b.closest('.modal').classList.add('hidden'));
  });

  // S13.5: экспорт детализации в .xlsx — собираем ровно те же query-params,
  // что и /api/pnl, и редиректим в /api/pnl.xlsx (браузер скачает файл).
  el('pnlExportXlsxBtn')?.addEventListener('click', () => {
    if (!state.pnl || state.selectedProjects.size === 0) {
      toast('Сначала выбери период и пиццерии', 'error');
      return;
    }
    const ds = el('dateStart').value;
    const de = el('dateEnd').value;
    const params = new URLSearchParams();
    params.set('date_start', ds);
    params.set('date_end', de);
    state.selectedProjects.forEach(p => params.append('project_ids', p));
    if (state.mode === 'month') {
      params.set('period_month', state.currentMonth);
    } else {
      params.set('group_by', 'month');
    }
    window.location.href = '/api/pnl.xlsx?' + params.toString();
  });

  // S13.6: экспорт drill-down — кнопка живёт всегда внутри модалки, но
  // имеет смысл только когда модалка открыта. Параметры берём из state,
  // куда они складывались при последнем openDrillDown.
  el('drillExportXlsxBtn')?.addEventListener('click', () => {
    const ctx = state._drillCtx;
    if (!ctx) return;
    const params = new URLSearchParams({
      date_start: ctx.dateStart, date_end: ctx.dateEnd,
    });
    if (ctx.projectId) params.set('project_id', ctx.projectId);
    else (ctx.projectIds || []).forEach(p => params.append('project_ids', p));
    (ctx.categoryIds || []).forEach(c => params.append('category_ids', c));
    if (ctx.label) params.set('label', ctx.label);
    window.location.href = '/api/operations.xlsx?' + params.toString();
  });

  try {
    // Дождёмся загрузки профиля юзера, чтобы _selectionKey() ключевался
    // именно по нему (S10.2). Если /auth/me долго отвечает — стартуем
    // через 800мс по таймауту чтобы не блокировать дашборд.
    await Promise.race([
      new Promise(r => window.addEventListener('user-loaded', r, { once: true })),
      new Promise(r => setTimeout(r, 800)),
    ]);
    // S13.1: восстанавливаем mode и диапазон ПЕРЕД loadPnl, чтобы первый
    // запрос ушёл уже под нужный режим. Поля from/to синхронизируем с
    // селекторами, потом applyMode переключит видимость и пересчитает
    // dateStart/dateEnd.
    loadModeAndRangeFromStorage();
    if (state.periodFrom) el('monthSelectFrom').value = state.periodFrom;
    if (state.periodTo) el('monthSelectTo').value = state.periodTo;
    applyMode(state.mode);
    await loadProjects();
    await loadPnl();
  } catch (e) {
    toast('Не удалось загрузить проекты. Проверь API-ключ PlanFact в .env', 'error');
  }
});
