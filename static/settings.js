// Settings page — плотная Excel-style страница.
// Один скролл, три блока: Проекты, Цели P&L (матрица), Ops (матрица + таргеты в header).

// Возможные P&L-коды для шаблона (показываются в select на каждом узле).
const TEMPLATE_CODES = [
  'UC', 'LC', 'DC', 'RENT', 'MARKETING', 'FRANCHISE', 'MGMT',
  'OTHER_OPEX', 'REVENUE', 'OTHER_INCOME', 'TAX', 'INTEREST', 'DIVIDENDS',
];

const state = {
  projects: [],
  settings: {},
  defaultTargets: {},     // {UC: 0.30}
  projectTargets: {},     // {'pid|METRIC': pct}
  opsTargets: {},         // {ORD_PER_COURIER_H: 2.5} — глобальные дефолты
  opsProjectTargets: {},  // {'pid|CODE': value} — override на пиццерию
  opsMetrics: {},         // {pid: {orders_per_courier_h, ...}} — факт из Dodo IS
  opsMeta: [],
  dodoUnits: [],          // [{id, name}] из /api/dodois/units
  template: {
    nodes: [],            // сохранённый шаблон с бэка (с реальными id)
    preview: null,        // плоский список из preview, ещё не сохранён
    warnings: [],
  },
  targetableMetrics: ['UC','LC','DC','TC'],
  pnlCodes: {
    UC: 'UC',
    LC: 'LC',
    DC: 'DC',
    TC: 'TC',
  },
  pnlFullNames: {
    UC: 'Себестоимость',
    LC: 'Оплата труда',
    DC: 'Доставка',
    TC: 'Итого (UC+LC+DC)',
  },
  currentMonth: null,
  // Текущий пользователь — заполняется при загрузке через /auth/me.
  // is_admin = true → секция «Проекты» редактируемая, false → read-only.
  me: null,
};

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));

function toast(msg, kind = '') {
  const t = el('toast');
  t.className = 'toast ' + kind;
  t.textContent = msg;
  setTimeout(() => t.classList.add('hidden'), 1800);
  t.classList.remove('hidden');
}

const parseNum = (raw) => {
  if (raw == null) return null;
  const s = String(raw).trim().replace(/\s+/g, '').replace(',', '.');
  if (s === '') return null;
  const n = parseFloat(s);
  return isNaN(n) ? null : n;
};

const fmtNum = (n, digits = 2) => {
  if (n === null || n === undefined || isNaN(n)) return '';
  return Number(n).toLocaleString('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: digits });
};

const fmtPct = (v) => (v == null || isNaN(v)) ? '' : (v * 100).toFixed(1).replace(/\.0$/, '').replace('.', ',');

// mm:ss — для метрик времени (AVG_DELIVERY, AOT, COOK_TIME_*), где значение
// хранится в СЕКУНДАХ. Показываем и вводим как «мм:сс» (напр. 33:03), а в БД
// всё так же уходит число секунд. Ввод допускает и «мм:сс», и просто секунды.
const fmtMmss = (sec) => {
  if (sec == null || isNaN(sec)) return '';
  const t = Math.round(Math.abs(sec));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
};
const parseMmss = (raw) => {
  if (raw == null) return null;
  const s = String(raw).trim();
  if (s === '') return null;
  if (s.includes(':')) {
    const parts = s.split(':');
    if (parts.length !== 2) return null;
    const mm = parseInt(parts[0], 10);
    const ss = parseInt(parts[1], 10);
    if (isNaN(mm) || isNaN(ss) || ss < 0 || ss >= 60) return null;
    return mm * 60 + ss;
  }
  const n = parseNum(s);
  return n == null ? null : Math.round(n);
};
const isMmss = (m) => !!(m && m.format === 'mm_ss');
// Формат/парс ops-значения с учётом mm_ss.
const fmtOps = (m, v) =>
  isMmss(m) ? fmtMmss(v) : fmtNum(v, (m && typeof m.digits === 'number') ? m.digits : 2);
const parseOps = (m, raw) => isMmss(m) ? parseMmss(raw) : parseNum(raw);

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (r.status === 401) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = '/login?next=' + next;
    return new Promise(() => {});
  }
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}
async function post(path, body) {
  return api(path, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
}
async function del(path) { return api(path, { method:'DELETE' }); }

// ---------- flash for auto-save feedback ----------
function flashOk(inputEl) {
  inputEl.classList.remove('cell-flash-err');
  inputEl.classList.add('cell-flash-ok');
  setTimeout(() => inputEl.classList.remove('cell-flash-ok'), 700);
}
function flashErr(inputEl) {
  inputEl.classList.remove('cell-flash-ok');
  inputEl.classList.add('cell-flash-err');
  setTimeout(() => inputEl.classList.remove('cell-flash-err'), 1500);
}

// ---------- Tabs (Профиль / Проекты и статьи / Показатели / Цели / Команда) ----------
// «Интеграции» свёрнуты в «Профиль»; «Цели» — для visibility ≥ 30 (.lvl30-only).
const TABS = ['profile', 'source', 'structure', 'metrics', 'targets', 'users', 'platform'];
const TAB_STORAGE_KEY = 'pnlSettings.activeTab';

function showTab(name) {
  if (!TABS.includes(name)) name = 'profile';
  document.querySelectorAll('.tab-btn').forEach(b => {
    const on = b.dataset.tab === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.querySelectorAll('.tab-pane').forEach(p => {
    p.classList.toggle('active', p.dataset.pane === name);
  });
  if (name === 'users') { try { renderAccessRequests(); } catch (_) {} }
  try { localStorage.setItem(TAB_STORAGE_KEY, name); } catch (_) {}
}

// Запросы на доступ (network_admin/super_admin): сотрудники, вошедшие через
// Dodo IS и ждущие одобрения. Одобрение создаёт User + привязку dodois_sub.
const _AR_VIS = [[10, 'Управляющий'], [30, 'Территориальный'], [60, 'Директор'], [100, 'Партнёр']];
function _setAccessReqBadge(n) {
  const btn = document.querySelector('.tab-btn[data-tab="users"]');
  if (!btn) return;
  let b = btn.querySelector('.tab-badge');
  if (n > 0) {
    if (!b) {
      b = document.createElement('span');
      b.className = 'tab-badge';
      b.style.cssText = 'display:inline-block;min-width:16px;height:16px;line-height:16px;padding:0 4px;margin-left:6px;border-radius:8px;background:#dc2626;color:#fff;font-size:11px;font-weight:700;text-align:center;vertical-align:middle';
      btn.appendChild(b);
    }
    b.textContent = String(n);
  } else if (b) {
    b.remove();
  }
}
async function renderAccessRequests() {
  const block = document.getElementById('accessReqBlock');
  const tbody = document.querySelector('#accessReqTable tbody');
  let rows = [];
  try { rows = await api('/api/admin/access-requests'); } catch (_) { return; }
  const n = Array.isArray(rows) ? rows.length : 0;
  _setAccessReqBadge(n);
  if (!block || !tbody) return;
  if (!n) { block.style.display = 'none'; return; }
  tbody.innerHTML = rows.map(r => {
    const units = (r.units || []).map(u => esc((u.name || u.uuid || '')).slice(0, 40))
      .filter(Boolean).join(', ');
    const sel = `<select class="ar-vis" data-id="${r.id}">`
      + _AR_VIS.map(([v, l]) => `<option value="${v}"${v === 10 ? ' selected' : ''}>${l}</option>`).join('')
      + `</select>`;
    return `<tr data-id="${r.id}">
      <td>${esc(r.name || '—')}</td>
      <td class="muted" style="font-size:11px">${esc((r.dodois_sub || '').slice(0, 10))}…</td>
      <td class="muted" style="font-size:11px">${units || '—'}</td>
      <td>${sel}</td>
      <td style="white-space:nowrap">
        <button type="button" class="btn-primary ar-approve" data-id="${r.id}">Одобрить</button>
        <button type="button" class="btn-secondary ar-deny" data-id="${r.id}">Отклонить</button>
      </td></tr>`;
  }).join('');
  block.style.display = '';
  tbody.querySelectorAll('.ar-approve').forEach(b => b.addEventListener('click', async () => {
    const id = b.dataset.id;
    const visEl = tbody.querySelector(`.ar-vis[data-id="${id}"]`);
    const vis = parseInt(visEl && visEl.value, 10) || 10;
    b.disabled = true;
    try {
      await post(`/api/admin/access-requests/${id}/approve`, { visibility_level: vis });
      await renderAccessRequests();
      if (typeof renderUsersTable === 'function') await renderUsersTable();
    } catch (_) { b.disabled = false; alert('Не удалось одобрить'); }
  }));
  tbody.querySelectorAll('.ar-deny').forEach(b => b.addEventListener('click', async () => {
    const id = b.dataset.id;
    if (!confirm('Отклонить запрос?')) return;
    b.disabled = true;
    try { await api(`/api/admin/access-requests/${id}/deny`, { method: 'POST' }); await renderAccessRequests(); }
    catch (_) { b.disabled = false; }
  }));
}

function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });
  // Стартовая вкладка: localStorage либо «Цели» по умолчанию.
  // Финальное переключение на «Структуру» — если шаблон ещё не загружен —
  // делается в loadAll() после получения /api/template.
  // ?tab= в URL (напр. из бейджа на главной → /settings?tab=users) имеет
  // приоритет над сохранённой вкладкой.
  const urlTab = new URLSearchParams(location.search).get('tab');
  let saved = null;
  try { saved = localStorage.getItem(TAB_STORAGE_KEY); } catch (_) {}
  showTab(urlTab || saved || 'targets');
}

// ---------- Load ----------
async function loadAll() {
  // Табы и кнопки шаблона должны работать даже если какой-то из API упадёт —
  // поэтому навешиваем хендлеры до сетевых запросов.
  initTabs();
  initTemplate();

  // Используем allSettled — если /api/template упадёт, остальные блоки прогрузятся.
  const results = await Promise.allSettled([
    api('/api/projects'),
    api('/api/settings'),
    api('/api/targets/defaults'),
    api('/api/targets'),
    api('/api/ops-targets'),
    api('/api/template'),
    api('/auth/me'),
    api('/api/metrics'),
  ]);
  const [projR, setR, defR, tarR, opsTgR, tplR, meR, metR] = results;
  const ok = (r, fb) => r.status === 'fulfilled' ? r.value : fb;
  state.me = ok(meR, null);

  const projResp = ok(projR, { projects: [] });
  const setResp  = ok(setR,  { settings: {} });
  const defResp  = ok(defR,  { defaults: {} });
  const tarResp  = ok(tarR,  { targets: [] });
  const opsTgResp= ok(opsTgR,{ targets: {}, project_targets: [], meta: [] });
  const tplResp  = ok(tplR,  { nodes: [] });
  const metResp  = ok(metR,  { metrics: [] });
  state.metrics = metResp.metrics || [];

  // Уведомим про упавшие запросы — но не прервём загрузку.
  results.forEach((r, i) => {
    if (r.status === 'rejected') {
      const url = ['/api/projects','/api/settings','/api/targets/defaults','/api/targets','/api/ops-targets','/api/template','/auth/me','/api/metrics'][i];
      console.error(`[loadAll] ${url} failed:`, r.reason);
    }
  });

  state.projects = (projResp.projects || []).slice().sort((a, b) => {
    if (a.is_active !== b.is_active) return a.is_active ? -1 : 1;
    const sa = a.sort_order ?? 9999, sb = b.sort_order ?? 9999;
    if (sa !== sb) return sa - sb;
    return (a.name || '').localeCompare(b.name || '', 'ru');
  });
  state.settings = setResp.settings || {};
  state.defaultTargets = defResp.defaults || {};
  state.projectTargets = {};
  (tarResp.targets || []).forEach(t => {
    state.projectTargets[`${t.project_id}|${t.metric_code}`] = t.target_pct;
  });
  state.opsTargets = opsTgResp.targets || {};
  state.opsProjectTargets = {};
  (opsTgResp.project_targets || []).forEach(t => {
    state.opsProjectTargets[`${t.project_id}|${t.metric_code}`] = t.target_value;
  });
  state.opsMeta = opsTgResp.meta || [];
  state.template.nodes = (tplResp.nodes || []);
  state.template.preview = null;
  state.template.warnings = [];

  initMonthSelect();
  initDodoSync();
  // Перегрузка целей под выбранный месяц (initMonthSelect ставит текущий).
  // Без этого state.projectTargets остался бы пустым: предыдущие fetch'и в
  // loadAll'е ходили без period_month → бэк возвращал бы __default__ (а его
  // больше нет, всё month-specific).
  await reloadTargets();
  await loadOpsMetrics();

  renderProjects();
  renderPnlMatrix();
  renderOpsMatrix();
  renderTemplate();

  // Бейдж со счётчиком pending-запросов на доступ — для админа, на загрузке
  // (renderAccessRequests сам ставит/снимает .tab-badge на вкладке «Команда»).
  if (state.me && state.me.is_admin) { renderAccessRequests().catch(() => {}); }

  // Онбординг: если шаблон ещё не загружен и пользователь не выбирал таб
  // вручную в этой сессии — лендим в «Структуру», там кнопка импорта.
  const urlTab = new URLSearchParams(location.search).get('tab');
  let savedTab = null;
  try { savedTab = localStorage.getItem(TAB_STORAGE_KEY); } catch (_) {}
  if (!urlTab && !savedTab && state.template.nodes.length === 0) {
    showTab('structure');
  }

  // Баннер с главной может прислать ?copy=1 → авто-копирование целей из
  // прошлого месяца (после загрузки таргетов). Param снимаем, чтобы не
  // повторялось при перезагрузке.
  if (new URLSearchParams(location.search).get('copy') === '1' && canEditTargets()) {
    showTab('targets');
    history.replaceState(null, '', location.pathname + '?tab=targets');
    copyTargetsFromPrev();
  }
}

// ---------- Dodo IS units sync ----------
function initDodoSync() {
  const btn = el('btnSyncUnits');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const status = el('unitsStatus');
    btn.disabled = true;
    status.textContent = 'Запрашиваю /api/dodois/units…';
    try {
      const res = await api('/api/dodois/units');
      state.dodoUnits = res.units || [];
      status.textContent = `Загружено юнитов: ${state.dodoUnits.length}`;
      renderProjects();           // перерисуем с обновлённым datalist
      toast('Юниты Dodo IS подгружены');
    } catch (e) {
      status.textContent = '';
      toast('Ошибка: ' + e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  });

  // Автопривязка по совпадающему имени — для админа.
  const autoBtn = el('btnAutoLinkUnits');
  if (autoBtn) {
    autoBtn.addEventListener('click', async () => {
      const status = el('unitsStatus');
      autoBtn.disabled = true;
      status.textContent = 'Сопоставляю по имени…';
      try {
        const r = await post('/api/projects/auto-link-dodois', {});
        status.textContent = r.summary || '';
        // Покажем подробности через toast/alert
        const lines = [r.summary];
        if (r.linked && r.linked.length) {
          lines.push('Привязано:');
          for (const l of r.linked) lines.push(`  • ${l.name} → ${l.unit_name}`);
        }
        if (r.no_match && r.no_match.length) {
          lines.push('Не нашли пару:');
          for (const m of r.no_match) lines.push(`  • ${m.name}`);
        }
        if (r.duplicate_unit_id && r.duplicate_unit_id.length) {
          lines.push('Юнит уже привязан к другому проекту:');
          for (const d of r.duplicate_unit_id) lines.push(`  • ${d.name} ↛ ${d.unit_name}`);
        }
        alert(lines.join('\n'));
        // Перезагрузим список проектов чтобы обновить uuid в таблице
        const projResp = await api('/api/projects');
        state.projects = (projResp.projects || []).slice().sort((a, b) => {
          if (a.is_active !== b.is_active) return a.is_active ? -1 : 1;
          return (a.sort_order ?? 999) - (b.sort_order ?? 999);
        });
        renderProjects();
        toast('Автопривязка завершена');
      } catch (e) {
        status.textContent = '';
        toast('Ошибка: ' + e.message, 'error');
      } finally {
        autoBtn.disabled = false;
      }
    });
  }
}

