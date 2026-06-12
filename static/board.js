// /board — оперативная сводка дня по сети.
// Тянем /api/board, рендерим hero + карточки, auto-refresh каждые 60с.


const RU_MONTH_NOM_PL = [
  "январь", "февраль", "март", "апрель", "май", "июнь",
  "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
];
const RU_MONTH_GEN = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];
const RU_WEEKDAY_GEN = [
  "воскресенье", "понедельник", "вторник", "среду", "четверг",
  "пятницу", "субботу",
];
const RU_WEEKDAY_NOM = [
  "Воскресенье", "Понедельник", "Вторник", "Среда", "Четверг",
  "Пятница", "Суббота",
];
const RU_MONTH_PREP = [
  "январе", "феврале", "марте", "апреле", "мае", "июне",
  "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
];

function el(id) { return document.getElementById(id); }

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Форматирование целого числа с пробелами между разрядами.
function fmt(n) {
  if (n == null || isNaN(n)) return "—";
  const i = Math.round(Number(n));
  return i.toLocaleString("ru-RU").replace(/,/g, " ").replace(/ /g, " ");
}

// Δ% с знаком и стрелкой. dirArrow=true → добавляет ↗ / ↘.
function fmtDelta(d, opts = {}) {
  if (d == null || isNaN(d)) return { text: "", cls: "" };
  const pct = d * 100;
  const sign = pct > 0 ? "+" : (pct < 0 ? "−" : "");
  const val = Math.abs(pct).toFixed(1).replace(/\.0$/, "").replace(".", ",");
  const arrow = opts.dirArrow
    ? (pct > 0 ? " ↗" : (pct < 0 ? " ↘" : ""))
    : "";
  // Ровно 0% — нейтральный (как в fmtDeltaShort), не зелёный.
  const cls = pct > 0 ? "pos" : (pct < 0 ? "neg" : "");
  return { text: `${sign}${val}%${arrow}`, cls };
}

// Из ISO yyyy-mm → "июнь" / "июня" / "июне".
function ruMonth(monthIsoYM, form = "nom") {
  const m = parseInt((monthIsoYM || "").split("-")[1], 10) - 1;
  if (isNaN(m) || m < 0 || m > 11) return "";
  if (form === "gen") return RU_MONTH_GEN[m];
  if (form === "prep") return RU_MONTH_PREP[m];
  return RU_MONTH_NOM_PL[m];
}

// "среду" из dateIso. Используется в фразе «vs прошл. среду».
function weekdayGen(dateIso) {
  const d = new Date(dateIso + "T00:00:00+03:00");
  return RU_WEEKDAY_GEN[d.getDay()];
}
// "Среда" — именительный, для заголовков.
function weekdayNom(dateIso) {
  const d = new Date(dateIso + "T00:00:00+03:00");
  return RU_WEEKDAY_NOM[d.getDay()];
}

// "4 июня" из dateIso
function dayMonth(dateIso) {
  const d = new Date(dateIso + "T00:00:00+03:00");
  return d.getDate() + " " + RU_MONTH_GEN[d.getMonth()];
}

let lastPayload = null;
let lastFetchAt = 0;
let counterTimer = null;

// Вид: "compact" (table-style карточки) или "rich" (детальный).
// Запоминается в localStorage.
function getViewMode() {
  // Дефолт — rich (Метрики). Если в localStorage явно "compact"
  // (Выручка) — отдаём compact, иначе rich.
  return localStorage.getItem("boardView") === "compact" ? "compact" : "rich";
}
function setViewMode(v) {
  if (v !== "rich" && v !== "compact") return;
  localStorage.setItem("boardView", v);
}

// Сортировка: cycle через режимы. Сохраняется в localStorage.
const SORT_MODES = [
  { key: "day_asc",   label: "от худшего к лучшему по Δ дня",
    sub: "day-Δ ↑",
    cmp: (a, b) => (numOrInf(a.day?.delta_pct) - numOrInf(b.day?.delta_pct)) },
  { key: "day_desc",  label: "от лучшего к худшему по Δ дня",
    sub: "day-Δ ↓",
    cmp: (a, b) => (numOrNegInf(b.day?.delta_pct) - numOrNegInf(a.day?.delta_pct)) },
  { key: "revenue",   label: "по выручке дня",
    sub: "выручка ↓",
    cmp: (a, b) => (b.day?.value || 0) - (a.day?.value || 0) },
  { key: "name",      label: "по алфавиту",
    sub: "А → Я",
    cmp: (a, b) => (a.name || "").localeCompare(b.name || "", "ru") },
];
function numOrInf(x)    { return x == null ? Number.POSITIVE_INFINITY : x; }
function numOrNegInf(x) { return x == null ? Number.NEGATIVE_INFINITY : x; }

function getSortMode() {
  const k = localStorage.getItem("boardSort") || "day_asc";
  return SORT_MODES.find(m => m.key === k) || SORT_MODES[0];
}
function setSortMode(key) {
  localStorage.setItem("boardSort", key);
}
function nextSortMode(curKey) {
  const i = SORT_MODES.findIndex(m => m.key === curKey);
  return SORT_MODES[(i + 1) % SORT_MODES.length];
}

async function fetchBoard() {
  // Передаём фильтр выбранных проектов в backend.
  const params = new URLSearchParams();
  if (selectedProjectIds && selectedProjectIds.size) {
    selectedProjectIds.forEach(pid => params.append("project_ids", pid));
  }
  const url = params.toString()
    ? `/api/board?${params}` : "/api/board";
  const r = await fetch(url, { credentials: "same-origin" });
  if (r.status === 401) {
    // Сессия протухла — на логин с возвратом сюда (как api() в app.js).
    location.href = "/login?next=" + encodeURIComponent("/board");
    throw new Error("Требуется вход");
  }
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`${r.status}: ${t.slice(0, 200)}`);
  }
  return r.json();
}

// ─── Drawer (выбор проектов) ──────────────────────────────────────
let allProjects = [];           // [{id, name, ...}] из /api/projects
let selectedProjectIds = new Set();  // Set<string>
let currentUsername = null;

