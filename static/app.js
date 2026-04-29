// P&L Dashboard — client logic.
// Использует месячный пикер: пользователь выбирает один календарный месяц,
// это превращается в [YYYY-MM-01 .. YYYY-MM-<last>] для API.
// Учитывает projects_config — показывает только активные проекты.

const state = {
  projects: [],                 // только активные, которые пришли в /api/pnl.projects
  allProjects: [],              // полный список из /api/projects для сайдбара
  selectedProjects: new Set(),  // ручной фильтр пользователя
  pnl: null,
  charts: {},
  currentMonth: null,
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

const fmtPct = (n) => {
  if (n === null || n === undefined || isNaN(n)) return '';
  const sign = n < 0 ? '' : '+';
  return sign + (n * 100).toFixed(1).replace('.', ',') + '%';
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

// --- Month picker ---
function initMonthSelect() {
  const sel = el('monthSelect');
  const now = new Date();
  const opts = [];
  for (let i = 0; i < 24; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    const label = d.toLocaleDateString('ru-RU', { year: 'numeric', month: 'long' });
    opts.push({ key, label: label.charAt(0).toUpperCase() + label.slice(1) });
  }
  sel.innerHTML = opts.map(o => `<option value="${o.key}">${o.label}</option>`).join('');
  // По умолчанию — текущий месяц
  state.currentMonth = opts[0].key;
  sel.value = state.currentMonth;
  syncPeriodFromMonth();

  sel.addEventListener('change', () => {
    state.currentMonth = sel.value;
    syncPeriodFromMonth();
    loadPnl();
  });
}

function syncPeriodFromMonth() {
  // YYYY-MM → YYYY-MM-01 / YYYY-MM-<last day>
  const [y, m] = state.currentMonth.split('-').map(Number);
  const last = new Date(y, m, 0).getDate();
  el('dateStart').value = `${state.currentMonth}-01`;
  el('dateEnd').value = `${state.currentMonth}-${String(last).padStart(2, '0')}`;
  el('periodMonth').value = state.currentMonth;
}

function previousMonthKey(key) {
  const [y, m] = key.split('-').map(Number);
  const d = new Date(y, m - 2, 1); // m-1 = current, -1 ещё = prev
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
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
  // По умолчанию в сайдбаре отмечены все активные проекты.
  state.selectedProjects = new Set(
    state.allProjects.filter(p => p.is_active).map(p => p.id)
  );
  renderProjectsSidebar();
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
            <div class="proj-row">
              <label class="switch">
                <input type="checkbox" data-pid="${p.id}"
                  ${state.selectedProjects.has(p.id) ? 'checked' : ''}>
                <span class="slider"></span>
              </label>
              <span class="proj-name">${esc(p.name)}</span>
            </div>
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

  // Применить — подтянуть applied к selected, загрузить P&L
  document.getElementById('projApplyBtn')?.addEventListener('click', () => {
    state.appliedSelection = new Set(state.selectedProjects);
    refreshApplyBar();
    loadPnl();
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
    el('pnlTable').innerHTML = '<tr><td style="padding:20px;color:#9ca3af">Выберите хотя бы один проект</td></tr>';
    el('kpiCards').innerHTML = '';
    destroyCharts();
    return;
  }
  const ds = el('dateStart').value;
  const de = el('dateEnd').value;
  if (!ds || !de) { toast('Укажи месяц', 'error'); return; }

  const params = new URLSearchParams();
  params.set('date_start', ds);
  params.set('date_end', de);
  params.set('period_month', state.currentMonth);
  state.selectedProjects.forEach(p => params.append('project_ids', p));

  if (el('compareToggle').checked) {
    const [ps, pe] = monthToRange(previousYearKey(state.currentMonth));
    params.set('compare_start', ps);
    params.set('compare_end', pe);
    params.set('compare_mode', 'lfl');
  }

  try {
    state.pnl = await api('/api/pnl?' + params.toString());
    render();
  } catch (e) {
    toast('Ошибка загрузки: ' + e.message, 'error');
  }
}

// --- Rendering ---
function render() {
  renderCards();
  renderCharts();
  renderTable();
}

function findLine(code) {
  return state.pnl.lines.find(l => l.code === code);
}

// Цвет значения по знаку amount. При null/undefined/0 — без цвета.
function signClass(amount) {
  if (amount === null || amount === undefined) return '';
  if (amount < 0) return 'neg';
  if (amount > 0) return 'pos';
  return '';
}

// ---- Tile builders ----

// Плитка % от выручки с таргетом (UC/LC/DC/TC). Ceiling: actual <= target = ok.
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
  const targetStr = (typeof target === 'number' && target > 0)
    ? `цель ${(target * 100).toFixed(0)}%`
    : '&nbsp;';
  const hint = opts.hint ? ` <span class="tile-sublabel">${opts.hint}</span>` : '';
  return `
    <div class="tile tile-metric ${stateCls}">
      <div class="tile-label">${label}${hint}</div>
      <div class="tile-value">${valueStr}<span class="tile-unit">%</span></div>
      <div class="tile-hint">${targetStr}</div>
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
  const valueStr = hasVal ? fmtNum(val, digits) : '—';
  // Скобка с абсолютным количеством — рядом со значением, в более мелком шрифте.
  let countStr = '';
  if (meta.count_field && opsRow && opsRow[meta.count_field] != null) {
    countStr = ` <span class="tile-sub">(${fmtNum(opsRow[meta.count_field], 0)})</span>`;
  }
  const targetStr = target != null ? `цель ${fmtNum(target, digits)} ${meta.unit}` : '&nbsp;';
  return `
    <div class="tile tile-metric ${stateCls}">
      <div class="tile-label">${meta.label}</div>
      <div class="tile-value">${valueStr}<span class="tile-unit">${meta.unit}</span>${countStr}</div>
      <div class="tile-hint">${targetStr}</div>
    </div>`;
}

// Большая финансовая плитка (выручка, EBITDA и т.п.)
function finTile(label, proj, opts = {}) {
  const amt = proj?.amount;
  const pct = proj?.pct_of_revenue;
  const hasVal = typeof amt === 'number' && !isNaN(amt);
  const cls = opts.colorize === false ? '' :
    (hasVal && amt < 0 ? 'tile-neg' : (hasVal && amt > 0 ? 'tile-pos' : ''));
  const pctStr = (typeof pct === 'number' && !isNaN(pct))
    ? `${(pct * 100).toFixed(1).replace('.', ',')}% от выручки`
    : '&nbsp;';
  return `
    <div class="tile tile-fin ${cls}">
      <div class="tile-label">${label}</div>
      <div class="tile-value">${fmt(amt)}<span class="tile-unit">₽</span></div>
      <div class="tile-hint">${opts.hideSub ? '&nbsp;' : pctStr}</div>
    </div>`;
}

function renderCards() {
  const box = el('kpiCards');
  box.innerHTML = '';
  const revenue = findLine('REVENUE');
  const uc = findLine('UC');
  const lc = findLine('LC');
  const dc = findLine('DC');
  const tc = findLine('TC');
  const margin = findLine('MARGIN');
  const ebitda = findLine('EBITDA');
  const net = findLine('NET_PROFIT');
  if (!revenue) return;

  const defT = state.pnl?.default_targets || {};
  // target_report уже содержит эффективный таргет (project override ИЛИ default)
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

  state.pnl.projects.forEach(p => {
    const rev = revenue.projects[p.id]?.amount || 0;
    const ebP = ebitda?.projects[p.id] || {};
    const ops = p.ops || {};

    let cls = '';
    if (rev > 0) cls = (ebP.amount ?? 0) >= 0 ? 'profit' : 'loss';

    // Блок метрик: UC/LC/DC/TC + ops — все плитки
    const metricTiles = [
      pctTile('UC', uc?.projects[p.id], targetFor(p.id, 'UC')),
      pctTile('LC', lc?.projects[p.id], targetFor(p.id, 'LC')),
      pctTile('DC', dc?.projects[p.id], targetFor(p.id, 'DC'), { hint: 'от дост.' }),
      pctTile('TC', tc?.projects[p.id], targetFor(p.id, 'TC')),
      ...opsMeta.map(m => opsTile(m, ops[m.field], opsTargets[m.code], ops)),
    ].join('');

    // Блок финансов: выручка, маржин. прибыль, EBITDA, чистая прибыль
    const finTiles = [
      finTile('Выручка', revenue.projects[p.id], { colorize: false, hideSub: true }),
      finTile('Маржин. прибыль', margin?.projects[p.id]),
      finTile('EBITDA', ebitda?.projects[p.id]),
      finTile('Чистая прибыль', net?.projects[p.id]),
    ].join('');

    const div = document.createElement('div');
    div.className = 'card ' + cls;
    div.innerHTML = `
      <div class="card-title">${p.name}</div>
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
const CHARTS = [
  { id: 'revProfit', title: 'Выручка vs Чистая прибыль',         defaultVisible: true },
  { id: 'margins',   title: 'Маржинальность по уровням, %',      defaultVisible: true },
  { id: 'costShare', title: 'Структура затрат, % от выручки',    defaultVisible: true },
];

// Храним set СКРЫТЫХ id (а не видимых), чтобы при добавлении нового графика
// он автоматически становился видимым у уже состоявшихся пользователей —
// если их выбора скрытия в нём нет, то он показывается.
const CHARTS_HIDDEN_KEY = 'pnlDashboard.chartsHidden';

function loadChartsHidden() {
  try {
    const raw = localStorage.getItem(CHARTS_HIDDEN_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch { return new Set(); }
}

function saveChartsHidden(set) {
  try { localStorage.setItem(CHARTS_HIDDEN_KEY, JSON.stringify([...set])); } catch {}
}

function isChartVisible(id) {
  // Дефолт — defaultVisible из каталога. Если пользователь добавил id в
  // hidden-set — скрываем.
  const hidden = loadChartsHidden();
  if (hidden.has(id)) return false;
  const meta = CHARTS.find(c => c.id === id);
  return meta ? meta.defaultVisible !== false : true;
}

function applyChartsVisibility() {
  // Скрываем/показываем сами обёртки .chart-box. Делаем это до renderCharts(),
  // чтобы Chart.js не считал размеры на скрытом canvas (иначе он рендерится
  // 0×0 и при показе остаётся пустым).
  const hidden = loadChartsHidden();
  for (const c of CHARTS) {
    const box = document.querySelector(`[data-chart-id="${c.id}"]`);
    if (!box) continue;
    box.style.display = hidden.has(c.id) ? 'none' : '';
  }
  // Если ВСЕ графики скрыты — прячем и тулбар-таб «⚙ Графики»? Нет, наоборот,
  // оставляем — иначе пользователь не сможет их вернуть. Тулбар всегда виден.
}

function renderChartsConfigList() {
  const box = el('chartsConfigList');
  if (!box) return;
  const hidden = loadChartsHidden();
  box.innerHTML = CHARTS.map(c => `
    <label class="charts-config-row">
      <input type="checkbox" data-chart-id="${c.id}" ${hidden.has(c.id) ? '' : 'checked'}>
      <span>${esc(c.title)}</span>
    </label>
  `).join('');
  box.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const set = loadChartsHidden();
      const id = cb.dataset.chartId;
      if (cb.checked) set.delete(id);
      else set.add(id);
      saveChartsHidden(set);
      applyChartsVisibility();
      renderCharts();
    });
  });
}

function setAllChartsVisible(visible) {
  const set = visible ? new Set() : new Set(CHARTS.map(c => c.id));
  saveChartsHidden(set);
  renderChartsConfigList();
  applyChartsVisibility();
  renderCharts();
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

  // --- График 3: Структура затрат (stacked) ---
  // В compare-режиме переключаемся с одинарного stacked на сгруппированный stacked (cur / ly)
  // с помощью ключа stack. В обычном режиме — без группировки.
  if (isChartVisible('costShare')) {
    const costCodes = ['UC','LC','DC','RENT','MARKETING','FRANCHISE','OTHER_OPEX'];
    const colors = ['#6366f1','#8b5cf6','#ec4899','#f97316','#14b8a6','#fb7185','#94a3b8'];
    const costDatasets = costCodes.map((code, i) => {
      const line = findLine(code);
      return {
        label: hasCmp ? `${line?.label || code} · ${curLbl}` : (line?.label || code),
        stack: 'cur',
        data: pids.map(pid => (line?.projects[pid]?.pct_of_revenue || 0) * 100),
        backgroundColor: colors[i],
        borderRadius: 2,
      };
    });
    if (hasCmp) {
      costCodes.forEach((code, i) => {
        const line = findIn(cmp.lines, code);
        costDatasets.push({
          label: `${line?.label || code} · ${lyLbl}`,
          stack: 'ly',
          data: pids.map(pid => (line?.projects[pid]?.pct_of_revenue || 0) * 100),
          backgroundColor: colors[i] + '59', // ~35% alpha в hex
          borderRadius: 2,
        });
      });
    }
    state.charts.costShare = new Chart(el('costShare'), {
      type: 'bar',
      data: { labels, datasets: costDatasets },
      options: { ...pctOpts, scales: { ...pctOpts.scales, x: { ...pctOpts.scales.x, stacked: true }, y: { ...pctOpts.scales.y, stacked: true } } }
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
  projects.forEach(p => thead += `<th>${p.name}</th>`);
  thead += '<th>Итого</th></tr></thead>';

  let tbody = '<tbody>';
  state.pnl.lines.forEach(line => {
    const cls = line.kind === 'summary' ? 'summary-row'
             : line.kind === 'final' ? 'final-row'
             : line.kind === 'header' ? 'lvl-1'
             : 'lvl-' + line.level;
    const drillable = line.kind === 'detail' && line.code !== 'REVENUE';
    tbody += `<tr class="${cls}" data-code="${line.code}">`;
    tbody += `<td>${line.label}</td>`;
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

  let thead = '<thead><tr>';
  thead += `<th class="tree-col-head">Статья${toolbar}</th>`;
  projects.forEach(p => thead += `<th title="${esc(p.name)}">${esc(p.name)}</th>`);
  thead += '<th title="Сумма по выбранным пиццериям">Итого</th></tr></thead>';

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
        // % показываем для расходных строк (не выручки), и не для calc-итогов «без формулы»
        if (pct != null && n.pnl_code !== 'REVENUE') {
          cell += `<span class="pct">${fmtPctAbs(pct)}</span>`;
        }
      }
      const cellCls = isDrillable ? 'cell-clickable' : '';
      const cellAttrs = isDrillable
        ? ` data-pid="${p.id}" data-pname="${esc(p.name)}" data-amt="${amt ?? ''}" data-cat-ids="${catIdsJson}"`
        : '';
      tbody += `<td class="${cellCls}"${cellAttrs}>${cell}</td>`;
    });

    const tot = n.total || {};
    let totalCell;
    if (isPctRow) {
      const valCls = (tot.pct_of_revenue != null && tot.pct_of_revenue < 0) ? 'neg' : '';
      totalCell = `<strong class="${valCls}">${fmtPctAbs(tot.pct_of_revenue)}</strong>`;
    } else {
      totalCell = `<strong>${fmt(tot.amount)}</strong>`;
      if (tot.pct_of_revenue != null && n.pnl_code !== 'REVENUE') {
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
      openDrillDown(code, title, td.dataset.pid || null, td.dataset.pname || '', catIds, expectedAmt);
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
async function openDrillDown(code, label, projectId = null, projectName = '', categoryIds = [], expectedAmount = null) {
  const prefix = projectName ? `${projectName} · ` : '';
  el('drillTitle').textContent = `Операции · ${prefix}${label}`;
  el('drillBody').innerHTML = '<p class="muted">Загрузка…</p>';
  el('drillModal').classList.remove('hidden');

  const ds = el('dateStart').value;
  const de = el('dateEnd').value;
  const params = new URLSearchParams({ date_start: ds, date_end: de, limit: '500' });
  if (projectId) {
    params.set('project_id', projectId);
  }
  // "Итого" — без фильтра по проекту; PlanFact /operations отдаст все операции
  // за период, а ниже мы покажем колонку «Проект» в таблице.
  for (const cid of (categoryIds || [])) {
    if (cid) params.append('category_ids', cid);
  }

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
        <td>${esc(op.date || '')}</td>
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
    el('drillBody').innerHTML = `<p class="neg">Ошибка: ${e.message}</p>`;
  }
}

// --- Wire up events ---
document.addEventListener('DOMContentLoaded', async () => {
  initMonthSelect();

  // Попап «⚙ Графики» — список + кнопки «Показать все / Скрыть все».
  // Применяем visibility сразу, чтобы скрытые графики не мелькали при первом
  // рендере перед загрузкой данных.
  applyChartsVisibility();
  renderChartsConfigList();
  el('chartsConfigShowAll')?.addEventListener('click', () => setAllChartsVisible(true));
  el('chartsConfigHideAll')?.addEventListener('click', () => setAllChartsVisible(false));

  el('compareToggle').addEventListener('change', loadPnl);

  el('refreshBtn').addEventListener('click', async () => {
    const btn = el('refreshBtn');
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Обновляю…';
    try {
      // PlanFact-кэш и Dodo IS ops — параллельно, они независимы.
      // Dodo IS может отсутствовать (нет токена / нет привязанных юнитов) —
      // не валим весь refresh из-за него, только показываем предупреждение.
      const pfP = fetch('/api/refresh', { method: 'POST' });
      const dodoP = state.currentMonth
        ? fetch(`/api/ops-metrics/sync?period=${state.currentMonth}`, { method: 'POST' })
        : Promise.resolve(null);
      const [pfR, dodoR] = await Promise.allSettled([pfP, dodoP]);

      if (pfR.status === 'rejected' || (pfR.value && !pfR.value.ok)) {
        const msg = pfR.status === 'rejected'
          ? pfR.reason.message
          : `${pfR.value.status}: ${await pfR.value.text()}`;
        throw new Error('PlanFact: ' + msg);
      }

      let opsMsg = '';
      if (dodoR.status === 'fulfilled' && dodoR.value) {
        if (dodoR.value.ok) {
          const res = await dodoR.value.json();
          const n = (res.updated || []).length;
          const nf = (res.not_found_in_response || []).length;
          opsMsg = nf
            ? `, ops: ${n} обновлено / ${nf} без ответа`
            : (n ? `, ops: ${n} обновлено` : '');
        } else {
          const t = await dodoR.value.text();
          opsMsg = `, ops: ошибка Dodo IS (${dodoR.value.status})`;
          console.warn('Dodo IS sync failed:', t);
        }
      } else if (dodoR.status === 'rejected') {
        opsMsg = ', ops: ошибка сети';
      }
      toast('Кэш сброшен' + opsMsg + ', перезагружаю…');
      await loadPnl();
    } catch (e) {
      toast('Ошибка: ' + e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = origText;
    }
  });

  document.querySelectorAll('[data-close]').forEach(b => {
    b.addEventListener('click', () => b.closest('.modal').classList.add('hidden'));
  });

  try {
    await loadProjects();
    await loadPnl();
  } catch (e) {
    toast('Не удалось загрузить проекты. Проверь API-ключ PlanFact в .env', 'error');
  }
});