function dodoUnitName(uuid) {
  if (!uuid) return '';
  const u = state.dodoUnits.find(x => (x.id || '').toLowerCase() === uuid.toLowerCase());
  return u ? u.name : '';
}

// ---------- Month picker ----------
// Цели всегда месячные (нет общего __default__-уровня). Дефолт — текущий
// месяц. История: до этого был отдельный __default__-уровень («общие цели»),
// но прошлые периоды никто реально не редактировал, поэтому упростили.
function initMonthSelect() {
  const sel = el('opsMonthSelect');
  const now = new Date();
  const opts = [];
  for (let i = 0; i < 24; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    const label = d.toLocaleDateString('ru-RU', { year: 'numeric', month: 'long' });
    opts.push({ key, label: label.charAt(0).toUpperCase() + label.slice(1) });
  }
  sel.innerHTML = opts.map(o => `<option value="${o.key}">${esc(o.label)}</option>`).join('');
  state.targetsPeriod = opts[0].key;   // текущий месяц
  state.currentMonth = opts[0].key;
  sel.value = state.targetsPeriod;
  sel.addEventListener('change', async () => {
    state.targetsPeriod = sel.value;
    state.currentMonth = sel.value;
    await Promise.all([loadOpsMetrics(), reloadTargets()]);
    renderPnlMatrix();
    renderOpsMatrix();
    updateCopyTargetsBtn();
  });
  const copyBtn = el('copyTargetsBtn');
  if (copyBtn) copyBtn.addEventListener('click', copyTargetsFromPrev);
  updateCopyTargetsBtn();
}