async function loadUser() {
  try {
    const r = await fetch("/auth/me", { credentials: "same-origin" });
    if (r.ok) {
      const me = await r.json();
      currentUsername = me.username || null;
    }
  } catch {}
}

function selectionStorageKey() {
  return `pnlDashboard.selectedProjects.${currentUsername || "default"}`;
}

function loadSavedSelection() {
  try {
    const raw = localStorage.getItem(selectionStorageKey());
    if (!raw) return null;
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.map(String) : null;
  } catch { return null; }
}

function saveSelection(set) {
  try {
    localStorage.setItem(selectionStorageKey(), JSON.stringify([...set]));
  } catch {}
}

async function loadProjects() {
  try {
    const r = await fetch("/api/projects", { credentials: "same-origin" });
    if (!r.ok) return;
    const data = await r.json();
    const list = data.projects || [];
    // Фильтр: доступен юзеру + есть dodo_unit_uuid (иначе нет данных для /board).
    // Сохраняем поля группировки — нужны для drawer-render.
    allProjects = list
      .filter(p => p.is_active === true && p.dodo_unit_uuid)
      .map(p => ({
        id: String(p.id),
        name: p.name || p.planfact_name || String(p.id),
        project_group_title: p.project_group_title || null,
        project_group_is_undistributed: !!p.project_group_is_undistributed,
      }));
    // Применяем сохранённый выбор; если ничего — все доступные.
    const saved = loadSavedSelection();
    if (saved !== null) {
      const allIds = new Set(allProjects.map(p => p.id));
      selectedProjectIds = new Set(saved.filter(id => allIds.has(id)));
    } else {
      selectedProjectIds = new Set(allProjects.map(p => p.id));
    }
  } catch (e) {
    console.warn("loadProjects failed", e);
  }
}

// Группировка по project_group_title — идентично логике на /
function groupProjects(projects) {
  const buckets = new Map();
  for (const p of projects) {
    const key = p.project_group_title || "Без группы";
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
    // «Текущий бизнес» наверху, «Без группы» в самом низу, остальное А→Я.
    if (a.title === "Текущий бизнес") return -1;
    if (b.title === "Текущий бизнес") return 1;
    if (a.is_undistributed && !b.is_undistributed) return 1;
    if (!a.is_undistributed && b.is_undistributed) return -1;
    return a.title.localeCompare(b.title, "ru");
  });
  // Сортировка проектов внутри группы — по имени
  arr.forEach(g => g.projects.sort((a, b) =>
    (a.name || "").localeCompare(b.name || "", "ru")));
  return arr;
}

