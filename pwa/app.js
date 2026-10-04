/* PWA 外殼：自選股列（原生 HTML，讀 /api）＋ 以 iframe 組合多種 Python UI。 */
(() => {
  "use strict";
  const cfg = window.APP_CONFIG;
  const api = cfg.apiBase.replace(/\/$/, "");
  const $ = (id) => document.getElementById(id);
  const store = {
    get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 無痕模式 */ } },
  };

  const state = {
    stock: store.get("stock", cfg.defaultStock),
    view: store.get("view", cfg.views[0].id),
    quotes: store.get("quotes", []),
    backend: "unknown", // unknown | waking | ready | down
  };
  if (!cfg.views.some((v) => v.id === state.view)) state.view = cfg.views[0].id;

  // ---------- 狀態列 ----------
  function setStatus(kind, text) {
    $("status").dataset.kind = kind;
    $("status-text").textContent = text;
  }

  // ---------- 喚醒後端（Render 免費方案閒置 15 分鐘會休眠）----------
  let wakePromise = null;
  function wakeBackend() {
    if (state.backend === "ready") return Promise.resolve(true);
    if (wakePromise) return wakePromise;
    const ctrl = new AbortController();
    const hardStop = setTimeout(() => ctrl.abort(), 120000);
    const slowHint = setTimeout(() => {
      state.backend = "waking";
      setStatus("waking", "後端喚醒中，約需一分鐘");
      render();
    }, 2500);
    wakePromise = fetch(`${api}/health`, { signal: ctrl.signal, cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(r.status); state.backend = "ready"; setStatus("ok", "已連線"); return true; })
      .catch(() => { state.backend = "down"; setStatus("down", navigator.onLine ? "無法連線到後端" : "離線"); return false; })
      .finally(() => { clearTimeout(hardStop); clearTimeout(slowHint); wakePromise = null; render(); });
    return wakePromise;
  }

  async function loadQuotes() {
    if (!(await wakeBackend())) return;
    try {
      // no-cache：自選股會在網頁上增減，不能拿瀏覽器快取裡 5 分鐘前的清單
      const r = await fetch(`${api}/api/stocks`, { cache: "no-cache" });
      if (!r.ok) throw new Error(r.status);
      state.quotes = await r.json();
      store.set("quotes", state.quotes);
      renderQuotes();
    } catch {
      setStatus("down", "報價讀取失敗，顯示上次資料");
    }
  }

  // ---------- 自選股列 ----------
  const fmt = (n, d = 2) => (n == null ? "–" : Number(n).toLocaleString("zh-TW", { maximumFractionDigits: d }));

  function renderQuotes() {
    const list = [...state.quotes];
    if (!list.some((q) => q.stock_id === state.stock)) list.unshift({ stock_id: state.stock, name: "" });
    $("quotes").replaceChildren(...list.map((q) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      const dir = q.change_pct > 0 ? "up" : q.change_pct < 0 ? "down" : "flat";
      btn.className = `quote ${dir}`;
      btn.type = "button";
      btn.setAttribute("aria-pressed", String(q.stock_id === state.stock));
      const pct = q.change_pct == null ? "" : `${q.change_pct > 0 ? "+" : ""}${q.change_pct.toFixed(2)}%`;
      btn.innerHTML = `
        <span class="q-id"></span><span class="q-name"></span>
        <span class="q-close">${fmt(q.close)}</span><span class="q-pct">${pct}</span>`;
      btn.querySelector(".q-id").textContent = q.stock_id;
      btn.querySelector(".q-name").textContent = q.name && q.name !== q.stock_id ? q.name : "";
      btn.setAttribute("aria-label", `${q.stock_id} ${q.name || ""} 收盤 ${fmt(q.close)} ${pct}`);
      btn.addEventListener("click", () => selectStock(q.stock_id));
      li.append(btn);
      if (q.removable) {
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "q-remove";
        rm.textContent = "×";
        rm.setAttribute("aria-label", `從自選股移除 ${q.stock_id}`);
        rm.addEventListener("click", () => removeStock(q.stock_id));
        li.append(rm);
      }
      return li;
    }));
    // 手機上報價列是橫向捲動，新加入或選取的股票要捲到看得見
    $("quotes").querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function selectStock(id) {
    state.stock = id.trim().toUpperCase();
    store.set("stock", state.stock);
    renderQuotes();
    openFrame();
  }

  // ---------- 畫面切換 ----------
  function renderViews() {
    const wrap = $("views");
    wrap.replaceChildren(...cfg.views.map((v) => {
      const b = document.createElement("button");
      b.type = "button";
      b.role = "tab";
      b.id = `tab-${v.id}`;
      b.className = "view";
      b.setAttribute("aria-selected", String(v.id === state.view));
      b.tabIndex = v.id === state.view ? 0 : -1;
      b.innerHTML = `<span class="v-label"></span><span class="v-engine"></span>`;
      b.querySelector(".v-label").textContent = v.label;
      b.querySelector(".v-engine").textContent = v.engine;
      b.addEventListener("click", () => selectView(v.id));
      return b;
    }));
  }

  $("views").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const i = cfg.views.findIndex((v) => v.id === state.view);
    const next = cfg.views[(i + (e.key === "ArrowRight" ? 1 : -1) + cfg.views.length) % cfg.views.length];
    selectView(next.id);
    $(`tab-${next.id}`).focus();
  });

  function selectView(id) {
    state.view = id;
    store.set("view", id);
    renderViews();
    openFrame();
  }

  // ---------- iframe 組合 ----------
  const frame = $("frame");
  let loadTimer = null;

  function veil(title, body = "", retry = false) {
    $("veil").hidden = !title;
    $("veil-title").textContent = title || "";
    $("veil-body").textContent = body;
    const old = $("veil").querySelector("button");
    if (old) old.remove();
    if (retry) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = "重新載入";
      b.addEventListener("click", reloadFrame);
      $("veil").append(b);
    }
  }

  // 閒置會休眠的主機（例如 Streamlit Community Cloud）：iframe 跨網域，讀不到裡面是否為休眠頁，
  // 所以固定在畫面下方提示；喚醒要在新分頁按按鈕，回到這裡時自動重新載入。
  const wakeDismissed = new Set();
  let wakePending = false;

  function wakeNote(view) {
    const show = Boolean(view?.wakeUrl) && !wakeDismissed.has(view.id);
    $("wake-note").hidden = !show;
    if (!show) return;
    $("wake-text").textContent = `${view.engine} 免費主機閒置會休眠。看到「Zzzz」畫面時，請開新分頁按喚醒按鈕，啟動後回到這裡會自動重新載入。`;
    $("wake-link").href = view.wakeUrl.replace("{stock}", encodeURIComponent(state.stock));
  }

  function reloadFrame() { frame.removeAttribute("src"); openFrame(); }

  $("wake-link").addEventListener("click", () => { wakePending = true; });
  $("wake-reload").addEventListener("click", reloadFrame);
  $("wake-close").addEventListener("click", () => { wakeDismissed.add(state.view); wakeNote(null); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !wakePending) return;
    wakePending = false;
    reloadFrame();
  });

  async function openFrame() {
    const view = cfg.views.find((v) => v.id === state.view);
    const url = view.url.replace("{api}", api).replace("{stock}", encodeURIComponent(state.stock));
    if (frame.getAttribute("src") === url) return;
    frame.title = `${view.label}（${view.engine}）`;
    wakeNote(null);

    if (!navigator.onLine) return veil("目前離線", "連上網路後，分析畫面會自動載入。");
    if (view.needsApi !== false && state.backend !== "ready") {
      veil("後端喚醒中", "免費主機閒置後需要約一分鐘啟動，請稍候。");
      if (!(await wakeBackend())) return veil("無法連線到後端", "請確認 config.js 的 apiBase 設定，或稍後再試。", true);
      if (state.view !== view.id) return; // 等待期間使用者已切換畫面
    }
    veil("載入中", `${view.engine} 畫面`);
    clearTimeout(loadTimer);
    loadTimer = setTimeout(() => veil("載入時間較長", "如果畫面一直空白，可以重新載入。", true), 45000);
    frame.src = url;
    wakeNote(view);
  }

  frame.addEventListener("load", () => {
    if (!frame.getAttribute("src")) return;
    clearTimeout(loadTimer);
    veil(null);
  });

  // ---------- 輸入代號：加入自選股，資料庫沒有資料時後端會立刻抓 ----------
  function inputError(msg) {
    $("add-input").setCustomValidity(msg);
    $("add-input").reportValidity();
  }

  async function addStock(id) {
    const submit = $("add-form").querySelector("button");
    submit.disabled = true;
    // 第一次查看要從 FinMind 下載約 10 秒；很快就回應的（已有資料）不閃提示
    const slow = setTimeout(() => veil("下載資料中", `${id} 第一次查看，正在抓股價、營收與新聞，約需 10 秒。`), 800);
    try {
      if (!(await wakeBackend())) return inputError("無法連線到後端，請稍後再試");
      const r = await fetch(`${api}/api/watchlist/${encodeURIComponent(id)}`, { method: "POST" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) return inputError(body.detail || "加入失敗，請稍後再試");
      state.quotes = [...state.quotes.filter((q) => q.stock_id !== id), body];
      store.set("quotes", state.quotes);
      $("add-input").value = "";
      selectStock(id);
    } catch {
      inputError(navigator.onLine ? "加入失敗，請稍後再試" : "目前離線");
    } finally {
      clearTimeout(slow);
      submit.disabled = false;
      if ($("veil-title").textContent === "下載資料中") veil(null);
    }
  }

  async function removeStock(id) {
    try {
      const r = await fetch(`${api}/api/watchlist/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(r.status);
    } catch {
      return setStatus("down", "移除失敗，請稍後再試");
    }
    state.quotes = state.quotes.filter((q) => q.stock_id !== id);
    store.set("quotes", state.quotes);
    if (state.stock === id) selectStock(state.quotes[0]?.stock_id || cfg.defaultStock);
    else renderQuotes();
  }

  $("add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("add-input").value.trim().toUpperCase();
    if (/^\d{4,6}[A-Z]?$/.test(v)) addStock(v);
    else inputError("請輸入 4 到 6 碼股票代號");
  });
  $("add-input").addEventListener("input", (e) => e.target.setCustomValidity(""));

  // ---------- 類股選股：參考 Yahoo 股市類股分類，先選類股再點代號 ----------
  const picker = { industries: null, stocks: new Map(), current: store.get("industry", null) };

  async function getJSON(path) {
    const r = await fetch(`${api}${path}`);
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }

  function pickNote(text) { $("picks-note").textContent = text; }

  async function openPicker() {
    $("picker").showModal();
    $("picker-filter").value = "";
    if (picker.industries) return renderIndustries();
    pickNote("讀取類股中");
    $("pick-list").replaceChildren();
    if (!(await wakeBackend())) return pickNote("無法連線到後端，請稍後再試");
    try {
      picker.industries = await getJSON("/api/industries");
    } catch {
      return pickNote("類股讀取失敗，請稍後再試");
    }
    if (!picker.industries.length) return pickNote("股票清單還沒建立，請先執行每日排程");
    renderIndustries();
  }

  function renderIndustries() {
    const list = picker.industries;
    if (!list.some((i) => i.industry === picker.current)) picker.current = list[0].industry;
    $("industries").replaceChildren(...list.map((i) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "industry";
      b.setAttribute("aria-pressed", String(i.industry === picker.current));
      b.innerHTML = `<span class="i-name"></span><span class="i-count"></span>`;
      b.querySelector(".i-name").textContent = i.industry;
      b.querySelector(".i-count").textContent = i.count;
      b.addEventListener("click", () => selectIndustry(i.industry));
      li.append(b);
      return li;
    }));
    $("industries").querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
    loadIndustry(picker.current);
  }

  function selectIndustry(name) {
    picker.current = name;
    store.set("industry", name);
    $("picker-filter").value = "";
    renderIndustries();
  }

  async function loadIndustry(name) {
    if (!picker.stocks.has(name)) {
      pickNote(`讀取 ${name} 中`);
      $("pick-list").replaceChildren();
      try {
        picker.stocks.set(name, await getJSON(`/api/industries/stocks?industry=${encodeURIComponent(name)}`));
      } catch {
        return pickNote("股票讀取失敗，請稍後再試");
      }
      if (picker.current !== name) return; // 讀取期間已切換類股
    }
    renderPicks();
  }

  function renderPicks() {
    if (!picker.stocks.has(picker.current)) return; // 還在讀取
    const all = picker.stocks.get(picker.current);
    const q = $("picker-filter").value.trim().toUpperCase();
    const rows = q ? all.filter((s) => s.stock_id.includes(q) || s.name.toUpperCase().includes(q)) : all;
    const watched = new Set(state.quotes.map((s) => s.stock_id));
    pickNote(q ? `${picker.current}：符合「${q}」${rows.length} 檔` : `${picker.current}：共 ${all.length} 檔，點選加入自選股`);
    $("pick-list").replaceChildren(...rows.map((s) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pick-stock";
      b.innerHTML = `<span class="p-id"></span><span class="p-name"></span>`;
      b.querySelector(".p-id").textContent = s.stock_id;
      b.querySelector(".p-name").textContent = s.name;
      if (watched.has(s.stock_id)) {
        b.classList.add("watched");
        b.title = "已在自選股";
      }
      b.addEventListener("click", () => {
        $("picker").close();
        if (watched.has(s.stock_id)) selectStock(s.stock_id);
        else addStock(s.stock_id);
      });
      li.append(b);
      return li;
    }));
  }

  $("pick-open").addEventListener("click", openPicker);
  $("picker-close").addEventListener("click", () => $("picker").close());
  $("picker-filter").addEventListener("input", renderPicks);
  // 點對話框外的半透明背景也關閉
  $("picker").addEventListener("click", (e) => { if (e.target === $("picker")) $("picker").close(); });

  // ---------- 網路狀態 ----------
  window.addEventListener("online", () => { loadQuotes(); openFrame(); });
  window.addEventListener("offline", () => setStatus("down", "離線"));

  function render() { renderViews(); }

  // ---------- 啟動 ----------
  renderQuotes();
  renderViews();
  openFrame();
  loadQuotes();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
})();