// ---------- «Скопировать цели из прошлого месяца» ----------
// YYYY-MM → ключ предыдущего месяца.
function prevMonthKey(pm) {
  const [y, m] = String(pm).split('-').map(Number);
  const d = new Date(y, m - 2, 1);   // m 1-based → m-2 = предыдущий месяц (0-based)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}
function monthLabelRu(key) {
  const [y, m] = String(key).split('-').map(Number);
  const s = new Date(y, m - 1, 1)
    .toLocaleDateString('ru-RU', { year: 'numeric', month: 'long' });
  return s.charAt(0).toUpperCase() + s.slice(1);
}
function currentTargetsEmpty() {
  return Object.keys(state.projectTargets || {}).length === 0
    && Object.keys(state.defaultTargets || {}).length === 0
    && Object.keys(state.opsTargets || {}).length === 0
    && Object.keys(state.opsProjectTargets || {}).length === 0;
}
function updateCopyTargetsBtn() {
  const btn = el('copyTargetsBtn');
  if (!btn) return;
  if (!canEditTargets()) { btn.classList.add('hidden'); return; }
  const lbl = el('copyTargetsFromLabel');
  if (lbl) lbl.textContent = monthLabelRu(prevMonthKey(state.targetsPeriod));
  btn.classList.remove('hidden');
}
async function copyTargetsFromPrev() {
  const to = state.targetsPeriod;
  const from = prevMonthKey(to);
  if (!currentTargetsEmpty()) {
    if (!confirm(`В «${monthLabelRu(to)}» уже заданы цели. Перезаписать значениями `
      + `из «${monthLabelRu(from)}»?\nНесовпадающие ячейки текущего месяца останутся.`))
      return;
  }
  try {
    const res = await post('/api/targets/copy', { from_month: from, to_month: to });
    await reloadTargets();
    renderPnlMatrix();
    renderOpsMatrix();
    toast(`Скопировано целей: ${res.copied || 0}`, res.copied ? 'ok' : '');
  } catch (e) {
    toast('Не удалось скопировать: ' + e.message, 'error');
  }
}

// Перезагружает таргеты под выбранный period_month. Все цели month-specific —
// дополнительной «глобальной» подсказки больше нет, placeholder'ы пустые.
async function reloadTargets() {
  const pm = state.targetsPeriod;
  if (!pm) return;
  const q = `?period_month=${encodeURIComponent(pm)}`;
  try {
    const [defR, tarR, opsTgR] = await Promise.all([
      api('/api/targets/defaults' + q),
      api('/api/targets' + q),
      api('/api/ops-targets' + q),
    ]);

    state.defaultTargets = defR.defaults || {};
    state.projectTargets = {};
    (tarR.targets || []).forEach(t => {
      state.projectTargets[`${t.project_id}|${t.metric_code}`] = t.target_pct;
    });
    state.opsTargets = opsTgR.targets || {};
    state.opsProjectTargets = {};
    (opsTgR.project_targets || []).forEach(t => {
      state.opsProjectTargets[`${t.project_id}|${t.metric_code}`] = t.target_value;
    });

    // Global-копии больше не используются (нет __default__-уровня).
    // Оставляем пустые dict'ы, чтобы placeholder-логика рендера матриц
    // ниже отрабатывала корректно (не показывала никакой подсказки).
    state.defaultTargetsGlobal = {};
    state.projectTargetsGlobal = {};
    state.opsTargetsGlobal = {};
    state.opsProjectTargetsGlobal = {};
  } catch (e) {
    toast('Не удалось загрузить цели: ' + e.message, 'error');
  }
}

async function loadOpsMetrics() {
  // Факты ops показываем только при выбранном конкретном месяце.
  if (!state.currentMonth) {
    state.opsMetrics = {};
    return;
  }
  try {
    const res = await api(`/api/ops-metrics?period_month=${state.currentMonth}`);
    state.opsMetrics = res.metrics || {};
  } catch (e) {
    // Нет ключа / нет данных → не роняем loadAll. Иначе в DOMContentLoaded
    // не вызовется initUsersTab и у админа без ключа не раскроются вкладки
    // (в т.ч. «Платформа», где ключ и добавляется). Cold-start catch-22.
    console.error('[loadOpsMetrics] failed:', e);
    state.opsMetrics = {};
  }
}

// ======================================================
// БЛОК 1: Проекты — плотная таблица с тумблерами
// ======================================================
function renderProjects() {
  const box = el('projectsTable');
  if (!state.projects.length) {
    box.innerHTML = '<p class="muted">Проекты не найдены.</p>';
    return;
  }
  // Только админ редактирует projects_config (is_active, display_name,
  // sort_order, dodo_unit_uuid). Для остальных — read-only с подсказкой.
  const isAdmin = !!(state.me && state.me.is_admin);
  const dis = isAdmin ? '' : 'disabled';

  // <datalist> для подсказок — id юнита + читаемое имя в label.
  const dlOpts = state.dodoUnits
    .map(u => `<option value="${esc(u.id)}" label="${esc(u.name || '')}">${esc(u.name || '')}</option>`)
    .join('');

  const adminBadge = isAdmin
    ? ''
    : `<div class="muted" style="margin-bottom:8px;font-size:12px;
         background:#fef3c7;border:1px solid #fde68a;color:#92400e;
         padding:6px 10px;border-radius:6px;">
         Управление проектами доступно только администратору. Поля ниже — только для просмотра.
       </div>`;

  let html = adminBadge + `
    <datalist id="dodoUnitsList">${dlOpts}</datalist>
    <table class="dense-table projects-table">
      <thead>
        <tr>
          <th style="width:56px;text-align:center;">Вкл</th>
          <th>Название в PlanFact</th>
          <th style="width:140px;">Группа PF</th>
          <th>Отображаемое имя</th>
          <th style="width:90px;text-align:center;">Порядок</th>
          <th style="width:320px;">Dodo IS юнит</th>
        </tr>
      </thead>
      <tbody>
  `;
  state.projects.forEach(p => {
    const unitName = dodoUnitName(p.dodo_unit_uuid);
    const grp = p.project_group_title || '—';
    html += `
      <tr data-pid="${esc(p.id)}" class="${p.is_active ? '' : 'row-off'}">
        <td class="cell-center">
          <label class="switch">
            <input type="checkbox" class="js-active" ${p.is_active ? 'checked' : ''} ${dis}>
            <span class="slider"></span>
          </label>
        </td>
        <td class="cell-name">${esc(p.planfact_name || p.name)}</td>
        <td class="muted" style="font-size:11px;">${esc(grp)}</td>
        <td>
          <input type="text" class="js-display-name inp-flush"
            value="${esc(p.display_name || '')}"
            placeholder="${esc(p.planfact_name || '')}" ${dis}>
        </td>
        <td>
          <input type="number" class="js-sort inp-flush inp-center"
            value="${p.sort_order ?? ''}" step="1" placeholder="—" ${dis}>
        </td>
        <td>
          <input type="text" class="js-dodo-uuid inp-flush"
            list="dodoUnitsList"
            value="${esc(p.dodo_unit_uuid || '')}"
            placeholder="— не привязан —"
            title="${esc(unitName)}" ${dis}>
          <span class="muted js-unit-name" style="font-size:11px;">${esc(unitName)}</span>
        </td>
      </tr>
    `;
  });
  html += '</tbody></table>';
  box.innerHTML = html;

  // Не-админу хендлеры не нужны — поля уже disabled, но для чистоты не вешаем
  // обработчики в принципе.
  if (!isAdmin) return;

  box.querySelectorAll('tr[data-pid]').forEach(tr => {
    const pid = tr.dataset.pid;
    const chk = tr.querySelector('.js-active');
    chk.addEventListener('change', async () => {
      try {
        await post('/api/projects/config', { project_id: pid, is_active: chk.checked });
        tr.classList.toggle('row-off', !chk.checked);
        const p = state.projects.find(x => x.id === pid);
        if (p) p.is_active = chk.checked;
        // перерисуем матрицы, чтобы список активных обновился
        renderPnlMatrix();
        renderOpsMatrix();
        toast('Сохранено');
      } catch (e) { toast('Ошибка: ' + e.message, 'error'); chk.checked = !chk.checked; }
    });
    const nameInput = tr.querySelector('.js-display-name');
    nameInput.addEventListener('change', async () => {
      try {
        await post('/api/projects/config', { project_id: pid, display_name: nameInput.value });
        const p = state.projects.find(x => x.id === pid);
        if (p) { p.display_name = nameInput.value || null; p.name = p.display_name || p.planfact_name; }
        renderPnlMatrix(); renderOpsMatrix();
        flashOk(nameInput);
      } catch (e) { flashErr(nameInput); toast('Ошибка: ' + e.message, 'error'); }
    });
    const sortInput = tr.querySelector('.js-sort');
    sortInput.addEventListener('change', async () => {
      const v = parseInt(sortInput.value, 10);
      try {
        await post('/api/projects/config', { project_id: pid, sort_order: isNaN(v) ? null : v });
        const p = state.projects.find(x => x.id === pid);
        if (p) p.sort_order = isNaN(v) ? null : v;
        flashOk(sortInput);
      } catch (e) { flashErr(sortInput); toast('Ошибка: ' + e.message, 'error'); }
    });
    const uuidInput = tr.querySelector('.js-dodo-uuid');
    const unitLabel = tr.querySelector('.js-unit-name');
    uuidInput.addEventListener('change', async () => {
      const v = uuidInput.value.trim();
      try {
        await post('/api/projects/config', { project_id: pid, dodo_unit_uuid: v });
        const p = state.projects.find(x => x.id === pid);
        if (p) p.dodo_unit_uuid = v || null;
        const name = dodoUnitName(v);
        unitLabel.textContent = name;
        uuidInput.title = name;
        flashOk(uuidInput);
      } catch (e) { flashErr(uuidInput); toast('Ошибка: ' + e.message, 'error'); }
    });
  });
}

// ======================================================
// БЛОК 2: Цели P&L — транспонированная матрица:
//   строки = проекты (+ строка Дефолт вверху),
//   колонки = UC / LC / DC / TC
// ======================================================
// S11.8: цели могут редактировать Территориальный (≥30) и выше, плюс админ.
// На фронте дополнительно блокируем инпуты — чтобы юзер с уровнем 10 не
// получал 403 при попытке сохранить.
function canEditTargets() {
  const me = state.me || {};
  if (me.is_admin) return true;
  return (me.visibility_level || 0) >= 30;
}

function renderPnlMatrix() {
  const box = el('pnlTargetsMatrix');
  // S11.7: список метрик в матрице берём из pnl_metrics (фильтр is_target=true,
  // сортировка по sort_order). Раньше был хардкод ['UC','LC','DC','TC'] —
  // кастомные метрики (LOSSES, MGMT и т.п.) с флагом «Цель» не попадали сюда.
  const targetMetrics = (state.metrics || [])
    .filter(m => m.is_target)
    .slice()
    .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
  const active = state.projects.filter(p => p.is_active);
  const editable = canEditTargets();
  const roAttr = editable ? '' : 'disabled';
  const roTitle = editable ? ''
    : ' title="Редактировать цели может Территориальный управляющий или выше"';

  if (!targetMetrics.length) {
    const isSuper = !!(state.me && state.me.role === 'super_admin');
    const hint = isSuper
      ? 'Откройте вкладку «Платформа» → «Формулы KPI» и отметьте чекбокс «Цель» у нужных метрик.'
      : 'Целевые метрики P&L настраивает администратор платформы. Когда они появятся — здесь можно будет задать их значения.';
    box.innerHTML = `
      <div class="muted" style="padding:24px;text-align:center;max-width:560px;margin:0 auto;">
        Нет метрик с включённым флагом «Цель». ${hint}
      </div>`;
    return;
  }

  let html = `<table class="dense-table matrix-table pnl-matrix"><thead>
    <tr class="row-head-main">
      <th class="sticky-col metric-col">Пиццерия</th>`;
  targetMetrics.forEach(m => html += `
    <th class="col-proj" title="${esc(m.label || m.code)}">
      <div class="ops-h-label">${esc(m.code)}</div>
      <div class="ops-h-unit muted">${esc(m.label || '')} · %</div>
    </th>`);
  html += `</tr>
    <tr class="row-head-target">
      <td class="sticky-col metric-col target-label">Дефолт</td>`;
  targetMetrics.forEach(m => {
    const def = state.defaultTargets[m.code];
    // Placeholder в month-specific режиме = общий __default__, чтобы юзер
    // видел «текущая цель N% (используется общая)».
    const placeholderDef = (state.defaultTargetsGlobal || {})[m.code];
    html += `<td class="col-proj">
      <input type="text" class="js-def-target inp-flush inp-right target-input"
        data-metric="${esc(m.code)}"${roTitle}
        value="${def != null ? fmtPct(def) : ''}"
        placeholder="${placeholderDef != null ? fmtPct(placeholderDef) : '—'}" ${roAttr}>
    </td>`;
  });
  html += `</tr>
  </thead><tbody>`;

  if (!active.length) {
    html += `<tr><td class="sticky-col metric-col muted">Нет активных проектов</td>`;
    targetMetrics.forEach(() => html += `<td class="col-proj"></td>`);
    html += `</tr>`;
  } else {
    active.forEach(p => {
      html += `<tr data-pid="${esc(p.id)}"><td class="sticky-col metric-col">${esc(p.name)}</td>`;
      targetMetrics.forEach(m => {
        const pct = state.projectTargets[`${p.id}|${m.code}`];
        const hasOverride = pct != null;
        // Placeholder = эффективная цель из __default__: project-override (если есть)
        // или default-цель. То есть когда юзер открыл «Апрель 2026», он видит
        // в placeholder то значение, которое реально применится к этому
        // проекту на этом месяце по правилам fallback.
        const phProj = (state.projectTargetsGlobal || {})[`${p.id}|${m.code}`];
        const phDef = (state.defaultTargetsGlobal || {})[m.code];
        const placeholder = phProj != null ? phProj : phDef;
        html += `<td class="col-proj ${hasOverride ? 'has-override' : ''}">
          <input type="text" class="js-proj-target inp-flush inp-right"
            data-pid="${esc(p.id)}" data-metric="${esc(m.code)}"${roTitle}
            value="${hasOverride ? fmtPct(pct) : ''}"
            placeholder="${placeholder != null ? fmtPct(placeholder) : '—'}" ${roAttr}>
        </td>`;
      });
      html += '</tr>';
    });
  }
  html += '</tbody></table>';
  box.innerHTML = html;

  // defaults
  box.querySelectorAll('.js-def-target').forEach(inp => {
    inp.addEventListener('change', async () => {
      const m = inp.dataset.metric;
      const raw = inp.value.trim();
      const pm = state.targetsPeriod;
      try {
        if (raw === '') {
          await del(`/api/targets/defaults?metric_code=${m}&period_month=${encodeURIComponent(pm)}`);
          delete state.defaultTargets[m];
        } else {
          const v = parseNum(raw);
          if (v == null) { flashErr(inp); toast('Некорректное значение', 'error'); return; }
          const pct = v / 100;
          await post('/api/targets/defaults',
            { metric_code: m, target_pct: pct, period_month: pm });
          state.defaultTargets[m] = pct;
        }
        // обновить placeholder в проектных ячейках
        box.querySelectorAll(`.js-proj-target[data-metric="${m}"]`).forEach(p => {
          const d = state.defaultTargets[m];
          p.placeholder = d != null ? fmtPct(d) : '—';
        });
        flashOk(inp);
      } catch (e) { flashErr(inp); toast('Ошибка: ' + e.message, 'error'); }
    });
  });

  // project overrides
  box.querySelectorAll('.js-proj-target').forEach(inp => {
    inp.addEventListener('change', async () => {
      const pid = inp.dataset.pid;
      const m = inp.dataset.metric;
      const raw = inp.value.trim();
      const key = `${pid}|${m}`;
      const td = inp.closest('td');
      const pm = state.targetsPeriod;
      try {
        if (raw === '') {
          await del(`/api/targets?project_id=${pid}&metric_code=${m}&period_month=${encodeURIComponent(pm)}`);
          delete state.projectTargets[key];
          td.classList.remove('has-override');
        } else {
          const v = parseNum(raw);
          if (v == null) { flashErr(inp); toast('Некорректное значение', 'error'); return; }
          const pct = v / 100;
          await post('/api/targets',
            { project_id: pid, metric_code: m, target_pct: pct, period_month: pm });
          state.projectTargets[key] = pct;
          td.classList.add('has-override');
        }
        flashOk(inp);
      } catch (e) { flashErr(inp); toast('Ошибка: ' + e.message, 'error'); }
    });
  });
}

// ======================================================
// БЛОК 3: Ops-таргеты — транспонированная матрица по аналогии с P&L:
//   строки = проекты (+ Дефолт вверху), колонки = ops-метрики.
//   Значения в ячейке — таргет (floor). Под ним — факт из Dodo IS (ops_metrics)
//   за выбранный месяц (read-only). Окрашиваем ячейку по факт vs эффективный таргет.
// ======================================================
function opsEffectiveTarget(pid, code) {
  const ov = state.opsProjectTargets[`${pid}|${code}`];
  if (ov != null) return ov;
  const d = state.opsTargets[code];
  return d != null ? d : null;
}

function renderOpsMatrix() {
  const box = el('opsMetricsTable');
  const meta = state.opsMeta;
  const active = state.projects.filter(p => p.is_active);
  const editable = canEditTargets();
  const roAttr = editable ? '' : 'disabled';
  const roTitle = editable ? ''
    : ' title="Редактировать цели может Территориальный управляющий или выше"';

  if (!meta.length) {
    box.innerHTML = '<p class="muted">Нет ops-метрик.</p>';
    return;
  }

  // helper: количество знаков для метрики (по умолчанию 2)
  const dig = (m) => (typeof m.digits === 'number') ? m.digits : 2;
  // helper: окей ли факт vs таргет — учитывает direction
  const isOk = (m, fact, target) => {
    if (m.direction === 'lower') return fact <= target;
    return fact >= target;
  };
  // helper: подпись «зак/ч · floor» для higher и «% · ceil» для lower
  const headSub = (m) =>
    `${esc(m.unit)} · ${m.direction === 'lower' ? 'ceil' : 'floor'}`;

  let html = `<table class="dense-table matrix-table ops-matrix"><thead>
    <tr class="row-head-main">
      <th class="sticky-col metric-col">Пиццерия</th>`;
  meta.forEach(m => html += `
    <th class="col-proj" title="${esc(m.label)}">
      <div class="ops-h-label">${esc(m.label)}</div>
      <div class="ops-h-unit muted">${headSub(m)}</div>
    </th>`);
  html += `</tr>
    <tr class="row-head-target">
      <td class="sticky-col metric-col target-label">Дефолт</td>`;
  meta.forEach(m => {
    const v = state.opsTargets[m.code];
    const ph = (state.opsTargetsGlobal || {})[m.code];
    html += `<td class="col-proj">
      <input type="text" class="js-ops-def-target inp-flush inp-right target-input"
        data-code="${esc(m.code)}"${isMmss(m) && editable ? ' title="формат мм:сс, напр. 33:03"' : roTitle}
        value="${v != null ? fmtOps(m, v) : ''}"
        placeholder="${ph != null ? fmtOps(m, ph) : '—'}" ${roAttr}>
    </td>`;
  });
  html += `</tr>
  </thead><tbody>`;

  if (!active.length) {
    html += `<tr><td class="sticky-col metric-col muted">Нет активных проектов</td>`;
    meta.forEach(() => html += `<td class="col-proj"></td>`);
    html += `</tr>`;
  } else {
    active.forEach(p => {
      const values = state.opsMetrics[p.id] || {};
      html += `<tr data-pid="${esc(p.id)}"><td class="sticky-col metric-col">${esc(p.name)}</td>`;
      meta.forEach(m => {
        const override = state.opsProjectTargets[`${p.id}|${m.code}`];
        const def = state.opsTargets[m.code];
        const fact = values[m.field];
        const effTarget = override != null ? override : def;
        let cls = '';
        if (fact != null && effTarget != null) {
          cls = isOk(m, fact, effTarget) ? 'cell-ok' : 'cell-bad';
        }
        // факт + опционально количество в скобках (для процентных метрик)
        let factTxt = '';
        if (fact != null) {
          let s = 'факт: ' + esc(fmtOps(m, fact));
          if (m.count_field && values[m.count_field] != null) {
            s += ` (${esc(fmtNum(values[m.count_field], 0))})`;
          }
          factTxt = s;
        }
        // Placeholder = эффективная цель из общего __default__ (project-override
        // если есть, иначе default).
        const phProj = (state.opsProjectTargetsGlobal || {})[`${p.id}|${m.code}`];
        const phDef = (state.opsTargetsGlobal || {})[m.code];
        const placeholder = phProj != null ? phProj : phDef;
        html += `<td class="col-proj ${cls} ${override != null ? 'has-override' : ''}">
          <input type="text" class="js-ops-proj-target inp-flush inp-right"
            data-pid="${esc(p.id)}" data-code="${esc(m.code)}"${isMmss(m) && editable ? ' title="формат мм:сс, напр. 33:03"' : roTitle}
            value="${override != null ? fmtOps(m, override) : ''}"
            placeholder="${placeholder != null ? fmtOps(m, placeholder) : '—'}" ${roAttr}>
          <div class="cell-sub muted js-ops-fact" data-field="${esc(m.field)}">
            ${factTxt}
          </div>
        </td>`;
      });
      html += '</tr>';
    });
  }
  html += '</tbody></table>';
  box.innerHTML = html;

  // defaults
  box.querySelectorAll('.js-ops-def-target').forEach(inp => {
    inp.addEventListener('change', async () => {
      const code = inp.dataset.code;
      const raw = inp.value.trim();
      const m = state.opsMeta.find(x => x.code === code) || {};
      const pm = state.targetsPeriod;
      try {
        if (raw === '') {
          await del(`/api/ops-targets?metric_code=${code}&period_month=${encodeURIComponent(pm)}`);
          delete state.opsTargets[code];
        } else {
          const v = parseOps(m, raw);
          if (v == null) { flashErr(inp); toast('Некорректное значение', 'error'); return; }
          await post('/api/ops-targets',
            { metric_code: code, target_value: v, period_month: pm });
          state.opsTargets[code] = v;
          // Нормализуем отображение введённого значения (напр. «120» → «2:00»).
          inp.value = fmtOps(m, v);
        }
        // Обновить placeholder в project-ячейках этой метрики
        box.querySelectorAll(`.js-ops-proj-target[data-code="${code}"]`).forEach(p => {
          const d = state.opsTargets[code];
          p.placeholder = d != null ? fmtOps(m, d) : '—';
        });
        recolorOpsCells();
        flashOk(inp);
      } catch (e) { flashErr(inp); toast('Ошибка: ' + e.message, 'error'); }
    });
  });

  // project overrides
  box.querySelectorAll('.js-ops-proj-target').forEach(inp => {
    inp.addEventListener('change', async () => {
      const pid = inp.dataset.pid;
      const code = inp.dataset.code;
      const raw = inp.value.trim();
      const key = `${pid}|${code}`;
      const m = state.opsMeta.find(x => x.code === code) || {};
      const td = inp.closest('td');
      const pm = state.targetsPeriod;
      try {
        if (raw === '') {
          await del(`/api/ops-targets/project?project_id=${pid}&metric_code=${code}&period_month=${encodeURIComponent(pm)}`);
          delete state.opsProjectTargets[key];
          td.classList.remove('has-override');
        } else {
          const v = parseOps(m, raw);
          if (v == null) { flashErr(inp); toast('Некорректное значение', 'error'); return; }
          await post('/api/ops-targets/project',
            { project_id: pid, metric_code: code, target_value: v, period_month: pm });
          state.opsProjectTargets[key] = v;
          td.classList.add('has-override');
          inp.value = fmtOps(m, v);
        }
        recolorOpsCells();
        flashOk(inp);
      } catch (e) { flashErr(inp); toast('Ошибка: ' + e.message, 'error'); }
    });
  });
}

function recolorOpsCells() {
  const box = el('opsMetricsTable');
  box.querySelectorAll('.js-ops-proj-target').forEach(inp => {
    const pid = inp.dataset.pid;
    const code = inp.dataset.code;
    const meta = state.opsMeta.find(m => m.code === code);
    if (!meta) return;
    const effTarget = opsEffectiveTarget(pid, code);
    const fact = (state.opsMetrics[pid] || {})[meta.field];
    const td = inp.closest('td');
    td.classList.remove('cell-ok', 'cell-bad');
    if (fact != null && effTarget != null) {
      const ok = meta.direction === 'lower' ? fact <= effTarget : fact >= effTarget;
      td.classList.add(ok ? 'cell-ok' : 'cell-bad');
    }
  });
}

// ============================================================
//  ШАБЛОН СТАТЕЙ P&L — импорт из ПланФакт-экспорта (.xlsx)
// ============================================================

// state.template.nodes — то, что в БД (если шаблон сохранён). У узлов есть id.
// state.template.preview — превью после загрузки файла (id ещё нет).
// При наличии preview — показываем его (включая редактор pnl_code), кнопка
// «Сохранить» становится активной. После сохранения preview сбрасывается и
// показываются сохранённые узлы (read-only по структуре, можно править коды).

function initTemplate() {
  const fileInput = el('tplFileInput');
  const btnImport = el('btnTplImport');
  const btnSave = el('btnTplSave');
  const btnClear = el('btnTplClear');
  if (!fileInput || !btnImport || !btnSave || !btnClear) return;

  btnImport.addEventListener('click', () => fileInput.click());

  fileInput.addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    fileInput.value = '';  // чтобы повторный выбор того же файла триггерил change
    if (!file) return;
    await uploadTemplatePreview(file);
  });

  btnSave.addEventListener('click', saveTemplatePreview);
  btnClear.addEventListener('click', clearTemplate);

  // Расширенный режим: показывать колонку «P&L-код». Хранится в localStorage,
  // чтобы не сбрасывалось при перезагрузке.
  const adv = el('tplAdvancedToggle');
  if (adv) {
    adv.checked = localStorage.getItem('pnlDashboard.tplAdvanced') === '1';
    adv.addEventListener('change', () => {
      localStorage.setItem('pnlDashboard.tplAdvanced', adv.checked ? '1' : '0');
      renderTemplate();
    });
  }
}

function isTplAdvanced() {
  return localStorage.getItem('pnlDashboard.tplAdvanced') === '1';
}

async function uploadTemplatePreview(file) {
  const status = el('tplStatus');
  status.textContent = 'Парсю файл…';
  try {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/api/template/preview', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    const data = await r.json();
    state.template.preview = data.nodes || [];
    state.template.warnings = data.warnings || [];
    status.textContent = `Превью: ${data.total} узлов · листьев ${data.leaf_count} · расчётных ${data.calc_count}`;
    el('btnTplSave').disabled = false;
    renderTemplate();
    toast('Файл разобран. Проверь дерево и нажми «Сохранить шаблон».');
  } catch (err) {
    status.textContent = '';
    toast('Ошибка импорта: ' + err.message, 'error');
  }
}

async function saveTemplatePreview() {
  if (!state.template.preview) return;
  const status = el('tplStatus');
  status.textContent = 'Сохраняю…';
  try {
    const r = await fetch('/api/template', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nodes: state.template.preview }),
    });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    // Перечитываем сохранённое состояние (получим id)
    const tpl = await api('/api/template');
    state.template.nodes = tpl.nodes || [];
    state.template.preview = null;
    state.template.warnings = [];
    el('btnTplSave').disabled = true;
    status.textContent = `Шаблон сохранён · узлов: ${state.template.nodes.length}`;
    renderTemplate();
    toast('Шаблон сохранён');
  } catch (err) {
    status.textContent = '';
    toast('Ошибка сохранения: ' + err.message, 'error');
  }
}

async function clearTemplate() {
  if (!confirm('Удалить шаблон? Классификация откатится к встроенной эвристике.')) return;
  try {
    await del('/api/template');
    state.template.nodes = [];
    state.template.preview = null;
    state.template.warnings = [];
    el('btnTplSave').disabled = true;
    el('tplStatus').textContent = '';
    renderTemplate();
    toast('Шаблон очищен');
  } catch (err) {
    toast('Ошибка: ' + err.message, 'error');
  }
}