// Свёрнутые группы — общий localStorage ключ с / (чтобы состояние совпадало)
const GROUP_COLLAPSED_KEY = "pnlDashboard.collapsedGroups";
function loadCollapsedGroups() {
  try {
    const raw = localStorage.getItem(GROUP_COLLAPSED_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch { return new Set(); }
}
function saveCollapsedGroups(s) {
  try { localStorage.setItem(GROUP_COLLAPSED_KEY, JSON.stringify([...s])); }
  catch {}
}

// Staged-выбор (паттерн как на index): тумблеры меняют ТОЛЬКО
// stagedSelection, бэк не дёргается. «Применить» подтягивает
// selectedProjectIds к staged и делает ОДИН /api/board.
let stagedSelection = null; // Set<string> | null (init при первом рендере)

function _setsEqual(a, b) {
  if (!a || !b || a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}

function refreshApplyBar() {
  // Бар статичный (всегда виден); .dirty подсвечивает несохранённые
  // изменения текстом «Изменения не применены».
  const bar = el("projApplyBar");
  if (!bar) return;
  bar.classList.toggle("dirty",
    !_setsEqual(stagedSelection, selectedProjectIds));
}

function renderProjectsList() {
  const box = el("projectsList");
  if (!box) return;
  if (!allProjects.length) {
    box.innerHTML = `<p style="padding:12px;color:var(--muted)">Нет доступных проектов</p>`;
    return;
  }
  if (stagedSelection === null) stagedSelection = new Set(selectedProjectIds);
  const groups = groupProjects(allProjects);
  const collapsed = loadCollapsedGroups();

  box.innerHTML = groups.map(g => {
    const isCollapsed = collapsed.has(g.title);
    const onN = g.projects.filter(p => stagedSelection.has(p.id)).length;
    const total = g.projects.length;
    const allOn = onN === total;
    const noneOn = onN === 0;
    const indeterminate = !allOn && !noneOn;
    return `
      <div class="proj-group" data-group="${esc(g.title)}">
        <div class="proj-group-head" data-toggle="${esc(g.title)}">
          <label class="switch js-stop">
            <input type="checkbox" class="js-grp-toggle" data-grp="${esc(g.title)}"
              ${allOn ? "checked" : ""} ${indeterminate ? 'data-indeterminate="1"' : ""}>
            <span class="slider"></span>
          </label>
          <span class="proj-group-caret">${isCollapsed ? "▸" : "▾"}</span>
          <span class="proj-group-title">${esc(g.title)}</span>
          <span class="proj-group-count">${onN}/${total}</span>
        </div>
        <div class="proj-group-body" ${isCollapsed ? "hidden" : ""}>
          ${g.projects.map(p => `
            <label class="proj-row">
              <span class="switch">
                <input type="checkbox" data-pid="${esc(p.id)}"
                  ${stagedSelection.has(p.id) ? "checked" : ""}>
                <span class="slider"></span>
              </span>
              <span class="proj-name">${esc(p.name)}</span>
            </label>
          `).join("")}
        </div>
      </div>
    `;
  }).join("") + `
    <div id="projApplyBar" class="proj-apply-bar">
      <div class="bar-text">Изменения не применены</div>
      <div class="bar-actions">
        <button type="button" class="btn-cancel" id="projApplyReset">Сбросить</button>
        <button type="button" class="btn-apply" id="projApplyBtn">Применить</button>
      </div>
    </div>
  `;

  // indeterminate (атрибутом не задаётся)
  box.querySelectorAll('input[data-indeterminate="1"]').forEach(cb => {
    cb.indeterminate = true;
  });

  refreshApplyBar();

  // Тумблер группы — toggle все проекты группы (только staged)
  box.querySelectorAll("input.js-grp-toggle").forEach(cb => {
    cb.closest(".js-stop")?.addEventListener("click", e => e.stopPropagation());
    cb.addEventListener("change", () => {
      const grp = cb.dataset.grp;
      const target = cb.checked;
      const grpProj = allProjects.filter(p =>
        (p.project_group_title || "Без группы") === grp);
      grpProj.forEach(p => {
        if (target) stagedSelection.add(p.id);
        else stagedSelection.delete(p.id);
      });
      renderProjectsList();
    });
  });

  // Свёртка/раскрытие группы по клику на header (не по тумблеру)
  box.querySelectorAll(".proj-group-head").forEach(h => {
    h.addEventListener("click", e => {
      if (e.target.closest(".js-stop, input, label")) return;
      const t = h.dataset.toggle;
      const c = loadCollapsedGroups();
      if (c.has(t)) c.delete(t); else c.add(t);
      saveCollapsedGroups(c);
      renderProjectsList();
    });
  });

  // Per-project toggle (только staged)
  box.querySelectorAll("input[data-pid]").forEach(cb => {
    cb.addEventListener("change", () => {
      const pid = cb.dataset.pid;
      if (cb.checked) stagedSelection.add(pid);
      else stagedSelection.delete(pid);
      refreshApplyBar();
      // Полный re-render не нужен для одиночного тумблера — обновляем
      // только счётчик/индикатор группы через перерисовку.
      renderProjectsList();
    });
  });

  // Применить — единственное место, где staged-выбор уходит на бэк.
  el("projApplyBtn")?.addEventListener("click", () => {
    selectedProjectIds = new Set(stagedSelection);
    saveSelection(selectedProjectIds);
    refreshApplyBar();
    // На мобиле закрываем drawer, чтобы юзер увидел результат.
    if (document.body.classList.contains("drawer-open")) toggleDrawer(false);
    reloadBoardData();
  });
  // Сбросить — откатить staged к применённому.
  el("projApplyReset")?.addEventListener("click", () => {
    stagedSelection = new Set(selectedProjectIds);
    renderProjectsList();
  });
}

// Race-guard: при параллельных reloadах рендерим только последний ответ
// (ответы могут приходить не по порядку — stale перетёр бы свежий).
let boardLoadCounter = 0;

async function reloadBoardData() {
  // Пустой выбор: не дёргаем API (иначе запрос без project_ids = весь ключ),
  // показываем empty-state.
  if (selectedProjectIds.size === 0) {
    lastPayload = { now_msk: new Date().toISOString(), projects: [], totals: {} };
    lastFetchAt = Date.now();
    renderEmptyState();
    return;
  }
  const myLoad = ++boardLoadCounter;
  el("boardLoading").classList.remove("hidden");
  try {
    const p = await fetchBoard();
    if (myLoad !== boardLoadCounter) return; // пришёл stale-ответ
    lastPayload = p;
    lastFetchAt = Date.now();
    renderHero(p);
    renderCards(p);
    showContent();
  } catch (e) {
    if (myLoad !== boardLoadCounter) return;
    showError(e.message);
  }
}

// Empty-state: ни одной выбранной пиццерии. Вместо краша renderHero
// (p.totals.day === undefined) — подсказка, как включить точки.
// NB: #boardEmpty — статичный элемент в HTML. Не пишем innerHTML в
// #boardCards: внутри секции живут #cardsGrid/#richGrid, их затирание
// ломало повторный выбор точек («null is not an object … cardsGrid»).
function renderEmptyState() {
  el("boardLoading").classList.add("hidden");
  el("boardError").classList.add("hidden");
  el("boardHero").classList.add("hidden");
  el("pageHeader").classList.add("hidden");
  el("boardCards").classList.add("hidden");
  el("boardEmpty").classList.remove("hidden");
}

function toggleDrawer(open) {
  document.body.classList.toggle("drawer-open",
    typeof open === "boolean" ? open : !document.body.classList.contains("drawer-open"));
  const bd = el("drawerBackdrop");
  if (bd) bd.hidden = !document.body.classList.contains("drawer-open");
}

// Видимость ops-метрик per PF-ключ. {code: bool}. Если ключ не в карте
// (например запрос упал) — считаем метрику видимой.
let boardMetricVisibility = {};
async function fetchBoardMetricsVisibility() {
  try {
    const r = await fetch("/api/board-metrics", { credentials: "same-origin" });
    if (!r.ok) return;
    const data = await r.json();
    const map = {};
    (data.metrics || []).forEach(m => { map[m.code] = m.is_visible !== false; });
    boardMetricVisibility = map;
  } catch (e) {
    // ignore — рендер отработает с default-true для всех метрик
  }
}
function isBoardMetricVisible(code) {
  return boardMetricVisibility[code] !== false;
}

function renderHero(p) {
  // (boardTitle убран из topbar — теперь там toggle [День][Месяц][Период])

  // Page header: большой заголовок + subtitle с датой/часом.
  const wd = weekdayNom(p.today_date);
  const dm = dayMonth(p.today_date);
  el("pageSubtitle").innerHTML =
    `<span class="accent">${esc(wd)}, ${esc(dm)}</span>` +
    ` · данные до <span class="accent">${esc(p.to_hour)}</span> MSK`;
  el("pageHeader").classList.remove("hidden");

  // === День ===
  // Guard: payload без totals (пустой выбор и т.п.) не должен ронять рендер.
  if (!p.totals || !p.totals.day) { renderEmptyState(); return; }
  const day = p.totals.day;
  el("dayNum").innerHTML =
    fmt(day.value) + ' <span class="unit">₽</span>';
  const dayD = fmtDelta(day.delta_pct, { dirArrow: true });
  el("dayDelta").className = "panel-delta " + dayD.cls;
  el("dayDelta").textContent = dayD.text;
  el("dayBaseline").innerHTML =
    `vs прошл. ${weekdayGen(p.last_week_date)} · ${fmt(day.baseline)} ₽`;
  renderChannels("dayChannels", day.channels);

  // === Месяц LFL ===
  const m = p.totals.month;
  const monthName = ruMonth(p.month, "nom");
  const lyMonthName = ruMonth(p.last_year_month, "nom");
  const lyYear = (p.last_year_month || "").split("-")[0];
  el("monthLabel").textContent = `Месяц LFL · ${monthName}`;
  el("monthNum").innerHTML =
    fmt(m.value) + ' <span class="unit">₽</span>';
  const mD = fmtDelta(m.delta_pct, { dirArrow: true });
  el("monthDelta").className = "panel-delta " + mD.cls;
  el("monthDelta").textContent = mD.text;
  el("monthBaseline").innerHTML =
    `vs ${esc(lyMonthName)} ${esc(lyYear)} · ${fmt(m.baseline)} ₽`;
  renderChannels("monthChannels", m.channels);

  // === Прогноз ===
  const f = p.totals.forecast;
  el("forecastLabel").textContent = `Прогноз ${ruMonth(p.month, "gen")}`;
  el("forecastNum").innerHTML =
    fmt(f.value) + ' <span class="unit">₽</span>';
  const fD = fmtDelta(f.delta_pct, { dirArrow: true });
  el("forecastDelta").className = "panel-delta " + fD.cls;
  el("forecastDelta").textContent = fD.text;
  const methodLabel = f.method === "lfl" ? "по LFL"
                     : f.method === "pace" ? "по темпу"
                     : "";
  el("forecastBaseline").innerHTML =
    `vs LY ${fmt(f.ly_full)} ₽` +
    (methodLabel ? ` <span class="method">· ${methodLabel}</span>` : "");
}

function renderChannels(containerId, channels) {
  const c = el(containerId);
  if (!channels) { c.innerHTML = ""; return; }
  const rows = [
    { name: "Доставка", data: channels.delivery },
    { name: "Ресторан", data: channels.restaurant },
  ];
  c.innerHTML = rows.map(({ name, data }) => {
    if (!data) return "";
    const d = fmtDelta(data.delta_pct);
    const base = data.baseline != null
      ? `<span class="pch-base">${fmt(data.baseline)} ₽</span>` : "";
    return `
      <div class="panel-ch-row">
        <span class="pch-name">${esc(name)}</span>
        <span class="pch-val">${fmt(data.value)} ₽ ${base}</span>
        <span class="pch-delta ${d.cls}">${d.text || ""}</span>
      </div>`;
  }).join("");
}

// «Выручка» card — тот же r-card chrome как «Метрики», но контент
// сфокусирован на выручке (День + Месяц), без stops/ops-grid.
function renderCard(b, n) {
  const day = b.day || {};
  const month = b.month || {};
  const f = b.forecast || {};

  const dayPill = fmtDayPill(day.delta_pct);
  const monthPill = fmtDayPill(month.delta_pct);
  const fShort = fmtDeltaShort(f.delta_pct);

  // crit/warn/ok bucket — только по day Δ% (без stops в этом виде)
  let bucket = "ok";
  if (day.delta_pct != null) {
    if (day.delta_pct <= -0.10) bucket = "crit";
    else if (day.delta_pct <= -0.03) bucket = "warn";
  }

  const dDel = (day.channels || {}).delivery || {};
  const dRest = (day.channels || {}).restaurant || {};
  const mDel = (month.channels || {}).delivery || {};
  const mRest = (month.channels || {}).restaurant || {};

  const channelsLine = (del, rest) => {
    const dShort = fmtDeltaShort(del.delta_pct);
    const rShort = fmtDeltaShort(rest.delta_pct);
    return `<span class="name">Дост</span> <span class="val">${fmt(del.value)}</span> ` +
      `<span class="d ${dShort.cls}">${dShort.text}</span>` +
      `<span class="sep">·</span>` +
      `<span class="name">Рест</span> <span class="val">${fmt(rest.value)}</span> ` +
      `<span class="d ${rShort.cls}">${rShort.text}</span>`;
  };

  return `
    <article class="r-card ${bucket}">
      <div class="r-head">
        <span class="r-name">${esc(b.name)}</span>
        <span class="r-head-meta">
          <span class="r-day-delta ${dayPill.cls}">${dayPill.text}</span>
        </span>
      </div>

      <div class="r-section-label">День · vs ${esc(weekdayGen(lastPayload?.last_week_date))}</div>
      <div class="r-revenue">
        <span class="num">${fmt(day.value)} <span class="unit">₽</span></span>
        <span class="base">vs ${fmt(day.baseline)}</span>
      </div>
      <div class="r-channels">${channelsLine(dDel, dRest)}</div>

      <div class="r-section-label">Месяц · vs LFL</div>
      <div class="r-revenue">
        <span class="num">${fmt(month.value)} <span class="unit">₽</span></span>
        <span class="base">vs ${fmt(month.baseline)}</span>
        <span class="r-month-delta ${monthPill.cls}">${monthPill.text}</span>
      </div>
      <div class="r-channels">${channelsLine(mDel, mRest)}</div>

      <div class="r-foot">
        <span class="label">Прогноз ${esc(ruMonth(lastPayload?.month, "gen"))}</span>
        <span class="vd">
          <span class="v">${fmt(f.value)} ₽</span>
          <span class="d ${fShort.cls}">${fShort.text || ""}</span>
        </span>
      </div>
    </article>
  `;
}

// ─── Rich card (детальный вид) ──────────────────────────────────────
// Использует те же поля что и компактный card: day, month, forecast.
// Цветовой шум приглушаем: |Δ| ≤ 3% → neutral.

function classifyPctDelta(d) {
  if (d == null || isNaN(d)) return "neutral";
  const abs = Math.abs(d);
  if (abs < 0.03) return "neutral";   // ≤ 3% → нейтральный серый
  return d >= 0 ? "pos" : "neg";
}

// Δ% без стрелки, для inline-каналов и mini-pills (короче).
function fmtDeltaShort(d) {
  if (d == null || isNaN(d)) return { text: "", cls: "neutral" };
  const pct = d * 100;
  const sign = pct > 0 ? "+" : (pct < 0 ? "−" : "");
  const val = Math.abs(pct).toFixed(1).replace(/\.0$/, "").replace(".", ",");
  return { text: `${sign}${val}%`, cls: classifyPctDelta(d) };
}

// Δ-пилл дня — крупная (с стрелкой), цвет с учётом neutral.
function fmtDayPill(d) {
  if (d == null || isNaN(d)) return { text: "—", cls: "neutral" };
  const pct = d * 100;
  const sign = pct > 0 ? "+" : (pct < 0 ? "−" : "");
  const val = Math.abs(pct).toFixed(1).replace(/\.0$/, "").replace(".", ",");
  const arrow = pct > 0.001 ? " ↗" : (pct < -0.001 ? " ↘" : "");
  return { text: `${sign}${val}%${arrow}`, cls: classifyPctDelta(d) };
}

// "31 мин" если меньше часа, иначе "H:MM"
function fmtMinutes(n) {
  if (n == null || isNaN(n)) return "";
  const m = Math.max(0, Math.floor(n));
  if (m < 60) return `${m} мин`;
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

function pluralRu(n, one, few, many) {
  n = Math.abs(n) % 100;
  const n1 = n % 10;
  if (n > 10 && n < 20) return many;
  if (n1 > 1 && n1 < 5) return few;
  if (n1 === 1) return one;
  return many;
}

// Рендер «Раскрывающаяся секция» внутри crit-alert или warn-body.
function renderCritSub(label, items) {
  if (!items || !items.length) return "";
  const rows = items.map((it) => `
    <div class="crit-row">
      <span class="nm">${esc(it.name)}</span>
      <span class="dur">${esc(fmtMinutes(it.minutes))}</span>
    </div>`).join("");
  return `
    <div class="crit-sub">
      <span class="crit-sub-label">${esc(label)}</span>
      ${rows}
    </div>`;
}

function renderStops(stops) {
  const channels = stops?.channels || [];
  const sectors = stops?.sectors || [];
  const products = stops?.products || [];
  const ingredients = stops?.ingredients || [];

  const critCount = channels.length + sectors.length;
  const warnCount = products.length + ingredients.length;

  // CRIT: каналы / сектора. Если есть — поднимаем алерт с раскрытием.
  // В body показываем ВСЕ типы стопов (включая продукты/ингредиенты).
  if (critCount > 0) {
    const summaryParts = [];
    if (channels.length === 1) {
      const c = channels[0];
      summaryParts.push(`${esc(c.name)} ${esc(fmtMinutes(c.minutes))}`);
    } else if (channels.length > 1) {
      summaryParts.push(`${channels.length} ${pluralRu(channels.length, "канал", "канала", "каналов")}`);
    }
    if (sectors.length > 0) {
      summaryParts.push(`${sectors.length} ${pluralRu(sectors.length, "сектор", "сектора", "секторов")}`);
    }
    if (warnCount > 0) {
      summaryParts.push(`${warnCount} ${pluralRu(warnCount, "стоп", "стопа", "стопов")}`);
    }
    const sep = `<span class="sep">·</span>`;
    const summary = summaryParts.map(esc).join(sep);

    return `
      <details class="crit-alert">
        <summary>
          🛑 ${summary}
          <span class="chev">▾</span>
        </summary>
        <div class="crit-alert-body">
          ${renderCritSub("Каналы", channels)}
          ${renderCritSub("Сектора в стопе", sectors)}
          ${renderCritSub("Стопы продуктов и ингредиентов",
                         [...products, ...ingredients])}
        </div>
      </details>`;
  }

  // WARN: только продукты/ингредиенты. Делаем один чип с раскрытием.
  if (warnCount > 0) {
    const items = [...products, ...ingredients];
    const label = `${warnCount} ${pluralRu(warnCount, "стоп", "стопа", "стопов")}`;
    return `
      <div class="warn-chips">
        <details class="warn-details">
          <summary class="warn-chip">⚠ ${esc(label)}<span class="chev">▾</span></summary>
          <div class="warn-body">
            ${renderCritSub("Продукты и ингредиенты", items)}
          </div>
        </details>
      </div>`;
  }

  return "";
}

// ─── Ops-grid (Кухня + Доставка) ──────────────────────────────────
// Метрика-объект из API: {value, baseline, delta, delta_pct, lower_is_better, is_time}.

function fmtNum(v, decimals = 1) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toLocaleString("ru-RU", {
    minimumFractionDigits: 0, maximumFractionDigits: decimals,
  });
}

// Из секунд → "M:SS" если >= 60s, иначе "Ns"
function fmtSeconds(sec) {
  if (sec == null || isNaN(sec)) return "—";
  const s = Math.round(Number(sec));
  if (s < 60) return `${s} с`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

// Δ для ops: цвет инвертируется по lower_is_better. Возвращает {text, cls}.
function fmtOpsDelta(metric) {
  if (!metric) return { text: "", cls: "neutral" };
  const dp = metric.delta_pct;
  if (metric.is_time) {
    // Time-метрики показываем абсолютную дельту в секундах
    const d = metric.delta;
    if (d == null || isNaN(d)) return { text: "—", cls: "neutral" };
    const abs = Math.abs(d);
    if (abs < 15) return { text: "±0с", cls: "neutral" }; // <15с — неважно
    const sign = d > 0 ? "+" : "−";
    const txt = abs < 60 ? `${Math.round(abs)}с` : fmtSeconds(abs);
    // Direction: lower_is_better → +Δ = bad, −Δ = good
    let cls = "neutral";
    if (metric.lower_is_better) cls = d > 0 ? "neg" : "pos";
    else cls = d > 0 ? "pos" : "neg";
    return { text: `${sign}${txt}`, cls };
  }
  // Не-time: показываем %
  if (dp == null || isNaN(dp)) {
    // Если есть delta (количество, как сертификаты) — покажем абсолютной
    if (metric.delta != null && !isNaN(metric.delta)) {
      const d = metric.delta;
      if (Math.abs(d) < 0.5) return { text: "±0", cls: "neutral" };
      const sign = d > 0 ? "+" : "−";
      let cls = "neutral";
      if (metric.lower_is_better) cls = d > 0 ? "neg" : "pos";
      else cls = d > 0 ? "pos" : "neg";
      return { text: `${sign}${Math.abs(Math.round(d))}`, cls };
    }
    return { text: "—", cls: "neutral" };
  }
  if (Math.abs(dp) < 0.03) return { text: "±0%", cls: "neutral" };
  const pct = dp * 100;
  const sign = pct > 0 ? "+" : "−";
  const val = Math.abs(pct).toFixed(1).replace(/\.0$/, "").replace(".", ",");
  let cls = "neutral";
  if (metric.lower_is_better) cls = pct > 0 ? "neg" : "pos";
  else cls = pct > 0 ? "pos" : "neg";
  return { text: `${sign}${val}%`, cls };
}

// Формат value метрики: число или MM:SS
function fmtOpsValue(metric, opts = {}) {
  if (!metric || metric.value == null) return "—";
  if (metric.is_time) return fmtSeconds(metric.value);
  return fmtNum(metric.value, opts.decimals);
}

// `code` — для проверки видимости через /api/board-metrics. Если метрика
// отключена в настройках, строка не рендерится вообще.
function renderOpsRow(code, name, metric, opts = {}) {
  if (!isBoardMetricVisible(code)) return "";
  if (!metric) return "";
  const v = fmtOpsValue(metric, opts);
  const d = fmtOpsDelta(metric);
  const unitHtml = opts.unit ? `<span class="u"> ${esc(opts.unit)}</span>` : "";
  return `
    <div class="r-ops-row">
      <span class="nm">${esc(name)}</span>
      <span class="vl">${esc(v)}${unitHtml}</span>
      <span class="dl ${d.cls}">${esc(d.text)}</span>
    </div>`;
}

// Курьеры — snapshot {total, in_queue}. Паттерн как в Dodo IS UI:
// «N всего / M в очереди». in_queue берётся из numberOfCouriersInQueue
// последнего заказа (то же поле что использует сам Dodo IS UI).
// Если последний заказ был >30 мин назад — значение stale, показываем
// только «N» без скобок.
function renderCouriersRow(c) {
  if (!isBoardMetricVisible("couriers")) return "";
  if (!c || c.total == null) return "";
  if (c.total === 0) return "";
  const total = c.total;
  const queue = c.in_queue;
  const queueHtml = queue != null
    ? `<span class="u"> / ${esc(String(queue))}</span>`
    : "";
  return `
    <div class="r-ops-row r-courier">
      <span class="nm">Курьеры</span>
      <span class="vl">${esc(String(total))}${queueHtml}</span>
    </div>`;
}

function renderOpsGrid(ops) {
  if (!ops) return "";
  const k = ops.kitchen || {};
  const d = ops.delivery || {};
  // Если совсем нет данных — не показываем секцию.
  const hasAny = [
    k.sales_per_hour?.value, k.products_per_hour?.value,
    k.cooking_delivery_sec?.value, k.cooking_hall_sec?.value,
    k.heated_shelf_sec?.value,
    d.orders_per_courier_hour?.value, d.orders_per_trip?.value,
    d.avg_delivery_sec?.value,
    d.vouchers_count?.value, d.couriers?.total,
  ].some(v => v != null);
  if (!hasAny) return "";

  return `
    <div class="r-ops-grid">
      <div class="r-ops-col">
        <div class="r-ops-label">Кухня</div>
        ${renderOpsRow("sales_per_hour",        "₽/чел·ч",   k.sales_per_hour,    { decimals: 0 })}
        ${renderOpsRow("products_per_hour",     "шт/чел·ч",  k.products_per_hour, { decimals: 1 })}
        ${renderOpsRow("cooking_hall_sec",      "Готовка · зал",      k.cooking_hall_sec)}
        ${renderOpsRow("cooking_delivery_sec",  "Готовка · доставка", k.cooking_delivery_sec)}
      </div>
      <div class="r-ops-col">
        <div class="r-ops-label">Доставка</div>
        ${renderOpsRow("orders_per_courier_hour", "Заказов на курьера", d.orders_per_courier_hour, { decimals: 1 })}
        ${renderOpsRow("orders_per_trip",         "Заказов за поездку", d.orders_per_trip, { decimals: 1 })}
        ${renderOpsRow("avg_delivery_sec",        "Среднее доставки", d.avg_delivery_sec)}
        ${renderOpsRow("heated_shelf_sec",        "На полке",         k.heated_shelf_sec)}
        ${renderOpsRow("vouchers_count", "Сертификаты", d.vouchers_count)}
        ${renderCouriersRow(d.couriers)}
      </div>
    </div>`;
}

function renderRichCard(b) {
  const day = b.day || {};
  const month = b.month || {};
  const f = b.forecast || {};
  const stops = b.stops || {};

  const dayPill = fmtDayPill(day.delta_pct);
  const monthShort = fmtDeltaShort(month.delta_pct);
  const fShort = fmtDeltaShort(f.delta_pct);

  // crit/warn/ok — комбинируем stops + Δ дня.
  const critCount = (stops.channels?.length || 0) + (stops.sectors?.length || 0);
  const warnCount = (stops.products?.length || 0) + (stops.ingredients?.length || 0);
  let bucket = "ok";
  if (critCount > 0) {
    bucket = "crit";
  } else if (day.delta_pct != null && day.delta_pct <= -0.10) {
    bucket = "crit";
  } else if (warnCount > 0) {
    bucket = "warn";
  } else if (day.delta_pct != null && day.delta_pct <= -0.03) {
    bucket = "warn";
  }

  const dDel = (day.channels || {}).delivery || {};
  const dRest = (day.channels || {}).restaurant || {};
  const dDelShort = fmtDeltaShort(dDel.delta_pct);
  const dRestShort = fmtDeltaShort(dRest.delta_pct);

  const channelsLine =
    `<span class="name">Дост</span> <span class="val">${fmt(dDel.value)}</span> ` +
    `<span class="d ${dDelShort.cls}">${dDelShort.text}</span>` +
    `<span class="sep">·</span>` +
    `<span class="name">Рест</span> <span class="val">${fmt(dRest.value)}</span> ` +
    `<span class="d ${dRestShort.cls}">${dRestShort.text}</span>`;

  return `
    <article class="r-card ${bucket}">
      <div class="r-head">
        <span class="r-name">${esc(b.name)}</span>
        <span class="r-head-meta">
          <span class="r-month-mini ${monthShort.cls}">мес <span class="v">${monthShort.text || "—"}</span></span>
          <span class="r-day-delta ${dayPill.cls}">${dayPill.text}</span>
        </span>
      </div>
      ${renderStops(stops)}
      <div class="r-revenue">
        <span class="num">${fmt(day.value)} <span class="unit">₽</span></span>
        <span class="base">vs ${fmt(day.baseline)}</span>
      </div>
      <div class="r-channels">${channelsLine}</div>
      ${renderOpsGrid(b.ops)}
      <div class="r-foot">
        <span class="label">Прогноз ${esc(ruMonth(lastPayload?.month, "gen"))}</span>
        <span class="vd">
          <span class="v">${fmt(f.value)} ₽</span>
          <span class="d ${fShort.cls}">${fShort.text || ""}</span>
        </span>
      </div>
    </article>
  `;
}

function renderCards(p) {
  const mode = getSortMode();
  // Берём копию и сортируем согласно режиму (backend уже отсортировал по
  // day_asc, но юзер может выбрать другой режим).
  const sorted = (p.projects || []).slice().sort(mode.cmp);
  // Переписываем ранки в соответствии с новым порядком.
  // Рендерим оба представления — какое показано, решает CSS .hidden.
  el("cardsGrid").innerHTML = sorted.map(renderCard).join("");
  el("richGrid").innerHTML = sorted.map(renderRichCard).join("");
  // Левая подпись «По пиццериям · …» удалена — режим сортировки
  // показывается только на кнопке справа. «↕» убран: после суффикса
  // вроде «выручка ↓» двойная стрелка выглядела как глюк.
  const btn = el("sortToggleBtn");
  if (btn) {
    btn.textContent = `сортировка · ${mode.sub}`;
    btn.setAttribute("aria-label", `Сортировка: ${mode.label}. Нажмите для смены`);
  }
}

// Видимость двух grid'ов — синхронно с getViewMode().
function applyViewMode() {
  const v = getViewMode();
  el("cardsGrid").classList.toggle("hidden", v !== "compact");
  el("richGrid").classList.toggle("hidden", v !== "rich");
  document.querySelectorAll("#viewSwitch button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === v);
  });
}

function showError(msg) {
  el("boardError").textContent = "Ошибка: " + msg;
  el("boardError").classList.remove("hidden");
  el("boardLoading").classList.add("hidden");
  el("boardHero").classList.add("hidden");
  el("boardCards").classList.add("hidden");
  el("boardEmpty").classList.add("hidden");
}

function showContent() {
  el("boardLoading").classList.add("hidden");
  el("boardError").classList.add("hidden");
  el("boardEmpty").classList.add("hidden");
  el("boardHero").classList.remove("hidden");
  el("boardCards").classList.remove("hidden");
  el("pageHeader").classList.remove("hidden");
}

async function load() {
  try {
    // Сначала юзер и проекты — нужны для filter selection в fetchBoard.
    await loadUser();
    await Promise.all([
      loadProjects(),                  // заполняет selectedProjectIds
      fetchBoardMetricsVisibility(),
    ]);
    renderProjectsList();
    // Если у юзера ничего не выбрано — показываем пустой стейт без вызова API
    if (selectedProjectIds.size === 0) {
      lastPayload = { now_msk: new Date().toISOString(), projects: [], totals: {} };
      lastFetchAt = Date.now();
      renderEmptyState();
      startCounters();
      return;
    }
    const p = await fetchBoard();
    lastPayload = p;
    lastFetchAt = Date.now();
    renderHero(p);
    renderCards(p);
    showContent();
    startCounters();
  } catch (e) {
    showError(e.message);
  }
}

function startCounters() {
  if (counterTimer) clearInterval(counterTimer);
  // Авторефреш отключён — пользователь обновляет данные кнопкой ⟳.
  // Счётчик «обн. N с/мин» показывает свежесть данных.
  counterTimer = setInterval(updateCounters, 1000);
  updateCounters();
  const btn = el("refreshNowBtn");
  if (btn) btn.classList.remove("hidden");
}

function updateCounters() {
  if (!lastFetchAt) return;
  const sinceSec = Math.floor((Date.now() - lastFetchAt) / 1000);
  const counter = el("boardRefresh");
  counter.textContent =
    sinceSec < 60
      ? `обн. ${sinceSec} с`
      : `обн. ${Math.floor(sinceSec / 60)} мин ${sinceSec % 60} с`;
  // После 5 минут — amber: данные пора освежить (кнопка ⟳ рядом).
  counter.classList.toggle("stale", sinceSec >= 300);
  el("nextRefresh").textContent = "данные обновляются кнопкой ⟳ наверху";
}

// Явное обновление данных кнопкой (auto-refresh отключён осознанно —
// rate-limit инциденты Dodo IS; явное действие юзера их не ломает).
async function refreshNow() {
  const btn = el("refreshNowBtn");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  btn.classList.add("spinning");
  try {
    await reloadBoardData();
  } finally {
    btn.disabled = false;
    btn.classList.remove("spinning");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  // Кнопка явного обновления данных в topbar.
  const rBtn = el("refreshNowBtn");
  if (rBtn) rBtn.addEventListener("click", refreshNow);

  // Sort toggle: cycle через режимы, перерисовываем карточки.
  const btn = el("sortToggleBtn");
  if (btn) {
    btn.addEventListener("click", () => {
      const cur = getSortMode();
      const next = nextSortMode(cur.key);
      setSortMode(next.key);
      if (lastPayload) renderCards(lastPayload);
    });
  }
  // View-switch: Сводка / Подробно. localStorage persist.
  const switchEl = el("viewSwitch");
  if (switchEl) {
    switchEl.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-view]");
      if (!b) return;
      setViewMode(b.dataset.view);
      applyViewMode();
    });
  }
  // Настройки видимости метрик — попап с чекбоксами.
  setupMetricsConfigPopover();
  // Высота topbar в CSS-переменную — для позиционирования drawer-panel
  // (top:var(--topbar-h)) и пересчёт при resize.
  const _setTopbarHVar = () => {
    const tb = document.querySelector(".topbar");
    if (!tb) return;
    const h = tb.offsetHeight;
    if (h > 0) document.documentElement.style.setProperty("--topbar-h", h + "px");
  };
  _setTopbarHVar();
  window.addEventListener("resize", _setTopbarHVar);

  // Page-mode toggle (День/Месяц/Период): День active, Месяц/Период → /
  document.querySelectorAll("#pageModeToggle .mode-toggle-btn").forEach((b) => {
    b.addEventListener("click", () => {
      const m = b.dataset.mode;
      if (m === "day") return;  // уже здесь
      window.location.href = `/?mode=${encodeURIComponent(m)}`;
    });
  });
  // Drawer выбора проектов.
  el("drawerToggle")?.addEventListener("click", () => toggleDrawer());
  el("drawerBackdrop")?.addEventListener("click", () => toggleDrawer(false));
  el("drawerCloseBtn")?.addEventListener("click", () => toggleDrawer(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("drawer-open"))
      toggleDrawer(false);
  });
  // Применяем сохранённый вид сразу — до загрузки данных,
  // чтобы при первом render правильный grid был видимым.
  applyViewMode();
  load();
});


