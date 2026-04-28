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

// ---------- Tabs (Профиль / Интеграции / Структура / Таргеты / Пользователи) ----------
const TABS = ['profile', 'integrations', 'structure', 'targets', 'users'];
const TAB_STORAGE_KEY = 'pnlSettings.activeTab';

function showTab(name) {
  if (!TABS.includes(name)) name = 'targets';
  document.querySelectorAll('.tab-btn').forEach(b => {
    const on = b.dataset.tab === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.querySelectorAll('.tab-pane').forEach(p => {
    p.classList.toggle('active', p.dataset.pane === name);
  });
  try { localStorage.setItem(TAB_STORAGE_KEY, name); } catch (_) {}
}

function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });
  // Стартовая вкладка: localStorage либо «Таргеты» по умолчанию.
  // Финальное переключение на «Структуру» — если шаблон ещё не загружен —
  // делается в loadAll() после получения /api/template.
  let saved = null;
  try { saved = localStorage.getItem(TAB_STORAGE_KEY); } catch (_) {}
  showTab(saved || 'targets');
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
  ]);
  const [projR, setR, defR, tarR, opsTgR, tplR, meR] = results;
  const ok = (r, fb) => r.status === 'fulfilled' ? r.value : fb;
  state.me = ok(meR, null);

  const projResp = ok(projR, { projects: [] });
  const setResp  = ok(setR,  { settings: {} });
  const defResp  = ok(defR,  { defaults: {} });
  const tarResp  = ok(tarR,  { targets: [] });
  const opsTgResp= ok(opsTgR,{ targets: {}, project_targets: [], meta: [] });
  const tplResp  = ok(tplR,  { nodes: [] });

  // Уведомим про упавшие запросы — но не прервём загрузку.
  results.forEach((r, i) => {
    if (r.status === 'rejected') {
      const url = ['/api/projects','/api/settings','/api/targets/defaults','/api/targets','/api/ops-targets','/api/template','/auth/me'][i];
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

  initMethodology();
  initMonthSelect();
  initDodoSync();
  await loadOpsMetrics();

  renderProjects();
  renderPnlMatrix();
  renderOpsMatrix();
  renderTemplate();

  // Онбординг: если шаблон ещё не загружен и пользователь не выбирал таб
  // вручную в этой сессии — лендим в «Структуру», там кнопка импорта.
  let savedTab = null;
  try { savedTab = localStorage.getItem(TAB_STORAGE_KEY); } catch (_) {}
  if (!savedTab && state.template.nodes.length === 0) {
    showTab('structure');
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

// ---------- Methodology checkbox ----------
function initMethodology() {
  const chk = el('chkIncludeMgr');
  const on = (state.settings.include_manager_in_lc ?? 'true').toLowerCase() === 'true';
  chk.checked = on;
  chk.addEventListener('change', async () => {
    await post('/api/settings', {
      key: 'include_manager_in_lc',
      value: chk.checked ? 'true' : 'false',
    });
    state.settings.include_manager_in_lc = chk.checked ? 'true' : 'false';
    toast('Сохранено');
  });
}

// ---------- Month picker ----------
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
  state.currentMonth = opts[0].key;
  sel.value = state.currentMonth;
  sel.addEventListener('change', async () => {
    state.currentMonth = sel.value;
    await loadOpsMetrics();
    renderOpsMatrix();
  });
}

async function loadOpsMetrics() {
  const res = await api(`/api/ops-metrics?period_month=${state.currentMonth}`);
  state.opsMetrics = res.metrics || {};
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
          <th>Отображаемое имя</th>
          <th style="width:90px;text-align:center;">Порядок</th>
          <th style="width:320px;">Dodo IS юнит</th>
        </tr>
      </thead>
      <tbody>
  `;
  state.projects.forEach(p => {
    const unitName = dodoUnitName(p.dodo_unit_uuid);
    html += `
      <tr data-pid="${esc(p.id)}" class="${p.is_active ? '' : 'row-off'}">
        <td class="cell-center">
          <label class="switch">
            <input type="checkbox" class="js-active" ${p.is_active ? 'checked' : ''} ${dis}>
            <span class="slider"></span>
          </label>
        </td>
        <td class="cell-name">${esc(p.planfact_name || p.name)}</td>
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
function renderPnlMatrix() {
  const box = el('pnlTargetsMatrix');
  const metrics = state.targetableMetrics;
  const active = state.projects.filter(p => p.is_active);

  let html = `<table class="dense-table matrix-table pnl-matrix"><thead>
    <tr class="row-head-main">
      <th class="sticky-col metric-col">Пиццерия</th>`;
  metrics.forEach(m => html += `
    <th class="col-proj" title="${esc(state.pnlFullNames[m] || m)}">
      <div class="ops-h-label">${esc(state.pnlCodes[m] || m)}</div>
      <div class="ops-h-unit muted">${esc(state.pnlFullNames[m] || '')} · %</div>
    </th>`);
  html += `</tr>
    <tr class="row-head-target">
      <td class="sticky-col metric-col target-label">Дефолт</td>`;
  metrics.forEach(m => {
    const def = state.defaultTargets[m];
    html += `<td class="col-proj">
      <input type="text" class="js-def-target inp-flush inp-right target-input"
        data-metric="${m}"
        value="${def != null ? fmtPct(def) : ''}" placeholder="—">
    </td>`;
  });
  html += `</tr>
  </thead><tbody>`;

  if (!active.length) {
    html += `<tr><td class="sticky-col metric-col muted">Нет активных проектов</td>`;
    metrics.forEach(() => html += `<td class="col-proj"></td>`);
    html += `</tr>`;
  } else {
    active.forEach(p => {
      html += `<tr data-pid="${esc(p.id)}"><td class="sticky-col metric-col">${esc(p.name)}</td>`;
      metrics.forEach(m => {
        const pct = state.projectTargets[`${p.id}|${m}`];
        const hasOverride = pct != null;
        const def = state.defaultTargets[m];
        html += `<td class="col-proj ${hasOverride ? 'has-override' : ''}">
          <input type="text" class="js-proj-target inp-flush inp-right"
            data-pid="${esc(p.id)}" data-metric="${m}"
            value="${hasOverride ? fmtPct(pct) : ''}"
            placeholder="${def != null ? fmtPct(def) : '—'}">
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
      try {
        if (raw === '') {
          await del(`/api/targets/defaults?metric_code=${m}`);
          delete state.defaultTargets[m];
        } else {
          const v = parseNum(raw);
          if (v == null) { flashErr(inp); toast('Некорректное значение', 'error'); return; }
          const pct = v / 100;
          await post('/api/targets/defaults', { metric_code: m, target_pct: pct });
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
      try {
        if (raw === '') {
          await del(`/api/targets?project_id=${pid}&metric_code=${m}`);
          delete state.projectTargets[key];
          td.classList.remove('has-override');
        } else {
          const v = parseNum(raw);
          if (v == null) { flashErr(inp); toast('Некорректное значение', 'error'); return; }
          const pct = v / 100;
          await post('/api/targets', { project_id: pid, metric_code: m, target_pct: pct });
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
    html += `<td class="col-proj">
      <input type="text" class="js-ops-def-target inp-flush inp-right target-input"
        data-code="${esc(m.code)}"
        value="${v != null ? fmtNum(v, dig(m)) : ''}" placeholder="—">
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
          let s = 'факт: ' + esc(fmtNum(fact, dig(m)));
          if (m.count_field && values[m.count_field] != null) {
            s += ` (${esc(fmtNum(values[m.count_field], 0))})`;
          }
          factTxt = s;
        }
        html += `<td class="col-proj ${cls} ${override != null ? 'has-override' : ''}">
          <input type="text" class="js-ops-proj-target inp-flush inp-right"
            data-pid="${esc(p.id)}" data-code="${esc(m.code)}"
            value="${override != null ? fmtNum(override, dig(m)) : ''}"
            placeholder="${def != null ? fmtNum(def, dig(m)) : '—'}">
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
      try {
        if (raw === '') {
          await del(`/api/ops-targets?metric_code=${code}`);
          delete state.opsTargets[code];
        } else {
          const v = parseNum(raw);
          if (v == null) { flashErr(inp); toast('Некорректное значение', 'error'); return; }
          await post('/api/ops-targets', { metric_code: code, target_value: v });
          state.opsTargets[code] = v;
        }
        // Обновить placeholder в project-ячейках этой метрики
        box.querySelectorAll(`.js-ops-proj-target[data-code="${code}"]`).forEach(p => {
          const d = state.opsTargets[code];
          p.placeholder = d != null ? fmtNum(d, dig(m)) : '—';
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
      const td = inp.closest('td');
      try {
        if (raw === '') {
          await del(`/api/ops-targets/project?project_id=${pid}&metric_code=${code}`);
          delete state.opsProjectTargets[key];
          td.classList.remove('has-override');
        } else {
          const v = parseNum(raw);
          if (v == null) { flashErr(inp); toast('Некорректное значение', 'error'); return; }
          await post('/api/ops-targets/project',
            { project_id: pid, metric_code: code, target_value: v });
          state.opsProjectTargets[key] = v;
          td.classList.add('has-override');
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

  const rows = nodes.map((n, idx) => {
    const indent = '&nbsp;&nbsp;'.repeat(n.depth || 0);
    const flag = n.is_calc ? '<span class="muted" title="Расчётная строка ПланФакт — не сохраняется как статья">[calc]</span>' :
                 n.is_leaf ? '<span class="muted">[лист]</span>' : '';
    const idAttr = (mode === 'saved') ? `data-id="${n.id}"` : `data-idx="${idx}"`;
    const select = n.is_calc
      ? '<span class="muted">—</span>'
      : `<select class="js-tpl-code" ${idAttr}>${codeOptions(n.pnl_code || '')}</select>`;
    return `<tr class="${n.is_calc ? 'tpl-calc' : ''}">
      <td class="tpl-title">${indent}${esc(n.title)} ${flag}</td>
      <td class="tpl-code-cell">${select}</td>
      <td class="muted tpl-path">${esc(Array.isArray(n.path) ? n.path.join(' / ') : (n.path || ''))}</td>
    </tr>`;
  }).join('');

  box.innerHTML = `
    ${warnHtml}
    <table class="tpl-tree">
      <thead><tr><th>Статья</th><th>P&amp;L-код</th><th class="muted">Полный путь</th></tr></thead>
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

function initProfileTab() {
  const form = document.getElementById('passwordForm');
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
  } catch (e) {
    console.warn('integrations status failed', e);
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
      <td><button class="btn-secondary js-edit-pfkey" data-id="${k.id}">Изменить</button></td>
    </tr>
  `).join('');
  tbody.querySelectorAll('.js-edit-pfkey').forEach(btn => {
    btn.addEventListener('click', () => {
      const k = keys.find(x => x.id === Number(btn.dataset.id));
      if (k) openPfKeyModal(k);
    });
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
async function renderUsersTable() {
  const tbody = document.querySelector('#usersTable tbody');
  if (!tbody) return;
  try {
    const users = await api('/api/admin/users');
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="muted">Пользователей нет.</td></tr>';
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
        <td>${u.is_admin ? '★ да' : 'нет'}</td>
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
  document.getElementById('ueIsAdmin').checked = !!u.is_admin;
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

async function openUserProjectsModal(userId, username) {
  document.getElementById('upTitle').textContent = `Проекты: ${username}`;
  const wrap = document.getElementById('upTableWrap');
  wrap.innerHTML = '<p class="muted">Загрузка…</p>';
  openModal('userProjectsModal');
  let resp;
  try {
    resp = await api(`/api/admin/users/${userId}/projects`);
  } catch (e) {
    wrap.innerHTML = `<p class="neg">Ошибка: ${esc(e.message)}</p>`;
    return;
  }
  if (resp.message) {
    wrap.innerHTML = `<p class="muted" style="padding:14px;background:#fef3c7;border:1px solid #fde68a;border-radius:6px;color:#92400e;">${esc(resp.message)}</p>`;
    return;
  }
  const projects = resp.projects || [];
  if (!projects.length) {
    wrap.innerHTML = '<p class="muted">Под этим PF-ключом нет ни одного проекта.</p>';
    return;
  }
  // Сортируем — активные сверху, потом по name
  projects.sort((a, b) => {
    if (a.is_active !== b.is_active) return a.is_active ? -1 : 1;
    return (a.planfact_name || '').localeCompare(b.planfact_name || '', 'ru');
  });
  wrap.innerHTML = `
    <table class="dense-table">
      <thead><tr>
        <th style="width:60px;text-align:center;">Вкл</th>
        <th>PlanFact-имя</th>
        <th>ID</th>
        <th>Активность в PF</th>
      </tr></thead>
      <tbody>
        ${projects.map(p => `
          <tr data-pid="${esc(p.id)}" class="${p.is_active ? '' : 'row-off'}">
            <td class="cell-center">
              <label class="switch">
                <input type="checkbox" class="js-up-toggle" ${p.is_active ? 'checked' : ''}>
                <span class="slider"></span>
              </label>
            </td>
            <td><strong>${esc(p.planfact_name)}</strong></td>
            <td><code style="font-size:11px;">${esc(p.id)}</code></td>
            <td>${p.planfact_active ? 'да' : '<span class="muted">нет</span>'}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
  wrap.querySelectorAll('tr[data-pid]').forEach(tr => {
    const pid = tr.dataset.pid;
    const chk = tr.querySelector('.js-up-toggle');
    chk.addEventListener('change', async () => {
      try {
        await api(`/api/admin/users/${userId}/projects/${pid}/config`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ is_active: chk.checked }),
        });
        tr.classList.toggle('row-off', !chk.checked);
        toast('Сохранено');
      } catch (e) {
        toast('Ошибка: ' + e.message, 'error');
        chk.checked = !chk.checked;
      }
    });
  });
}

async function initUsersTab() {
  // Скрываем admin-only элементы для не-админов; показываем для админов.
  const isAdmin = !!(state.me && state.me.is_admin);
  document.querySelectorAll('.admin-only').forEach(e => {
    e.classList.toggle('hidden', !isAdmin);
  });
  if (!isAdmin) return;

  // Подгружаем каталоги (PF-ключи + Dodo IS логины) для селектов
  await loadAdminCatalogs();
  initPfKeysCatalog();

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
    const body = {
      username: document.getElementById('cuUsername').value.trim(),
      password: document.getElementById('cuPassword').value,
      display_name: document.getElementById('cuDisplayName').value.trim() || null,
      is_admin: document.getElementById('cuIsAdmin').checked,
      dodois_credentials_name: document.getElementById('cuDodoisName').value || null,
      planfact_key_id: pfKeyId ? Number(pfKeyId) : null,
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
    const body = {
      display_name: document.getElementById('ueDisplayName').value.trim() || null,
      is_admin: document.getElementById('ueIsAdmin').checked,
      dodois_credentials_name: document.getElementById('ueDodoisName').value || null,
    };
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
    if (!confirm(`Удалить пользователя ${username}? Это удалит ВСЕ его данные (проекты, таргеты, шаблон).`)) return;
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
document.addEventListener('DOMContentLoaded', async () => {
  try {
    await loadAll();
    initProfileTab();
    initIntegrationsTab();
    initUsersTab();
  } catch (e) {
    console.error(e);
    toast('Ошибка загрузки: ' + e.message, 'error');
  }
});