function renderTemplate() {
  const box = el('templateTree');
  if (!box) return;

  const preview = state.template.preview;
  const saved = state.template.nodes;

  // Что показываем:
  //  - preview ⇒ режим «несохранённое превью»: коды редактируются в state.preview, при «Сохранить» отправляем целиком
  //  - saved   ⇒ режим «сохранённый шаблон»: правка кода идёт через PATCH сразу
  //  - иначе   ⇒ пустой
  let mode, nodes;
  if (preview && preview.length) {
    mode = 'preview';
    nodes = preview;
  } else if (saved && saved.length) {
    mode = 'saved';
    nodes = saved;
  } else {
    box.innerHTML = '<p class="muted">Шаблон не загружен. Нажми «Импорт из ПланФакт» и выбери .xlsx.</p>';
    return;
  }

  const warns = state.template.warnings || [];
  const warnHtml = warns.length
    ? `<div class="muted" style="margin-bottom:6px;">⚠ ${warns.map(esc).join(' · ')}</div>`
    : '';

  const codeOptions = (current) => {
    const opts = [`<option value="">—</option>`];
    for (const c of TEMPLATE_CODES) {
      const sel = (c === current) ? ' selected' : '';
      opts.push(`<option value="${c}"${sel}>${c}</option>`);
    }
    return opts.join('');
  };

  const advanced = isTplAdvanced();

  const rows = nodes.map((n, idx) => {
    const indent = '&nbsp;&nbsp;'.repeat(n.depth || 0);
    const flag = n.is_calc ? '<span class="muted" title="Расчётная строка ПланФакт — не сохраняется как статья">[calc]</span>' :
                 n.is_leaf ? '<span class="muted">[лист]</span>' : '';
    const idAttr = (mode === 'saved') ? `data-id="${n.id}"` : `data-idx="${idx}"`;
    const select = n.is_calc
      ? '<span class="muted">—</span>'
      : `<select class="js-tpl-code" ${idAttr}>${codeOptions(n.pnl_code || '')}</select>`;
    // line_no — порядковый номер строки. Используется в формулах метрик [N].
    // В preview-режиме line_no ещё нет (он проставляется при сохранении на бэке).
    const lineNo = n.line_no ?? '';
    const lineNoCell = lineNo
      ? `<td class="tpl-lineno"><code>[${lineNo}]</code></td>`
      : `<td class="tpl-lineno muted">—</td>`;
    // Колонка «P&L-код» — только в расширенном режиме. По умолчанию шум,
    // мешает скроллу. Эвристика классификации работает без ручной правки
    // в 99% случаев на стандартном плане счетов Dodo.
    const codeCell = advanced
      ? `<td class="tpl-code-cell">${select}</td>`
      : '';
    return `<tr class="${n.is_calc ? 'tpl-calc' : ''}">
      ${lineNoCell}
      <td class="tpl-title">${indent}${esc(n.title)} ${flag}</td>
      ${codeCell}
      <td class="muted tpl-path">${esc(Array.isArray(n.path) ? n.path.join(' / ') : (n.path || ''))}</td>
    </tr>`;
  }).join('');

  const codeHead = advanced ? '<th>P&amp;L-код</th>' : '';
  box.innerHTML = `
    ${warnHtml}
    <table class="tpl-tree">
      <thead><tr><th class="tpl-lineno-head">№</th><th>Статья</th>${codeHead}<th class="muted">Полный путь</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  // Навешиваем хендлеры
  box.querySelectorAll('select.js-tpl-code').forEach(sel => {
    sel.addEventListener('change', (e) => {
      const newCode = e.target.value || null;
      if (mode === 'preview') {
        const idx = parseInt(e.target.dataset.idx, 10);
        if (state.template.preview[idx]) {
          state.template.preview[idx].pnl_code = newCode;
        }
        flashOk(e.target);
      } else {
        // saved → PATCH сразу
        const id = parseInt(e.target.dataset.id, 10);
        fetch(`/api/template/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pnl_code: newCode }),
        }).then(r => {
          if (!r.ok) throw new Error(r.status);
          flashOk(e.target);
          // обновляем локальное состояние
          const node = state.template.nodes.find(x => x.id === id);
          if (node) node.pnl_code = newCode;
        }).catch(err => {
          flashErr(e.target);
          toast('Ошибка сохранения кода: ' + err.message, 'error');
        });
      }
    });
  });
}

// ======================================================
// Профиль: смена пароля + список активных сессий
// ======================================================
function setMsg(id, text, kind) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('ok', 'err');
  if (!text) { el.textContent = ''; return; }
  el.textContent = text;
  el.classList.add(kind === 'ok' ? 'ok' : 'err');
}