// ─── Видимость метрик: попап на /board ──────────────────────────────
// Список метрик и их labels приходит из /api/board-metrics. При toggle
// — PUT на /api/board-metrics/{code}, обновляем boardMetricVisibility
// и перерисовываем карточки.

// Список метрик (с label/group) — лениво подгружаем при первом открытии.
let boardMetricsConfig = [];

async function loadBoardMetricsConfig() {
  try {
    const r = await fetch("/api/board-metrics", { credentials: "same-origin" });
    if (!r.ok) return false;
    const data = await r.json();
    boardMetricsConfig = data.metrics || [];
    // Заодно обновим карту видимости (на случай если /api/board вернул её раньше)
    const map = {};
    boardMetricsConfig.forEach(m => { map[m.code] = m.is_visible !== false; });
    boardMetricVisibility = map;
    return true;
  } catch (e) {
    return false;
  }
}

function renderMetricsConfigPopover() {
  const body = el("metricsCfgBody");
  if (!body) return;
  if (!boardMetricsConfig.length) {
    body.innerHTML = `<p class="muted" style="margin:8px 0">Метрики не загрузились</p>`;
    return;
  }
  // Группируем
  const groups = { kitchen: [], delivery: [] };
  boardMetricsConfig.forEach(m => {
    (groups[m.group] || (groups[m.group] = [])).push(m);
  });
  const titles = { kitchen: "Кухня", delivery: "Доставка" };
  const html = Object.entries(groups).map(([gkey, items]) => {
    if (!items.length) return "";
    return `
      <div class="metrics-cfg-group-label">${esc(titles[gkey] || gkey)}</div>
      ${items.map(m => `
        <label class="metrics-cfg-item">
          <input type="checkbox" data-mc-code="${esc(m.code)}"
                 ${m.is_visible !== false ? "checked" : ""}>
          <span class="nm">${esc(m.label)}</span>
        </label>
      `).join("")}
    `;
  }).join("");
  body.innerHTML = html;
  body.querySelectorAll("input[data-mc-code]").forEach(cb => {
    cb.addEventListener("change", () => onMetricToggle(cb));
  });
}

async function onMetricToggle(cb) {
  const code = cb.dataset.mcCode;
  const is_visible = cb.checked;
  try {
    const r = await fetch(`/api/board-metrics/${encodeURIComponent(code)}`, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_visible }),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    // Локально обновляем
    boardMetricVisibility[code] = is_visible;
    const m = boardMetricsConfig.find(x => x.code === code);
    if (m) m.is_visible = is_visible;
    // Перерисовываем карточки
    if (lastPayload) renderCards(lastPayload);
  } catch (e) {
    cb.checked = !is_visible;
    alert("Не удалось сохранить: " + e.message);
  }
}

function setupMetricsConfigPopover() {
  const btn = el("metricsCfgBtn");
  const popover = el("metricsCfgPopover");
  const closeBtn = el("metricsCfgCloseBtn");
  if (!btn || !popover) return;

  const openPopover = async () => {
    btn.classList.add("active");
    popover.classList.remove("hidden");
    // Грузим список метрик при первом открытии, а потом перерисовываем
    if (!boardMetricsConfig.length) await loadBoardMetricsConfig();
    renderMetricsConfigPopover();
  };
  const closePopover = () => {
    btn.classList.remove("active");
    popover.classList.add("hidden");
  };

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (popover.classList.contains("hidden")) openPopover();
    else closePopover();
  });
  closeBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    closePopover();
  });
  // Закрытие по клику вне попапа
  document.addEventListener("click", (e) => {
    if (popover.classList.contains("hidden")) return;
    if (popover.contains(e.target) || btn.contains(e.target)) return;
    closePopover();
  });
  // Esc закрывает
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !popover.classList.contains("hidden")) {
      closePopover();
    }
  });
}