async function renderSessions() {
  const tbody = document.querySelector('#sessionsTable tbody');
  if (!tbody) return;
  try {
    const rows = await api('/api/me/sessions');
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="muted">Сессий нет.</td></tr>';
      return;
    }
    const fmtDt = (s) => {
      try { return new Date(s).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }); }
      catch { return s; }
    };
    tbody.innerHTML = rows.map(s => `
      <tr data-token="${esc(s.token_short)}" class="${s.is_current ? 'row-current' : ''}">
        <td><code>${esc(s.token_short)}…${s.is_current ? ' <span class="muted">(текущая)</span>' : ''}</code></td>
        <td>${fmtDt(s.created_at)}</td>
        <td>${fmtDt(s.last_seen_at)}</td>
        <td>${fmtDt(s.expires_at)}</td>
        <td>${esc(s.ip || '—')}</td>
        <td title="${esc(s.user_agent || '')}" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(s.user_agent || '—')}</td>
        <td>${s.is_current ? '' : `<button class="btn-secondary js-revoke" data-token="${esc(s.token_short)}">Завершить</button>`}</td>
      </tr>
    `).join('');
    tbody.querySelectorAll('.js-revoke').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('Завершить эту сессию?')) return;
        try {
          await api(`/api/me/sessions/${btn.dataset.token}`, { method: 'DELETE' });
          await renderSessions();
          toast('Сессия завершена');
        } catch (e) { toast('Ошибка: ' + e.message, 'error'); }
      });
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="neg">Ошибка: ${esc(e.message)}</td></tr>`;
  }
}

function renderSsoLink() {
  const box = document.getElementById('ssoLinkStatus');
  if (!box) return;
  const me = state.me || {};
  const linked = !!me.dodois_linked;
  const hasPwd = me.has_password !== false;
  if (linked) {
    box.innerHTML = '<strong style="color:#16a34a;">Dodo IS привязан ✓</strong>'
      + (hasPwd ? ' <button type="button" id="ssoUnlinkBtn" class="btn-secondary" style="margin-left:auto;">Отвязать</button>' : '');
  } else {
    box.innerHTML = '<a href="/auth/link/start" class="btn-secondary">Привязать Dodo IS</a>';
  }
  const p = new URLSearchParams(location.search).get('link');
  if (p) {
    const m = {
      ok: ['Dodo IS привязан ✓', 'ok'],
      taken: ['Этот Dodo-аккаунт уже привязан к другому пользователю.', 'err'],
      invalid: ['Не удалось подтвердить Dodo IS, попробуйте снова.', 'err'],
      nosession: ['Сессия Dodo IS не найдена — войдите в Dodo IS и повторите.', 'err'],
      unavailable: ['SSO не настроен.', 'err'],
    }[p];
    if (m) setMsg('ssoLinkMsg', m[0], m[1]);
  }
  document.getElementById('ssoUnlinkBtn')?.addEventListener('click', async () => {
    try {
      await api('/auth/unlink', { method: 'POST' });
      toast('Dodo IS отвязан');
      if (state.me) state.me.dodois_linked = false;
      renderSsoLink();
    } catch (e) { setMsg('ssoLinkMsg', e.message, 'err'); }
  });
}

function renderProfilePizzerias() {
  const ul = document.getElementById('profilePizzerias');
  if (!ul) return;
  const list = (state.projects || [])
    .filter((p) => p.is_active)
    .sort((a, b) => (a.name || '').localeCompare(b.name || '', 'ru'));
  if (!list.length) {
    ul.innerHTML = '<li class="muted">Нет доступных пиццерий</li>';
    return;
  }
  ul.innerHTML = list.map((p) => `<li>${esc(p.name || p.id)}</li>`).join('');
}

function initProfileTab() {
  const form = document.getElementById('passwordForm');
  renderSsoLink();
  renderProfilePizzerias();
  if (!form) return;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    setMsg('passwordMsg', '', '');
    const cur = document.getElementById('pwdCurrent').value;
    const n1 = document.getElementById('pwdNew').value;
    const n2 = document.getElementById('pwdNew2').value;
    if (n1 !== n2) {
      setMsg('passwordMsg', 'Новые пароли не совпадают', 'err');
      return;
    }
    if (n1.length < 8) {
      setMsg('passwordMsg', 'Новый пароль должен быть минимум 8 символов', 'err');
      return;
    }
    const btn = document.getElementById('passwordSubmit');
    btn.disabled = true;
    try {
      await post('/api/me/password', { current_password: cur, new_password: n1 });
      setMsg('passwordMsg', 'Пароль обновлён. Все остальные сессии отозваны.', 'ok');
      form.reset();
      await renderSessions();  // обновим список — других сессий не должно остаться
    } catch (err) {
      setMsg('passwordMsg', err.message.replace(/^\d+:\s*/, ''), 'err');
    } finally {
      btn.disabled = false;
    }
  });
  renderSessions();
}

// ======================================================
// Интеграции (read-only для юзера; CRUD ключей и логинов — в админ-табе)
// ======================================================
async function loadIntegrationStatus() {
  try {
    const s = await api('/api/me/integrations');
    const pfName = document.getElementById('pfKeyName');
    if (pfName) pfName.textContent = s.planfact_key_name || '— не назначен —';
    const di = document.getElementById('dodoisName');
    if (di) di.textContent = s.dodois_credentials_name || '— не назначен —';
    renderSourceBlock(s);
  } catch (e) {
    console.warn('integrations status failed', e);
  }
}

// Витрина «Источник данных P&L»: Lite → предложить подключить, Full → статус.
function renderSourceBlock(s) {
  const statusEl = document.getElementById('sourceStatus');
  const connectEl = document.getElementById('sourceConnect');
  if (!statusEl || !connectEl) return;
  const kind = (s && s.source_kind) || 'lite';
  if (kind === 'planfact') {
    statusEl.innerHTML = 'Подключён источник: <strong>PlanFact</strong> — доступен полный P&L.';
    connectEl.style.display = 'none';
  } else {
    statusEl.innerHTML = 'Текущий режим: <strong>Lite</strong> — выручка и метрики из Dodo IS.';
    connectEl.style.display = 'block';
  }
}

function initIntegrationsTab() {
  const pfTest = document.getElementById('pfTestBtn');
  if (pfTest) {
    pfTest.addEventListener('click', async () => {
      setMsg('planfactMsg', 'Проверяю…', 'ok');
      try {
        const r = await post('/api/me/test-planfact', {});
        setMsg('planfactMsg', r.detail, r.ok ? 'ok' : 'err');
      } catch (err) { setMsg('planfactMsg', err.message, 'err'); }
    });
  }
  const dTest = document.getElementById('dodoisTestBtn');
  if (dTest) {
    dTest.addEventListener('click', async () => {
      setMsg('dodoisMsg', 'Проверяю…', 'ok');
      try {
        const r = await post('/api/me/test-dodois', {});
        setMsg('dodoisMsg', r.detail, r.ok ? 'ok' : 'err');
      } catch (err) { setMsg('dodoisMsg', err.message, 'err'); }
    });
  }
  loadIntegrationStatus();
}

// Статус источника в разделе «Источник P&L» (Lite/Full + число проектов) +
// подпись кнопки запуска мастера.
async function loadSourceTabStatus() {
  const box = document.getElementById('srcTabStatus');
  if (!box) return;
  try {
    const s = await api('/api/me/integrations');
    const full = (s.source_kind === 'planfact');
    const n = (state.projects || []).length;
    box.innerHTML = full
      ? `Источник: <strong>PlanFact</strong> — доступен полный P&L. Проектов: <strong>${n}</strong>.`
      : 'Текущий режим: <strong>Lite</strong> (данные из Dodo IS). Подключите P&L для полного анализа: себестоимость, ФОТ, расходы и прибыльность.';
    const btn = document.getElementById('btnOpenWizard');
    if (btn) btn.textContent = full ? 'Перенастроить P&L' : 'Настроить P&L';
  } catch (e) { box.textContent = ''; }
}

// ======================================================
// Мастер «Источник P&L» — единый онбординг (4 шага)
// ======================================================
function initSourceWizard() {
  const modal = document.getElementById('pnlWizardModal');
  if (!modal) return;
  const steps = [...modal.querySelectorAll('.wiz-step')];
  const wpanes = [...modal.querySelectorAll('.wiz-pane')];
  const ORDER = ['1', '2', '3', '4'];
  let curStep = '1';
  function showStep(n) {
    n = String(n);
    curStep = n;
    wpanes.forEach(p => { p.style.display = (p.dataset.wpane === n) ? 'block' : 'none'; });
    steps.forEach(s => {
      const active = s.dataset.wstep === n;
      s.style.background = active ? 'var(--primary)' : '#fff';
      s.style.color = active ? '#fff' : 'var(--text)';
      s.style.borderColor = active ? 'var(--primary)' : 'var(--border)';
    });
    const backBtn = document.getElementById('wizBack');
    const nextBtn = document.getElementById('wizNext');
    const idx = ORDER.indexOf(n);
    if (backBtn) backBtn.style.visibility = idx <= 0 ? 'hidden' : 'visible';
    if (nextBtn) nextBtn.textContent = idx >= ORDER.length - 1 ? 'Готово ✓' : 'Далее →';
    if (n === '2' && !state._wizUnitsLoaded) loadWizUnits();
    if (n === '3') maybeAutoImportStructure();
  }
  window._wizShowStep = showStep;
  steps.forEach(s => s.addEventListener('click', () => showStep(s.dataset.wstep)));
  document.getElementById('wizBack')?.addEventListener('click', () => {
    const i = ORDER.indexOf(curStep); if (i > 0) showStep(ORDER[i - 1]);
  });
  document.getElementById('wizNext')?.addEventListener('click', () => {
    const i = ORDER.indexOf(curStep);
    if (i >= ORDER.length - 1) { closeModal('pnlWizardModal'); loadSourceTabStatus(); return; }
    showStep(ORDER[i + 1]);
  });
  function openWizard(step) { openModal('pnlWizardModal'); showStep(step || '1'); }
  window.openPnlWizard = openWizard;
  document.getElementById('btnOpenWizard')?.addEventListener('click', () => openWizard('1'));

  // Подключение ключа прямо в модале — без перезагрузки: обновляем состояние и
  // переходим на шаг 2.
  const pfConnect = document.getElementById('srcPfConnect');
  if (pfConnect) pfConnect.addEventListener('click', async () => {
    const inp = document.getElementById('srcPfKey');
    const key = ((inp && inp.value) || '').trim();
    if (!key) { setMsg('srcPfMsg', 'Вставьте ключ PlanFact.', 'err'); return; }
    pfConnect.disabled = true;
    setMsg('srcPfMsg', 'Проверяю ключ у PlanFact…', 'ok');
    try {
      const r = await post('/api/me/source/planfact', { api_key: key });
      setMsg('srcPfMsg', `Готово! Ключ принят, проектов: ${r.projects}.`, 'ok');
      try {
        const s = await api('/api/me/integrations'); renderSourceBlock(s);
        const pr = await api('/api/projects'); state.projects = pr.projects || [];
        renderWizProjects(); renderWizMap(); loadSourceTabStatus();
      } catch (_) {}
      setTimeout(() => showStep('2'), 700);
    } catch (err) {
      setMsg('srcPfMsg', err.message, 'err');
      pfConnect.disabled = false;
    }
  });

  renderWizProjects();
  renderWizMap();
  loadSourceTabStatus();

  const autoLinkBtn = document.getElementById('wizAutoLink');
  if (autoLinkBtn) autoLinkBtn.addEventListener('click', async () => {
    const st = document.getElementById('wizUnitsStatus');
    autoLinkBtn.disabled = true; st.textContent = 'Сопоставляю по имени…';
    try {
      if (!state._wizUnitsLoaded) await loadWizUnits();
      const r = await post('/api/projects/auto-link-dodois', {});
      const pr = await api('/api/projects');
      state.projects = pr.projects || state.projects;
      st.textContent = r.summary || 'Готово';
      renderWizMap(); renderWizProjects();
    } catch (e) { st.textContent = 'Ошибка: ' + e.message; }
    finally { autoLinkBtn.disabled = false; }
  });
  const pruneBtn = document.getElementById('wizPrune');
  if (pruneBtn) pruneBtn.addEventListener('click', async () => {
    const st = document.getElementById('wizUnitsStatus');
    const unmapped = (state.projects || []).filter(p => !p.dodo_unit_uuid && p.is_active);
    if (!unmapped.length) { st.textContent = 'Несопоставленных активных проектов нет.'; return; }
    if (!confirm(`Отключить ${unmapped.length} несопоставленных проектов? Вернуть можно во вкладке «Проекты и статьи».`)) return;
    pruneBtn.disabled = true; st.textContent = 'Отсекаю…';
    try {
      for (const p of unmapped) {
        await post('/api/projects/config', { project_id: p.id, is_active: false });
        p.is_active = false;
      }
      st.textContent = `Отсечено проектов: ${unmapped.length}`;
      renderWizMap(); renderWizProjects();
    } catch (e) { st.textContent = 'Ошибка: ' + e.message; }
    finally { pruneBtn.disabled = false; }
  });

  const tplFromPfBtn = document.getElementById('wizTplFromPf');
  if (tplFromPfBtn) tplFromPfBtn.addEventListener('click', () => importStructureFromPf(false));

  const tplPreviewBtn = document.getElementById('wizTplPreview');
  if (tplPreviewBtn) tplPreviewBtn.addEventListener('click', async () => {
    const f = document.getElementById('wizTplFile').files[0];
    const st = document.getElementById('wizTplStatus');
    if (!f) { st.textContent = 'Выберите .xlsx'; return; }
    st.textContent = 'Разбираю…';
    try {
      const fd = new FormData(); fd.append('file', f);
      const r = await fetch('/api/template/preview', { method: 'POST', body: fd });
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      const data = await r.json();
      state.wizTplPreview = data.nodes || [];
      st.textContent = `Узлов: ${data.total} · листьев ${data.leaf_count} · расчётных ${data.calc_count}`;
      document.getElementById('wizTplSave').classList.remove('hidden');
      renderWizTree(state.wizTplPreview);
    } catch (e) { st.textContent = 'Ошибка: ' + e.message; }
  });
  const tplSaveBtn = document.getElementById('wizTplSave');
  if (tplSaveBtn) tplSaveBtn.addEventListener('click', async () => {
    const st = document.getElementById('wizTplStatus');
    if (!state.wizTplPreview) return;
    st.textContent = 'Сохраняю…';
    try {
      const r = await fetch('/api/template', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nodes: state.wizTplPreview }) });
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      st.textContent = 'Структура сохранена. Перейдите к шагу 4.';
      toast('Структура P&L сохранена');
    } catch (e) { st.textContent = 'Ошибка: ' + e.message; }
  });

  const seedBtn = document.getElementById('wizSeedMetrics');
  if (seedBtn) seedBtn.addEventListener('click', async () => {
    const st = document.getElementById('wizMetricsStatus');
    seedBtn.disabled = true; st.textContent = 'Генерирую формулы…';
    try {
      const r = await post('/api/metrics/seed', {});
      // Обновляем конструктор ниже (тот же state.metrics + рендер).
      try { await loadMetrics(); renderMetrics(); renderMetricsLineList(); } catch (_) {}
      st.textContent = r.count ? `Сгенерировано метрик: ${r.count}. Проверьте и правьте в конструкторе ниже.` : 'Метрики не созданы — сначала загрузите структуру (шаг 3).';
    } catch (e) { st.textContent = 'Ошибка: ' + e.message; }
    finally { seedBtn.disabled = false; }
  });

  // Авто-открытие мастера по ?wizard=1 (переход с баннера дашборда).
  try { if (new URLSearchParams(location.search).get('wizard') === '1') openWizard('1'); } catch (_) {}
}

function renderWizProjects() {
  const box = document.getElementById('wizProjects');
  if (!box) return;
  const ps = state.projects || [];
  if (!ps.length) { box.innerHTML = '<p class="muted" style="font-size:12px;">Проектов нет — подключите PlanFact выше.</p>'; return; }
  box.innerHTML = `<div class="muted" style="font-size:12px;margin-bottom:6px;">Проектов из источника: <strong>${ps.length}</strong> (✓ = привязан к Dodo)</div>` +
    '<div style="display:flex;flex-wrap:wrap;gap:6px;">' +
    ps.map(p => `<span style="border:1px solid var(--border);border-radius:6px;padding:3px 9px;font-size:12px;">${esc(p.name || p.id)}${p.dodo_unit_uuid ? ' ✓' : ''}</span>`).join('') +
    '</div>';
}

// Инверсия: строки — заведения Dodo (первичны), в каждой дропдаун проекта
// PlanFact. Одно заведение = один проект (при выборе чистим прежнюю привязку
// как заведения, так и проекта). Несопоставленные проекты не мешают.
function _wizNorm(s) { return (s || '').toLowerCase().replace(/[-\s_]/g, ''); }

function renderWizMap() {
  const box = document.getElementById('wizMapTable');
  if (!box) return;
  const units = state.dodoUnits || [];
  const projs = state.projects || [];
  if (!units.length) {
    box.innerHTML = '<p class="muted" style="font-size:12px;">Загружаю заведения…</p>';
    return;
  }
  const projByUuid = {};
  projs.forEach(p => { if (p.dodo_unit_uuid) projByUuid[_wizNorm(p.dodo_unit_uuid)] = p; });
  const mappedCount = Object.keys(projByUuid).length;
  box.innerHTML =
    `<div class="muted" style="font-size:12px;margin-bottom:6px;">Заведений Dodo: <strong>${units.length}</strong> · сопоставлено: <strong>${mappedCount}</strong></div>` +
    '<table class="dense-table"><thead><tr><th>Заведение Dodo</th><th>Проект PlanFact</th><th></th></tr></thead><tbody>' +
    units.map(u => {
      const linked = projByUuid[_wizNorm(u.id)];
      const optionsHtml = ['<option value="">— не задан —</option>']
        .concat(projs.map(p => `<option value="${esc(p.id)}" ${linked && linked.id === p.id ? 'selected' : ''}>${esc(p.name || p.id)}</option>`))
        .join('');
      return `<tr data-uuid="${esc(u.id)}"><td>${esc(u.name)}</td>` +
        `<td><select class="wiz-proj" style="font-size:12px;min-width:240px;max-width:100%;">${optionsHtml}</select></td>` +
        `<td>${linked ? '<span style="color:green;">✓</span>' : ''}</td></tr>`;
    }).join('') +
    '</tbody></table>';
  box.querySelectorAll('.wiz-proj').forEach(sel => {
    sel.addEventListener('change', async () => {
      const uuid = sel.closest('tr').dataset.uuid;
      const pid = sel.value;
      try {
        // 1:1 — снять привязку у другого проекта, что стоял на этом заведении,
        // и снять прежнее заведение у выбранного проекта.
        for (const p of (state.projects || [])) {
          if (p.dodo_unit_uuid && _wizNorm(p.dodo_unit_uuid) === _wizNorm(uuid) && p.id !== pid) {
            await post('/api/projects/config', { project_id: p.id, dodo_unit_uuid: '' });
            p.dodo_unit_uuid = null;
          }
        }
        if (pid) {
          await post('/api/projects/config', { project_id: pid, dodo_unit_uuid: uuid, is_active: true });
          const p = (state.projects || []).find(x => x.id === pid);
          if (p) { p.dodo_unit_uuid = uuid; p.is_active = true; }
        }
        toast('Сопоставление сохранено');
        renderWizMap(); renderWizProjects();
      } catch (e) { toast('Ошибка: ' + e.message, 'error'); }
    });
  });
}

async function loadWizUnits() {
  const st = document.getElementById('wizUnitsStatus');
  try {
    const res = await api('/api/dodois/units');
    state.dodoUnits = res.units || [];
    state._wizUnitsLoaded = true;
    if (st) st.textContent = `Заведений Dodo: ${state.dodoUnits.length}`;
    renderWizMap();
  } catch (e) { if (st) st.textContent = 'Не удалось загрузить заведения: ' + e.message; }
}

async function importStructureFromPf(auto) {
  const st = document.getElementById('wizTplStatus');
  const btn = document.getElementById('wizTplFromPf');
  if (btn) btn.disabled = true;
  if (st) st.textContent = auto ? 'Импортирую структуру из PlanFact…' : 'Обновляю структуру из PlanFact…';
  try {
    const data = await post('/api/template/import-from-planfact', {});
    state.wizTplPreview = data.nodes || [];
    if (st) st.textContent = `Импортировано: ${data.total} статей (листьев ${data.leaf_count}). Проверьте уровни и сохраните.`;
    const save = document.getElementById('wizTplSave'); if (save) save.classList.remove('hidden');
    renderWizTree(state.wizTplPreview);
  } catch (e) { if (st) st.textContent = 'Ошибка импорта: ' + e.message; }
  finally { if (btn) btn.disabled = false; }
}

// Шаг 3 при входе: превью в работе → показать; сохранённый шаблон → статус;
// свежий тенант → авто-импорт из PlanFact.
function maybeAutoImportStructure() {
  if (state.wizTplPreview) { renderWizTree(state.wizTplPreview); return; }
  if (state.template && state.template.nodes && state.template.nodes.length) {
    const st = document.getElementById('wizTplStatus');
    if (st) st.textContent = `Структура загружена: ${state.template.nodes.length} статей. «Обновить структуру» — перечитать из PlanFact.`;
    return;
  }
  importStructureFromPf(true);
}

function renderWizTree(nodes) {
  const box = document.getElementById('wizTplTree');
  if (!box) return;
  const CODES = ['', 'REVENUE', 'UC', 'LC', 'DC', 'RENT', 'MARKETING', 'FRANCHISE', 'MGMT', 'OTHER_OPEX', 'OTHER_INCOME', 'TAX', 'INTEREST', 'DIVIDENDS'];
  box.innerHTML = '<table class="dense-table"><thead><tr><th>Статья</th><th style="width:150px;">Уровень</th></tr></thead><tbody>' +
    nodes.map((n, i) => {
      const pad = (n.depth || 0) * 16;
      const sel = CODES.map(c => `<option value="${c}" ${((n.pnl_code || '') === c) ? 'selected' : ''}>${c || '—'}</option>`).join('');
      return `<tr><td style="padding-left:${pad}px;">${esc(n.title)}</td>` +
        `<td><select data-i="${i}" class="wiz-code" style="font-size:12px;">${sel}</select></td></tr>`;
    }).join('') + '</tbody></table>';
  box.querySelectorAll('.wiz-code').forEach(sel => {
    sel.addEventListener('change', () => {
      const i = parseInt(sel.dataset.i, 10);
      if (state.wizTplPreview && state.wizTplPreview[i]) state.wizTplPreview[i].pnl_code = sel.value || null;
    });
  });
}

function renderWizMetrics(metrics) {
  const box = document.getElementById('wizMetricsList');
  if (!box) return;
  if (!metrics.length) { box.innerHTML = '<p class="muted" style="font-size:12px;">Пока нет метрик.</p>'; return; }
  box.innerHTML = '<table class="dense-table"><thead><tr><th>Код</th><th>Название</th><th>Формула</th></tr></thead><tbody>' +
    metrics.map(m => `<tr><td><code>${esc(m.code)}</code></td><td>${esc(m.label || '')}</td><td><code>${esc(m.formula || '')}</code></td></tr>`).join('') +
    '</tbody></table>';
}

// ======================================================
// Пользователи + каталог PlanFact-ключей + Dodo IS логины (admin only)
// ======================================================

// Загружается на старте админ-таба, кэшируется в state.adminCatalogs
// и переиспользуется в селектах модалок создания/редактирования.
async function loadAdminCatalogs() {
  const [keysR, dodoisR] = await Promise.allSettled([
    api('/api/admin/planfact-keys'),
    api('/api/admin/dodois-credentials'),
  ]);
  state.adminCatalogs = {
    pfKeys: keysR.status === 'fulfilled' ? keysR.value : [],
    dodoisLogins: dodoisR.status === 'fulfilled' ? dodoisR.value : [],
  };
}

function fillSelect(selectEl, options, currentValue, valueKey, labelFn, placeholder) {
  if (!selectEl) return;
  const opts = [`<option value="">${placeholder}</option>`];
  for (const o of options) {
    const v = o[valueKey];
    const sel = String(v) === String(currentValue ?? '') ? 'selected' : '';
    opts.push(`<option value="${esc(String(v))}" ${sel}>${esc(labelFn(o))}</option>`);
  }
  selectEl.innerHTML = opts.join('');
}

async function renderPfKeysTable() {
  const tbody = document.querySelector('#pfKeysTable tbody');
  if (!tbody) return;
  let keys;
  try {
    keys = await api('/api/admin/planfact-keys');
    state.adminCatalogs = state.adminCatalogs || {};
    state.adminCatalogs.pfKeys = keys;
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="neg">Ошибка: ${esc(e.message)}</td></tr>`;
    return;
  }
  if (!keys.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="muted">Каталог пуст. Добавьте первый ключ.</td></tr>';
    return;
  }
  tbody.innerHTML = keys.map(k => `
    <tr data-id="${k.id}">
      <td><strong>${esc(k.name)}</strong></td>
      <td><code style="font-size:11px;">${esc(k.api_key_masked)}</code></td>
      <td class="muted">${esc(k.note || '—')}</td>
      <td>${k.used_by_count > 0 ? k.used_by_count + ' юзер(ов)' : '<span class="muted">—</span>'}</td>
      <td>
        <button class="btn-secondary js-edit-pfkey" data-id="${k.id}">Изменить</button>
        <button class="btn-secondary js-key-projects" data-id="${k.id}" data-name="${esc(k.name)}" style="margin-left:4px;">Проекты</button>
        <button class="btn-secondary js-key-cache" data-id="${k.id}" data-name="${esc(k.name)}" data-lmw="${k.live_months_window || 2}" style="margin-left:4px;">Кэш</button>
      </td>
    </tr>
  `).join('');
  tbody.querySelectorAll('.js-edit-pfkey').forEach(btn => {
    btn.addEventListener('click', () => {
      const k = keys.find(x => x.id === Number(btn.dataset.id));
      if (k) openPfKeyModal(k);
    });
  });
  tbody.querySelectorAll('.js-key-projects').forEach(btn => {
    btn.addEventListener('click', () => {
      openKeyProjectsModal(Number(btn.dataset.id), btn.dataset.name);
    });
  });
  tbody.querySelectorAll('.js-key-cache').forEach(btn => {
    btn.addEventListener('click', () => {
      openCacheModal(
        Number(btn.dataset.id),
        btn.dataset.name,
        Number(btn.dataset.lmw) || 2,
      );
    });
  });
}

// --- Cache history modal (S3.5) ---
async function openCacheModal(keyId, keyName, currentLmw) {
  document.getElementById('cacheTitle').textContent = `Кэш — ${keyName}`;
  document.getElementById('cacheLmw').value = currentLmw;
  state.cacheKeyId = keyId;
  document.getElementById('cacheTableWrap').innerHTML = '<p class="muted">Загрузка…</p>';
  openModal('cacheModal');
  await renderCacheTable(keyId);
}

async function renderCacheTable(keyId) {
  const wrap = document.getElementById('cacheTableWrap');
  let rows;
  try {
    rows = await api(`/api/admin/planfact-keys/${keyId}/cache`);
  } catch (e) {
    wrap.innerHTML = `<p class="neg">Ошибка: ${esc(e.message)}</p>`;
    return;
  }
  if (!rows.length) {
    wrap.innerHTML = '<p class="muted">Замороженных месяцев пока нет. Они появятся при первом запросе закрытого месяца.</p>';
    return;
  }
  const fmtDt = (s) => {
    if (!s) return '—';
    try { return new Date(s).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }); }
    catch { return s; }
  };
  wrap.innerHTML = `
    <table class="dense-table" style="width:100%;">
      <thead>
        <tr>
          <th style="text-align:left;">Месяц</th>
          <th style="text-align:left;">Заморожен</th>
          <th style="text-align:left;">Кем</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td><strong>${esc(r.period_month)}</strong></td>
            <td>${esc(fmtDt(r.frozen_at))}</td>
            <td class="muted">${esc(r.frozen_by_username || '—')}</td>
            <td>
              <button class="btn-secondary js-cache-reopen" data-pm="${esc(r.period_month)}">
                Переоткрыть
              </button>
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
  wrap.querySelectorAll('.js-cache-reopen').forEach(btn => {
    btn.addEventListener('click', async () => {
      const pm = btn.dataset.pm;
      if (!confirm(`Переоткрыть месяц ${pm}? Снэпшот будет удалён, при следующем запросе пересоберётся из PlanFact.`)) return;
      try {
        await api(
          `/api/admin/planfact-keys/${keyId}/cache/${pm}`,
          { method: 'DELETE' },
        );
        toast(`Месяц ${pm} переоткрыт`);
        await renderCacheTable(keyId);
      } catch (e) {
        toast(`Ошибка: ${e.message}`);
      }
    });
  });
}

function initCacheModal() {
  document.getElementById('cacheLmwSave')?.addEventListener('click', async () => {
    const keyId = state.cacheKeyId;
    if (!keyId) return;
    const v = Number(document.getElementById('cacheLmw').value);
    if (!Number.isInteger(v) || v < 1 || v > 24) {
      toast('Окно должно быть целым числом от 1 до 24');
      return;
    }
    try {
      await api(`/api/admin/planfact-keys/${keyId}/live-months-window`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ live_months_window: v }),
      });
      toast(`Live-окно: ${v} мес.`);
      await renderPfKeysTable();  // обновим data-lmw на кнопке Кэш
    } catch (e) {
      toast(`Ошибка: ${e.message}`);
    }
  });
}

function openPfKeyModal(existing) {
  const isEdit = !!existing;
  document.getElementById('pkTitle').textContent = isEdit ? 'Изменить PF-ключ' : 'Новый PF-ключ';
  document.getElementById('pkId').value = existing?.id || '';
  document.getElementById('pkName').value = existing?.name || '';
  document.getElementById('pkApiKey').value = '';
  document.getElementById('pkApiKeyHint').textContent = isEdit
    ? `Текущий: ${existing.api_key_masked}. Оставьте поле пустым чтобы не менять.`
    : '';
  document.getElementById('pkApiKey').required = !isEdit;
  document.getElementById('pkNote').value = existing?.note || '';
  document.getElementById('pkDelete').style.display = isEdit ? '' : 'none';
  setMsg('pkMsg', '', '');
  openModal('pfKeyModal');
}

function initPfKeysCatalog() {
  document.getElementById('btnCreatePfKey')?.addEventListener('click', () => openPfKeyModal(null));

  document.getElementById('pfKeyForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    setMsg('pkMsg', '', '');
    const id = document.getElementById('pkId').value;
    const name = document.getElementById('pkName').value.trim();
    const apiKey = document.getElementById('pkApiKey').value.trim();
    const note = document.getElementById('pkNote').value.trim();
    try {
      if (id) {
        const body = { name, note: note || null };
        if (apiKey) body.api_key = apiKey;
        await api(`/api/admin/planfact-keys/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      } else {
        if (!apiKey) { setMsg('pkMsg', 'Укажите api_key', 'err'); return; }
        await post('/api/admin/planfact-keys', { name, api_key: apiKey, note: note || null });
      }
      closeModal('pfKeyModal');
      toast('Сохранено');
      await renderPfKeysTable();
      await renderUsersTable();  // обновим колонку «PF KEY»
    } catch (err) {
      setMsg('pkMsg', err.message, 'err');
    }
  });

  document.getElementById('pkDelete').addEventListener('click', async () => {
    const id = document.getElementById('pkId').value;
    const name = document.getElementById('pkName').value;
    if (!confirm(`Удалить PF-ключ «${name}»?`)) return;
    try {
      await api(`/api/admin/planfact-keys/${id}`, { method: 'DELETE' });
      closeModal('pfKeyModal');
      toast('Ключ удалён');
      await renderPfKeysTable();
    } catch (err) {
      setMsg('pkMsg', err.message, 'err');
    }
  });

  renderPfKeysTable();
}
// UX-3: маппинг visibility_level → человекочитаемая должность.
// Должно совпадать с пресетами в openEditUserModal/openCreateUserModal.
const VISIBILITY_LABEL = {
  10: 'Менеджер пиццерии',
  30: 'Территориальный',
  60: 'Директор',
  100: 'Партнёр',
};
function visibilityLabel(level) {
  if (level == null) return '—';
  if (VISIBILITY_LABEL[level]) return VISIBILITY_LABEL[level];
  // нестандартный уровень — отображаем число
  return String(level);
}

async function renderUsersTable() {
  const tbody = document.querySelector('#usersTable tbody');
  if (!tbody) return;
  try {
    const users = await api('/api/admin/users');
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="muted">Пользователей нет.</td></tr>';
      return;
    }
    const fmtDt = (s) => {
      try { return new Date(s).toLocaleDateString('ru-RU'); }
      catch { return s; }
    };
    tbody.innerHTML = users.map(u => `
      <tr data-id="${u.id}">
        <td>${u.id}</td>
        <td><strong>${esc(u.username)}</strong></td>
        <td>${esc(u.display_name || '—')}</td>
        <td>${
          u.role === 'super_admin' ? '<span title="Супер-админ">★ супер</span>' :
          u.role === 'network_admin' ? '<span title="Сетевой админ">▲ сеть</span>' :
          '<span class="muted">—</span>'
        }</td>
        <td>${esc(visibilityLabel(u.visibility_level))}</td>
        <td>${esc(u.dodois_credentials_name || '—')}</td>
        <td>${u.planfact_key_name ? esc(u.planfact_key_name) : '<span class="muted">— не назначен —</span>'}</td>
        <td>${fmtDt(u.created_at)}</td>
        <td style="display:flex;gap:6px;">
          <button class="btn-secondary js-edit-user" data-id="${u.id}">Изменить</button>
          <button class="btn-secondary js-user-projects" data-id="${u.id}" data-username="${esc(u.username)}">Проекты</button>
        </td>
      </tr>
    `).join('');
    tbody.querySelectorAll('.js-edit-user').forEach(btn => {
      btn.addEventListener('click', () => {
        const u = users.find(x => x.id === Number(btn.dataset.id));
        if (u) openEditUserModal(u);
      });
    });
    tbody.querySelectorAll('.js-user-projects').forEach(btn => {
      btn.addEventListener('click', () => {
        openUserProjectsModal(Number(btn.dataset.id), btn.dataset.username);
      });
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="neg">Ошибка: ${esc(e.message)}</td></tr>`;
  }
}

function openModal(id) {
  document.getElementById(id)?.classList.remove('hidden');
}
function closeModal(id) {
  document.getElementById(id)?.classList.add('hidden');
}

function openCreateUserModal() {
  document.getElementById('createUserForm').reset();
  // Заполняем селекты из каталогов
  const cats = state.adminCatalogs || { pfKeys: [], dodoisLogins: [] };
  fillSelect(
    document.getElementById('cuPfKeyId'), cats.pfKeys, '',
    'id', k => `${k.name} — ${k.api_key_masked}` + (k.note ? ` (${k.note})` : ''),
    '— не назначен —',
  );
  fillSelect(
    document.getElementById('cuDodoisName'), cats.dodoisLogins, '',
    'name', d => d.name + (d.email ? ` (${d.email})` : ''),
    '— не выбран —',
  );
  setMsg('cuMsg', '', '');
  openModal('userCreateModal');
}

function openEditUserModal(u) {
  document.getElementById('ueId').value = u.id;
  document.getElementById('ueUsername').value = u.username;
  document.getElementById('ueDisplayName').value = u.display_name || '';
  document.getElementById('ueRole').value = u.role || 'user';
  // visibility_level — приведение к ближайшему пресету (10/30/60/100)
  const lvl = u.visibility_level ?? 100;
  document.getElementById('ueVisibilityLevel').value = String(lvl);
  const cats = state.adminCatalogs || { pfKeys: [], dodoisLogins: [] };
  fillSelect(
    document.getElementById('uePfKeyId'), cats.pfKeys, u.planfact_key_id ?? '',
    'id', k => `${k.name} — ${k.api_key_masked}` + (k.note ? ` (${k.note})` : ''),
    '— не назначен —',
  );
  fillSelect(
    document.getElementById('ueDodoisName'), cats.dodoisLogins, u.dodois_credentials_name ?? '',
    'name', d => d.name + (d.email ? ` (${d.email})` : ''),
    '— не выбран —',
  );
  setMsg('ueMsg', '', '');
  openModal('userEditModal');
}

function showGeneratedPassword(pwd) {
  document.getElementById('generatedPwd').textContent = pwd;
  openModal('passwordShownModal');
}

// ====================================================================
// Модалка «Проекты юзера» — упрощённая, только видимость per-user.
// Структурные поля (имя/порядок/dodo_unit) — в openKeyProjectsModal
// (открывается из каталога PF-ключей в табе «Интеграции»).
// ====================================================================
async function openUserProjectsModal(userId, username) {
  document.getElementById('upTitle').textContent = `Проекты: ${username}`;
  // Упрощённая модалка — без `.wide`, ширина по дефолту (~560px)
  document.getElementById('upModalContent')?.classList.remove('wide');
  const wrap = document.getElementById('upTableWrap');
  wrap.innerHTML = '<p class="muted">Загрузка…</p>';
  openModal('userProjectsModal');

  let resp;
  try {
    resp = await api(`/api/admin/users/${userId}/visibility`);
  } catch (e) {
    wrap.innerHTML = `<p class="neg">Ошибка: ${esc(e.message)}</p>`;
    return;
  }
  if (resp.message && (!resp.projects || !resp.projects.length)) {
    wrap.innerHTML = `<p class="muted" style="padding:14px;background:#fef3c7;border:1px solid #fde68a;border-radius:6px;color:#92400e;">${esc(resp.message)}</p>`;
    return;
  }
  const projects = resp.projects || [];
  if (!projects.length) {
    wrap.innerHTML = '<p class="muted">Под этим PF-ключом нет ни одного проекта.</p>';
    return;
  }
  // Группируем по project_group_title — внутри группы видимые сверху.
  const groups = _groupProjectsForModal(projects, p => p.is_visible);

  wrap.innerHTML = `
    <p class="muted" style="font-size:11px;margin-bottom:8px;">
      Снимите тумблер у проекта, который этот юзер не должен видеть на дашборде.
      Имена / порядок / привязки к Dodo IS — общие для всех юзеров под одним
      PlanFact-ключом и редактируются в табе «Интеграции» → каталог ключей →
      «Проекты».
    </p>
    <table class="dense-table">
      <thead><tr>
        <th style="width:60px;text-align:center;">Вкл</th>
        <th>PlanFact-имя</th>
      </tr></thead>
      <tbody>
        ${groups.map(g => _renderGroupRows(
          g,
          p => ({ rowClass: p.is_visible ? '' : 'row-off', checked: p.is_visible }),
          1,
        )).join('')}
      </tbody>
    </table>
  `;

  _wireGroupCheckboxes(wrap, async (pid, target) => {
    await api(`/api/admin/users/${userId}/visibility/${pid}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_visible: target }),
    });
  });
}


// Группировщик + рендер строки группы — переиспользуется для модалок
// «юзер» и «ключ». Возвращает отсортированный массив групп.
function _groupProjectsForModal(projects, includedFn) {
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
  const groups = [...buckets.values()];
  groups.sort((a, b) => {
    if (a.is_undistributed !== b.is_undistributed) return a.is_undistributed ? 1 : -1;
    if (a.title === 'Текущий бизнес') return -1;
    if (b.title === 'Текущий бизнес') return 1;
    return a.title.localeCompare(b.title, 'ru');
  });
  groups.forEach(g => g.projects.sort((a, b) => {
    const aOn = !!includedFn(a), bOn = !!includedFn(b);
    if (aOn !== bOn) return aOn ? -1 : 1;
    return (a.planfact_name || '').localeCompare(b.planfact_name || '', 'ru');
  }));
  return groups;
}


// Рендер строки группы + её проектов. perProjFn возвращает
// {rowClass, checked} для тумблера; restColumnsHtml — что положить в
// правые колонки (для модалки ключа — поля display/sort/dodo_unit).
// otherCols — сколько колонок занимает заголовок группы (через colspan).
function _renderGroupRows(g, perProjFn, otherCols, restColumnsHtml = () => '') {
  const onN = g.projects.filter(p => perProjFn(p).checked).length;
  const total = g.projects.length;
  const allOn = onN === total;
  const noneOn = onN === 0;
  return `
    <tr class="up-group-head" data-group="${esc(g.title)}">
      <td class="cell-center">
        <label class="switch" title="Включить/выключить всю группу">
          <input type="checkbox" class="js-up-grp-toggle"
                 ${allOn ? 'checked' : ''}
                 ${(!allOn && !noneOn) ? 'data-indeterminate="1"' : ''}>
          <span class="slider"></span>
        </label>
      </td>
      <td colspan="${otherCols}">
        <strong>${esc(g.title)}</strong>
        <span class="muted" style="font-size:11px;margin-left:6px;">${onN}/${total} включено</span>
      </td>
    </tr>
    ${g.projects.map(p => {
      const cell = perProjFn(p);
      return `
        <tr data-pid="${esc(p.id)}" data-group="${esc(g.title)}" class="up-row ${cell.rowClass}">
          <td class="cell-center">
            <label class="switch">
              <input type="checkbox" class="js-up-toggle" ${cell.checked ? 'checked' : ''}>
              <span class="slider"></span>
            </label>
          </td>
          <td>
            <strong>${esc(p.planfact_name)}</strong>
            <div class="muted" style="font-size:10px;">${esc(p.id)}${p.planfact_active ? '' : ' · архив в PF'}</div>
          </td>
          ${restColumnsHtml(p)}
        </tr>
      `;
    }).join('')}
  `;
}


// Развешивает обработчики на чекбоксы группы и проектов. onToggle(pid, target)
// должна делать PATCH и кидать exception при ошибке (UI откатим).
function _wireGroupCheckboxes(wrap, onToggle) {
  function refreshGroupHeader(grpTitle) {
    const groupRow = wrap.querySelector(`tr.up-group-head[data-group="${CSS.escape(grpTitle)}"]`);
    if (!groupRow) return;
    const rows = wrap.querySelectorAll(`tr.up-row[data-group="${CSS.escape(grpTitle)}"]`);
    const total = rows.length;
    const onN = [...rows].filter(r => r.querySelector('.js-up-toggle').checked).length;
    const cb = groupRow.querySelector('.js-up-grp-toggle');
    cb.checked = onN === total;
    cb.indeterminate = onN > 0 && onN < total;
    const lbl = groupRow.querySelector('.muted');
    if (lbl) lbl.textContent = `${onN}/${total} включено`;
  }
  wrap.querySelectorAll('input.js-up-grp-toggle[data-indeterminate="1"]').forEach(cb => {
    cb.indeterminate = true;
  });
  wrap.querySelectorAll('input.js-up-grp-toggle').forEach(cb => {
    cb.addEventListener('change', async () => {
      const grpTitle = cb.closest('tr').dataset.group;
      const target = cb.checked;
      const rows = wrap.querySelectorAll(`tr.up-row[data-group="${CSS.escape(grpTitle)}"]`);
      const toFlip = [...rows].filter(r => r.querySelector('.js-up-toggle').checked !== target);
      for (const tr of toFlip) {
        const pid = tr.dataset.pid;
        const innerCb = tr.querySelector('.js-up-toggle');
        try {
          await onToggle(pid, target);
          innerCb.checked = target;
          tr.classList.toggle('row-off', !target);
        } catch (e) { /* пропускаем — идём дальше */ }
      }
      refreshGroupHeader(grpTitle);
    });
  });
  wrap.querySelectorAll('tr.up-row').forEach(tr => {
    const pid = tr.dataset.pid;
    const chk = tr.querySelector('.js-up-toggle');
    chk.addEventListener('change', async () => {
      try {
        await onToggle(pid, chk.checked);
        tr.classList.toggle('row-off', !chk.checked);
        refreshGroupHeader(tr.dataset.group);
      } catch (e) {
        chk.checked = !chk.checked;
        toast('Ошибка: ' + e.message, 'error');
      }
    });
  });
}


// ====================================================================
// Модалка «Проекты ключа» — структурные настройки (is_active, display_name,
// sort_order, dodo_unit_uuid). Применяется ко всем юзерам этого ключа.
// Открывается из каталога PF-ключей в табе «Интеграции».
// ====================================================================
async function openKeyProjectsModal(keyId, keyName) {
  document.getElementById('upTitle').textContent = `Проекты ключа: ${keyName}`;
  // Полная модалка с 5 колонками — нужен широкий вариант
  document.getElementById('upModalContent')?.classList.add('wide');
  const wrap = document.getElementById('upTableWrap');
  wrap.innerHTML = '<p class="muted">Загрузка…</p>';
  openModal('userProjectsModal');

  let projectsResp, unitsResp;
  try {
    [projectsResp, unitsResp] = await Promise.all([
      api(`/api/admin/planfact-keys/${keyId}/projects`),
      api(`/api/admin/planfact-keys/${keyId}/dodois-units`).catch(e => ({ units: [], message: e.message })),
    ]);
  } catch (e) {
    wrap.innerHTML = `<p class="neg">Ошибка: ${esc(e.message)}</p>`;
    return;
  }
  if (projectsResp.message && (!projectsResp.projects || !projectsResp.projects.length)) {
    wrap.innerHTML = `<p class="muted" style="padding:14px;background:#fef3c7;border:1px solid #fde68a;border-radius:6px;color:#92400e;">${esc(projectsResp.message)}</p>`;
    return;
  }
  const projects = projectsResp.projects || [];
  const units = unitsResp.units || [];
  if (!projects.length) {
    wrap.innerHTML = '<p class="muted">Под этим PF-ключом нет ни одного проекта.</p>';
    return;
  }

  const unitDatalistId = `dodoUnitsForKey_${keyId}`;
  const dlOpts = units
    .map(u => `<option value="${esc(u.id)}" label="${esc(u.name || '')}">${esc(u.name || '')}</option>`)
    .join('');
  const unitName = (uuid) => {
    if (!uuid) return '';
    const u = units.find(x => (x.id || '').toLowerCase() === uuid.toLowerCase());
    return u ? u.name : '';
  };

  const dodoisHint = unitsResp.message
    ? `<p class="muted" style="font-size:11px;margin-bottom:8px;">Dodo IS юниты недоступны: ${esc(unitsResp.message)}</p>`
    : `<p class="muted" style="font-size:11px;margin-bottom:8px;">Снятый тумблер «Вкл» = проект исключён из этой сети: не показывается на дашборде и недоступен сетевому админу для назначения юзерам. Привязка к Dodo IS — выберите юнит из списка.</p>`;

  // Один тумблер «Вкл» = проект включён в этой сети
  // (is_active + is_admin_managed синхронно). Если выключить — проект
  // исчезает и из дашборда (архив), и из списка доступных сети для NA.
  const groups = _groupProjectsForModal(projects, p => p.is_active && p.is_admin_managed);

  wrap.innerHTML = `
    ${dodoisHint}
    ${units.length ? `<div style="margin-bottom:8px;"><button type="button" class="btn-secondary js-up-autolink">Автопривязка по имени</button></div>` : ''}
    <datalist id="${unitDatalistId}">${dlOpts}</datalist>
    <table class="dense-table">
      <thead><tr>
        <th style="width:60px;text-align:center;" title="Проект включён в этой сети: виден на дашборде + доступен сетевому админу">Вкл</th>
        <th>PlanFact-имя</th>
        <th style="width:200px;">Отображаемое имя</th>
        <th style="width:90px;text-align:center;">Порядок</th>
        <th style="width:280px;">Dodo IS юнит</th>
      </tr></thead>
      <tbody>
        ${groups.map(g => _renderGroupRows(
          g,
          p => {
            const on = p.is_active && p.is_admin_managed;
            return { rowClass: on ? '' : 'row-off', checked: on };
          },
          4,
          p => `
            <td>
              <input type="text" class="js-up-display inp-flush"
                value="${esc(p.display_name || '')}"
                placeholder="${esc(p.planfact_name || '')}">
            </td>
            <td>
              <input type="number" class="js-up-sort inp-flush inp-center"
                value="${p.sort_order ?? ''}" step="1" placeholder="—">
            </td>
            <td>
              <input type="text" class="js-up-uuid inp-flush"
                list="${unitDatalistId}"
                value="${esc(p.dodo_unit_uuid || '')}"
                placeholder="— не привязан —"
                title="${esc(unitName(p.dodo_unit_uuid))}">
              <span class="muted js-up-uname" style="font-size:10px;">${esc(unitName(p.dodo_unit_uuid))}</span>
            </td>
          `,
        )).join('')}
      </tbody>
    </table>
  `;

  async function patchField(pid, body, inputEl) {
    try {
      await api(`/api/admin/planfact-keys/${keyId}/projects/${pid}/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (inputEl) flashOk(inputEl);
    } catch (e) {
      if (inputEl) flashErr(inputEl);
      toast('Ошибка: ' + e.message, 'error');
      throw e;
    }
  }

  // Тумблер «Вкл» пишет ОБА флага в lock-step: is_active (показ на дашборде)
  // + is_admin_managed (доступ для сетевого админа). Один клик = «проект
  // включён в сети» / «исключён из сети».
  _wireGroupCheckboxes(wrap, async (pid, target) => {
    await patchField(pid, { is_active: target, is_admin_managed: target });
  });

  wrap.querySelectorAll('tr.up-row').forEach(tr => {
    const pid = tr.dataset.pid;
    const dn = tr.querySelector('.js-up-display');
    if (dn) dn.addEventListener('change', () => patchField(pid, { display_name: dn.value }, dn));
    const so = tr.querySelector('.js-up-sort');
    if (so) so.addEventListener('change', () => {
      const v = parseInt(so.value, 10);
      patchField(pid, { sort_order: isNaN(v) ? null : v }, so);
    });
    const uu = tr.querySelector('.js-up-uuid');
    const un = tr.querySelector('.js-up-uname');
    if (uu) uu.addEventListener('change', async () => {
      const v = uu.value.trim();
      try {
        await patchField(pid, { dodo_unit_uuid: v }, uu);
        const name = unitName(v);
        un.textContent = name; uu.title = name;
      } catch (e) {}
    });
  });

  const autoBtn = wrap.querySelector('.js-up-autolink');
  if (autoBtn) {
    autoBtn.addEventListener('click', async () => {
      autoBtn.disabled = true;
      const norm = (s) => (s || '').toLowerCase().replace(/[\s\-_ ]+/g, '');
      const unitsByName = new Map();
      units.forEach(u => unitsByName.set(norm(u.name), u));
      const usedUuids = new Set(projects.filter(p => p.dodo_unit_uuid).map(p => p.dodo_unit_uuid));
      let linked = 0, skipped = 0, noMatch = 0;
      for (const p of projects) {
        if (p.dodo_unit_uuid) { skipped++; continue; }
        const u = unitsByName.get(norm(p.planfact_name));
        if (!u) { noMatch++; continue; }
        if (usedUuids.has(u.id)) { skipped++; continue; }
        try {
          await api(`/api/admin/planfact-keys/${keyId}/projects/${p.id}/config`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dodo_unit_uuid: u.id }),
          });
          linked++; usedUuids.add(u.id);
        } catch (e) {}
      }
      autoBtn.disabled = false;
      toast(`Привязано: ${linked}, уже было: ${skipped}, не нашли пару: ${noMatch}`);
      openKeyProjectsModal(keyId, keyName);
    });
  }
}

async function initUsersTab() {
  // Скрываем admin-only элементы для не-админов; показываем для админов.
  // Дополнительно: super-only элементы (например «Каталог PF-ключей»)
  // показываются только super_admin'у. network_admin админит юзеров
  // в рамках своего ключа, но не управляет каталогом ключей.
  const isAdmin = !!(state.me && state.me.is_admin);
  const isSuper = !!(state.me && state.me.role === 'super_admin');
  document.querySelectorAll('.admin-only').forEach(e => {
    e.classList.toggle('hidden', !isAdmin);
  });
  document.querySelectorAll('.super-only').forEach(e => {
    e.classList.toggle('hidden', !isSuper);
  });
  // «Цели и нормативы» — управленцам (visibility ≥ 30) и админам.
  const canTargets = isAdmin || (((state.me && state.me.visibility_level) || 0) >= 30);
  document.querySelectorAll('.lvl30-only').forEach(e => {
    e.classList.toggle('hidden', !canTargets);
  });
  // Если активная вкладка скрыта для этой роли — уводим на «Профиль».
  const _activeBtn = document.querySelector('.tab-btn.active');
  if (_activeBtn && _activeBtn.classList.contains('hidden')) showTab('profile');
  if (!isAdmin) return;

  // Подгружаем каталоги (PF-ключи + Dodo IS логины) для селектов.
  // network_admin тоже зовёт loadAdminCatalogs — для своего PF-ключа
  // и Dodo IS логинов, чтобы корректно отрисовать форму создания юзера.
  await loadAdminCatalogs();
  if (isSuper) {
    initPfKeysCatalog();
    initCacheModal();
  }

  // Закрытие модалок по [data-close]
  document.querySelectorAll('[data-close]').forEach(btn => {
    btn.addEventListener('click', () => {
      btn.closest('.modal')?.classList.add('hidden');
    });
  });

  // Кнопка «Создать»
  document.getElementById('btnCreateUser').addEventListener('click', openCreateUserModal);

  // Submit формы создания
  document.getElementById('createUserForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    setMsg('cuMsg', '', '');
    const pfKeyId = document.getElementById('cuPfKeyId').value;
    // Селект «Роль»: user / network_admin / super_admin. У network admin'а
    // селект скрыт CSS'ом (.super-only) — для них роль форсится 'user'.
    const isSuperCreator = !!(state.me && state.me.role === 'super_admin');
    const roleSel = document.getElementById('cuRole');
    const role = isSuperCreator ? (roleSel.value || 'user') : 'user';
    const body = {
      username: document.getElementById('cuUsername').value.trim(),
      password: document.getElementById('cuPassword').value,
      display_name: document.getElementById('cuDisplayName').value.trim() || null,
      role,
      dodois_credentials_name: document.getElementById('cuDodoisName').value || null,
      planfact_key_id: pfKeyId ? Number(pfKeyId) : null,
      visibility_level: Number(document.getElementById('cuVisibilityLevel').value) || 100,
    };
    try {
      await post('/api/admin/users', body);
      closeModal('userCreateModal');
      toast('Пользователь создан');
      await renderUsersTable();
    } catch (err) {
      setMsg('cuMsg', err.message, 'err');
    }
  });

  // Submit формы редактирования
  document.getElementById('editUserForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    setMsg('ueMsg', '', '');
    const id = document.getElementById('ueId').value;
    const pfKeyId = document.getElementById('uePfKeyId').value;
    // Селект роли: user / network_admin / super_admin. Network admin
    // селект не видит — для него поле остаётся, но не отправляется (роль
    // не меняется). Super admin может выбрать любую.
    const isSuperEditor = !!(state.me && state.me.role === 'super_admin');
    const newRole = isSuperEditor
      ? (document.getElementById('ueRole').value || 'user')
      : undefined;
    const body = {
      display_name: document.getElementById('ueDisplayName').value.trim() || null,
      dodois_credentials_name: document.getElementById('ueDodoisName').value || null,
      visibility_level: Number(document.getElementById('ueVisibilityLevel').value) || 100,
    };
    if (newRole !== undefined) body.role = newRole;
    if (pfKeyId === '') {
      body.clear_planfact_key = true;
    } else {
      body.planfact_key_id = Number(pfKeyId);
    }
    try {
      await api(`/api/admin/users/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      closeModal('userEditModal');
      toast('Сохранено');
      await renderUsersTable();
    } catch (err) {
      setMsg('ueMsg', err.message, 'err');
    }
  });

  // Сброс пароля
  document.getElementById('ueResetPwd').addEventListener('click', async () => {
    const id = document.getElementById('ueId').value;
    const username = document.getElementById('ueUsername').value;
    if (!confirm(`Сгенерировать новый пароль для ${username}?\n\nСтарый пароль перестанет работать. Скопируйте новый и передайте пользователю.`)) return;
    try {
      const r = await post(`/api/admin/users/${id}/reset-password`, {});
      closeModal('userEditModal');
      showGeneratedPassword(r.password);
    } catch (err) {
      setMsg('ueMsg', err.message, 'err');
    }
  });

  // Удаление
  document.getElementById('ueDelete').addEventListener('click', async () => {
    const id = document.getElementById('ueId').value;
    const username = document.getElementById('ueUsername').value;
    if (!confirm(`Удалить пользователя ${username}? Это удалит ВСЕ его данные (проекты, цели, шаблон).`)) return;
    try {
      await api(`/api/admin/users/${id}`, { method: 'DELETE' });
      closeModal('userEditModal');
      toast('Пользователь удалён');
      await renderUsersTable();
    } catch (err) {
      setMsg('ueMsg', err.message, 'err');
    }
  });

  // Кнопка «Скопировать» в модалке нового пароля
  document.getElementById('copyPwdBtn').addEventListener('click', async () => {
    const text = document.getElementById('generatedPwd').textContent;
    try {
      await navigator.clipboard.writeText(text);
      toast('Скопировано в буфер обмена');
    } catch (err) {
      toast('Не удалось скопировать: ' + err.message, 'error');
    }
  });

  renderUsersTable();
}

// ---------- Bootstrap ----------
// ============ Метрики (KPI с формулами) ============
// admin only. Загружается лениво — только при активации табы или сразу для
// админа. Каждая строка таблицы редактируется inline + кнопка «Проверить»
// (POST /api/metrics/preview, рисует line_refs с подсветкой missing).

const METRICS_FORMATS = [
  { value: 'pct', label: '%' },
  { value: 'rub', label: '₽' },
  { value: 'x',   label: '×' },
];

async function loadMetrics() {
  const resp = await api('/api/metrics').catch(() => ({ metrics: [] }));
  state.metrics = resp.metrics || [];
}


// ─── Board card ops-metrics visibility (S19) ─────────────────────────

async function loadBoardMetrics() {
  const resp = await api('/api/board-metrics').catch(() => ({ metrics: [] }));
  state.boardMetrics = resp.metrics || [];
}

function renderBoardMetrics() {
  const wrap = document.getElementById('boardMetricsList');
  if (!wrap) return;
  const items = state.boardMetrics || [];
  if (items.length === 0) {
    wrap.innerHTML = '<p class="muted">Загрузка…</p>';
    return;
  }
  // Группируем по группе (kitchen / delivery)
  const groups = { kitchen: [], delivery: [] };
  items.forEach(m => {
    (groups[m.group] || (groups[m.group] = [])).push(m);
  });
  const groupTitles = {
    kitchen: 'Кухня',
    delivery: 'Доставка',
  };
  const html = Object.entries(groups).map(([gkey, gitems]) => {
    if (!gitems.length) return '';
    return `
      <div class="board-metrics-group" style="margin-bottom:16px">
        <h4 style="margin:0 0 8px;color:var(--muted);font-size:12px;
                   letter-spacing:0.08em;text-transform:uppercase;
                   font-weight:700">${esc(groupTitles[gkey] || gkey)}</h4>
        <div style="display:flex;flex-direction:column;gap:6px">
          ${gitems.map(m => `
            <label style="display:flex;align-items:center;gap:10px;
                          padding:6px 8px;border-radius:6px;cursor:pointer;
                          background:var(--row-bg, rgba(0,0,0,0.02))">
              <input type="checkbox" data-board-metric="${esc(m.code)}"
                     ${m.is_visible ? 'checked' : ''}>
              <span>${esc(m.label)}</span>
              <code style="color:var(--muted-2);font-size:11px">${esc(m.code)}</code>
            </label>
          `).join('')}
        </div>
      </div>
    `;
  }).join('');
  wrap.innerHTML = html;
  // Обработчики
  wrap.querySelectorAll('input[data-board-metric]').forEach(cb => {
    cb.addEventListener('change', async (e) => {
      const code = cb.dataset.boardMetric;
      const is_visible = cb.checked;
      try {
        await api(`/api/board-metrics/${encodeURIComponent(code)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ is_visible }),
        });
        // Обновляем локальный стейт чтобы перерисовка не сбросила
        const m = state.boardMetrics.find(x => x.code === code);
        if (m) m.is_visible = is_visible;
        if (typeof toast === 'function') {
          toast(`«${m?.label || code}» ${is_visible ? 'показывается' : 'скрыта'}`, 'ok');
        }
      } catch (err) {
        cb.checked = !is_visible;
        if (typeof toast === 'function') {
          toast('Не удалось сохранить: ' + err.message, 'error');
        }
      }
    });
  });
}

function renderMetrics() {
  const tbody = document.getElementById('metricsTbody');
  if (!tbody) return;
  const lineByNo = new Map();
  (state.template?.nodes || []).forEach(n => {
    if (n.line_no) lineByNo.set(n.line_no, n);
  });
  if (!state.metrics || state.metrics.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="9" style="text-align:center; color:var(--muted); padding:24px;">
        Метрик пока нет. Нажми «+ Метрика» сверху.
      </td></tr>
    `;
    return;
  }
  tbody.innerHTML = state.metrics.map(m => `
    <tr data-code="${esc(m.code)}">
      <td><code>${esc(m.code)}</code></td>
      <td><input type="text" data-f="label" value="${esc(m.label)}"></td>
      <td>
        <input type="text" data-f="formula" value="${esc(m.formula)}" class="formula-input"
               title="Ссылки на строки шаблона: [N]. Пример: [13] / [7]">
        <div class="formula-status" data-f="status"></div>
      </td>
      <td>
        <select data-f="format">
          ${METRICS_FORMATS.map(f => `<option value="${f.value}" ${f.value === m.format ? 'selected' : ''}>${f.label}</option>`).join('')}
        </select>
      </td>
      <td style="text-align:center">
        <input type="checkbox" data-f="is_target" ${m.is_target ? 'checked' : ''}>
      </td>
      <td style="text-align:center" title="Рисовать ли плитку на карточке проекта">
        <input type="checkbox" data-f="is_visible" ${m.is_visible !== false ? 'checked' : ''}>
      </td>
      <td><input type="number" data-f="sort_order" value="${m.sort_order || 0}" style="width:60px"></td>
      <td>
        <select data-f="min_visibility_level" title="Минимальный уровень доступа юзера">
          <option value="0" ${(m.min_visibility_level ?? 0) === 0 ? 'selected' : ''}>0 — все</option>
          <option value="10" ${m.min_visibility_level === 10 ? 'selected' : ''}>10 — управляющий</option>
          <option value="30" ${m.min_visibility_level === 30 ? 'selected' : ''}>30 — территориальный</option>
          <option value="60" ${m.min_visibility_level === 60 ? 'selected' : ''}>60 — директор</option>
          <option value="100" ${m.min_visibility_level === 100 ? 'selected' : ''}>100 — партнёр</option>
        </select>
      </td>
      <td>
        <button type="button" class="btn-danger metric-delete" title="Удалить">×</button>
      </td>
    </tr>
  `).join('');
  // Хендлеры
  tbody.querySelectorAll('tr[data-code]').forEach(tr => {
    const code = tr.dataset.code;
    // Сохранение по change любого поля. На бэке формула валидируется при
    // сохранении (PUT /api/metrics/{code}); ошибка → красный статус под
    // полем «Формула», ОК → зелёный flash.
    tr.querySelectorAll('input, select').forEach(input => {
      input.addEventListener('change', () => saveMetricRow(tr));
    });
    // Кнопка «Удалить»
    tr.querySelector('.metric-delete').addEventListener('click', async () => {
      if (!confirm(`Удалить метрику ${code}?`)) return;
      try {
        await api(`/api/metrics/${encodeURIComponent(code)}`, { method:'DELETE' });
        await loadMetrics();
        renderMetrics();
        if (typeof renderPnlMatrix === 'function') renderPnlMatrix();
        toast('Метрика удалена', 'ok');
      } catch (e) { toast('Не удалось удалить: ' + e.message, 'error'); }
    });
  });
}

async function saveMetricRow(tr) {
  const code = tr.dataset.code;
  const body = {
    code,
    label: tr.querySelector('[data-f=label]').value.trim(),
    formula: tr.querySelector('[data-f=formula]').value.trim(),
    format: tr.querySelector('[data-f=format]').value,
    is_target: tr.querySelector('[data-f=is_target]').checked,
    sort_order: parseInt(tr.querySelector('[data-f=sort_order]').value, 10) || 0,
    min_visibility_level: parseInt(
      tr.querySelector('[data-f=min_visibility_level]').value, 10) || 0,
    is_visible: tr.querySelector('[data-f=is_visible]').checked,
  };
  try {
    await api(`/api/metrics/${encodeURIComponent(code)}`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    flashOk(tr.querySelector('[data-f=formula]'));
    showFormulaStatus(tr, { ok: true, msg: 'Сохранено' });
    // Перечитываем метрики и обновляем матрицу «Цели» — состав колонок
    // зависит от is_target, который только что мог измениться.
    await loadMetrics();
    if (typeof renderPnlMatrix === 'function') renderPnlMatrix();
  } catch (e) {
    flashErr(tr.querySelector('[data-f=formula]'));
    showFormulaStatus(tr, { ok: false, msg: e.message });
  }
}

function showFormulaStatus(tr, { ok, msg }) {
  const el = tr.querySelector('[data-f=status]');
  if (!el) return;
  el.textContent = msg;
  el.className = 'formula-status ' + (ok ? 'ok' : 'err');
}

async function addMetric() {
  const code = prompt('Код метрики (UC, LC, DC, CUSTOM_X и т.п.):');
  if (!code) return;
  const codeUp = code.trim().toUpperCase();
  if (!/^[A-Z0-9_]{1,32}$/.test(codeUp)) {
    toast('Код должен быть из латиницы/цифр/_, до 32 символов', 'error');
    return;
  }
  if ((state.metrics || []).some(m => m.code === codeUp)) {
    toast('Метрика с таким кодом уже есть', 'error');
    return;
  }
  // Сохраним заглушку — юзер допишет формулу прямо в таблице
  const body = {
    code: codeUp, label: codeUp,
    formula: '[1]', format: 'pct',
    is_target: false, sort_order: (state.metrics?.length || 0) + 100,
  };
  try {
    await api(`/api/metrics/${encodeURIComponent(codeUp)}`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    await loadMetrics();
    renderMetrics();
    toast(`Метрика ${codeUp} добавлена`, 'ok');
  } catch (e) {
    toast('Не удалось создать: ' + e.message, 'error');
  }
}

// Список строк шаблона справа от таблицы метрик. Клик подставляет [N] в
// последнее активное поле формулы (где курсор) — самый частый кейс
// «настраиваю формулу, нужна строка».
let _lastActiveFormulaInput = null;

function trackActiveFormulaInput() {
  document.body.addEventListener('focusin', (e) => {
    if (e.target?.classList?.contains('formula-input')) {
      _lastActiveFormulaInput = e.target;
    }
  });
}

function renderMetricsLineList(filter = '') {
  const wrap = document.getElementById('metricsLineList');
  if (!wrap) return;
  const nodes = (state.template?.nodes || []).filter(n => n.line_no);
  const f = filter.trim().toLowerCase();
  const matched = f
    ? nodes.filter(n =>
        String(n.line_no).includes(f) ||
        (n.title || '').toLowerCase().includes(f) ||
        (n.path_lc || '').includes(f)
      )
    : nodes;
  if (!matched.length) {
    wrap.innerHTML = '<div class="muted" style="padding:8px;">Ничего не найдено</div>';
    return;
  }
  wrap.innerHTML = matched.map(n => {
    const indent = '&nbsp;&nbsp;'.repeat(Math.min(n.depth || 0, 4));
    const flag = n.is_calc ? ' <span class="muted">[calc]</span>' : '';
    return `<div class="line-row" data-lineno="${n.line_no}" title="Клик — вставить [${n.line_no}] в формулу">
      <code>[${n.line_no}]</code>
      <span class="line-title">${indent}${esc(n.title)}${flag}</span>
    </div>`;
  }).join('');
  wrap.querySelectorAll('.line-row').forEach(row => {
    row.addEventListener('click', () => insertLineRefIntoFormula(parseInt(row.dataset.lineno, 10)));
  });
}

function insertLineRefIntoFormula(lineNo) {
  const target = _lastActiveFormulaInput;
  if (!target || !document.body.contains(target)) {
    // Скопируем в буфер обмена как fallback
    const txt = `[${lineNo}]`;
    navigator.clipboard?.writeText(txt).then(
      () => toast(`${txt} → буфер обмена`, 'ok'),
      () => toast(`Скопируй вручную: ${txt}`, 'error')
    );
    return;
  }
  const ref = `[${lineNo}]`;
  const start = target.selectionStart ?? target.value.length;
  const end = target.selectionEnd ?? target.value.length;
  const before = target.value.slice(0, start);
  const after = target.value.slice(end);
  target.value = before + ref + after;
  target.focus();
  const newPos = start + ref.length;
  target.setSelectionRange(newPos, newPos);
  // Триггерим change → сохранение строки
  target.dispatchEvent(new Event('change', { bubbles: true }));
}

// ─── Расчётные KC/DC (из Dodo IS): коэффициенты налогов + показ DC ───
async function initCalcSettings() {
  const box = document.getElementById('calcSettingsBox');
  if (!box) return;
  const kc = document.getElementById('csKcTax');
  const dc = document.getElementById('csDcTax');
  const dcEn = document.getElementById('csDcEnabled');
  const msg = document.getElementById('csMsg');
  try {
    const s = await api('/api/calc-settings');
    kc.value = s.kc_tax_coefficient ?? 1;
    dc.value = s.dc_tax_coefficient ?? 1;
    dcEn.checked = !!s.dc_live_enabled;
    if (s.no_planfact_key) msg.textContent = 'Нет привязанного PF-ключа';
  } catch (e) {
    msg.textContent = 'Ошибка загрузки: ' + e.message;
  }
  document.getElementById('csSave')?.addEventListener('click', async () => {
    msg.textContent = 'Сохранение…';
    try {
      await api('/api/calc-settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kc_tax_coefficient: parseFloat(kc.value) || 1,
          dc_tax_coefficient: parseFloat(dc.value) || 1,
          dc_live_enabled: dcEn.checked,
        }),
      });
      msg.textContent = 'Сохранено ✓';
      toast('Настройки KC/DC сохранены');
    } catch (e) {
      msg.textContent = 'Ошибка: ' + e.message;
    }
  });
}

async function initMetricsTab() {
  const isAdmin = !!(state.me && state.me.is_admin);
  if (!isAdmin) return;
  document.getElementById('btnAddMetric')?.addEventListener('click', addMetric);
  trackActiveFormulaInput();
  await Promise.all([loadMetrics(), loadBoardMetrics()]);
  renderMetrics();
  renderBoardMetrics();
  renderMetricsLineList();
  await initCalcSettings();
  document.getElementById('metricsLineSearch')?.addEventListener('input', (e) => {
    renderMetricsLineList(e.target.value);
  });
}


document.addEventListener('DOMContentLoaded', async () => {
  try {
    await loadAll();
  } catch (e) {
    console.error(e);
    toast('Ошибка загрузки: ' + e.message, 'error');
  } finally {
    // Инициализация табов/ролей обязана пройти даже если loadAll частично
    // упал — иначе у админа без ключа не раскроются вкладки (в т.ч.
    // «Платформа», где ключ и добавляется). Cold-start catch-22.
    try { initProfileTab(); } catch (_) {}
    try { initIntegrationsTab(); } catch (_) {}
    try { initSourceWizard(); } catch (_) {}
    try { initUsersTab(); } catch (_) {}
    try { initMetricsTab(); } catch (_) {}
  }
});
