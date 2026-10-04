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
    $("status-retry").hidden = kind !== "down"; // 連不上時一定要有下一步可以按，不讓使用者卡住
  }

  $("status-retry").addEventListener("click", () => {
    if (!navigator.onLine) return toast("目前離線，連上網路後會自動重試", { kind: "error", key: "net" });
    state.backend = "unknown";
    loadQuotes();
    reloadFrame();
  });

  // ---------- 通知：操作失敗一律顯示原因，能重試的附上按鈕（不再只寫在狀態列或靜默略過）----------
  const toasts = new Map(); // key → 元素；同一件事只顯示一則，新的取代舊的
  function toast(text, { kind = "info", action = null, key = text, sticky = kind === "error" && Boolean(action) } = {}) {
    toasts.get(key)?.close();
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    const p = document.createElement("p");
    p.textContent = text;
    el.append(p);
    let timer = null;
    const close = () => {
      clearTimeout(timer);
      el.remove();
      if (toasts.get(key) === handle) toasts.delete(key);
    };
    const handle = { close };
    if (action) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = action.label;
      b.addEventListener("click", () => { close(); action.run(); });
      el.append(b);
    }
    const x = document.createElement("button");
    x.type = "button";
    x.className = "toast-close";
    x.textContent = "×";
    x.setAttribute("aria-label", "關閉通知");
    x.addEventListener("click", close);
    el.append(x);
    if (!sticky && kind !== "progress") timer = setTimeout(close, kind === "error" ? 8000 : 4000);
    $("toasts").append(el);
    toasts.set(key, handle);
    return handle;
  }

  // ---------- API 呼叫：有逾時、會讀出後端的錯誤說明 ----------
  async function request(path, { method = "GET", timeout = 30000, cache } = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout);
    try {
      let r;
      try {
        r = await fetch(`${api}${path}`, { method, cache, signal: ctrl.signal });
      } catch (e) {
        state.backend = "unknown"; // 下次操作重新喚醒，而不是假設後端還在
        if (!navigator.onLine) throw new Error("目前離線");
        throw new Error(e.name === "AbortError" ? "伺服器回應逾時" : "無法連線到後端");
      }
      const body = await r.json().catch(() => null);
      if (!r.ok) {
        const err = new Error(typeof body?.detail === "string" ? body.detail : `伺服器錯誤（${r.status}）`);
        err.status = r.status;
        throw err;
      }
      if (body === null) throw new Error("伺服器回應格式錯誤");
      return body;
    } finally {
      clearTimeout(timer);
    }
  }
  // 連線問題與 5xx 才給「重試」；代號錯誤、超過上限這類 4xx 重試也一樣，只顯示原因
  const retryable = (e) => !e.status || e.status >= 500;

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
    if (!(await wakeBackend())) return; // 狀態列已顯示連線失敗與「重試」
    try {
      // no-cache：自選股會在網頁上增減，不能拿瀏覽器快取裡 5 分鐘前的清單
      state.quotes = await request("/api/stocks", { cache: "no-cache" });
      store.set("quotes", state.quotes);
      renderQuotes();
      toasts.get("quotes")?.close();
    } catch (e) {
      const old = state.quotes.length ? "，先顯示上次的資料" : "";
      toast(`報價讀取失敗（${e.message}）${old}`, { kind: "error", key: "quotes", action: { label: "重試", run: loadQuotes } });
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
        rm.dataset.remove = q.stock_id;
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

  const adding = new Set(); // 正在加入的代號：避免連按造成重複請求

  async function addStock(id) {
    if (adding.has(id)) return;
    adding.add(id);
    const submit = $("add-form").querySelector('button[type="submit"]');
    submit.disabled = true;
    submit.textContent = "加入中";
    // 第一次查看要從 FinMind 下載約 10 秒；很快就回應的（已有資料）不閃提示
    let progress = null;
    const slow = setTimeout(() => {
      progress = toast(`${id} 第一次查看，正在下載股價、營收與新聞，約需 10 秒`, { kind: "progress", key: `add-${id}` });
    }, 800);
    const retry = { label: "重試", run: () => addStock(id) };
    try {
      if (!(await wakeBackend())) throw new Error("無法連線到後端");
      const body = await request(`/api/watchlist/${encodeURIComponent(id)}`, { method: "POST", timeout: 90000 });
      state.quotes = [...state.quotes.filter((q) => q.stock_id !== id), body];
      store.set("quotes", state.quotes);
      if ($("add-input").value.trim().toUpperCase() === id) $("add-input").value = "";
      selectStock(id);
      if (body.fetched) toast(`${id} ${body.name || ""} 資料已下載，已加入自選股`, { kind: "ok", key: `add-${id}` });
      else progress?.close();
    } catch (e) {
      toast(`${id} 加入失敗：${e.message}`, { kind: "error", key: `add-${id}`, action: retryable(e) ? retry : null });
    } finally {
      clearTimeout(slow);
      adding.delete(id);
      if (!adding.size) {
        submit.disabled = false;
        submit.textContent = "查看";
      }
    }
  }

  async function removeStock(id) {
    const btn = () => $("quotes").querySelector(`[data-remove="${CSS.escape(id)}"]`);
    if (btn()) btn().disabled = true;
    try {
      if (!(await wakeBackend())) throw new Error("無法連線到後端");
      await request(`/api/watchlist/${encodeURIComponent(id)}`, { method: "DELETE" });
    } catch (e) {
      if (btn()) btn().disabled = false;
      const retry = retryable(e) ? { label: "重試", run: () => removeStock(id) } : null;
      return toast(`${id} 移除失敗：${e.message}`, { kind: "error", key: `rm-${id}`, action: retry });
    }
    state.quotes = state.quotes.filter((q) => q.stock_id !== id);
    store.set("quotes", state.quotes);
    if (state.stock === id) selectStock(state.quotes[0]?.stock_id || cfg.defaultStock);
    else renderQuotes();
    // 移除不先跳確認框，改成事後可以復原：少一個步驟，也不怕按錯
    toast(`已從自選股移除 ${id}`, { kind: "ok", key: `rm-${id}`, action: { label: "復原", run: () => addStock(id) } });
  }

  $("add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("add-input").value.trim().toUpperCase();
    if (/^\d{4,6}[A-Z]?$/.test(v)) addStock(v);
    else inputError(v ? "請輸入 4 到 6 碼股票代號，例如 2330" : "請先輸入股票代號，或按「類股」挑選");
  });
  $("add-input").addEventListener("input", (e) => e.target.setCustomValidity(""));

  // ---------- 類股選股：參考 Yahoo 股市類股分類，先選上市／上櫃與類股再點代號 ----------
  const MARKETS = [["twse", "上市"], ["tpex", "上櫃"], ["emerging", "興櫃"]];
  const marketLabel = (m) => MARKETS.find(([k]) => k === m)?.[1] || "未分類"; // 未分類：排程還沒抓到上市櫃別
  const picker = {
    industries: null, stocks: new Map(),
    market: store.get("market", "twse"), current: store.get("industry", null),
  };
  const pickKey = () => `${picker.market}|${picker.current}`;

  // 讀取失敗時在訊息旁放「重試」，不必關掉對話框再開一次
  function pickNote(text, retry = null) {
    const note = $("picks-note");
    note.textContent = text;
    if (!retry) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "picks-retry";
    b.textContent = "重試";
    b.addEventListener("click", retry);
    note.append(b);
  }

  async function openPicker() {
    if (!$("picker").open) $("picker").showModal();
    $("picker-filter").value = "";
    if (picker.industries) return renderIndustries();
    pickNote("讀取類股中");
    $("industries").replaceChildren();
    $("pick-list").replaceChildren();
    try {
      if (!(await wakeBackend())) throw new Error("無法連線到後端");
      const list = await request("/api/industries");
      if (!list.length) return pickNote("股票清單還沒建立，請先執行每日排程；也可以直接在上方輸入代號查看", openPicker);
      picker.industries = list; // 空清單不快取，排程跑完後重開就會讀到
    } catch (e) {
      return pickNote(`類股讀取失敗：${e.message}`, openPicker);
    }
    renderIndustries();
  }

  function renderMarkets() {
    const keys = [...new Set(picker.industries.map((i) => i.market ?? ""))]
      .sort((a, b) => (MARKETS.findIndex(([k]) => k === a) + 1 || 99) - (MARKETS.findIndex(([k]) => k === b) + 1 || 99));
    if (!keys.includes(picker.market)) picker.market = keys[0];
    $("markets").replaceChildren(...keys.map((m) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "market";
      b.setAttribute("aria-pressed", String(m === picker.market));
      b.textContent = marketLabel(m);
      b.addEventListener("click", () => selectMarket(m));
      return b;
    }));
  }

  function selectMarket(m) {
    picker.market = m;
    store.set("market", m);
    $("picker-filter").value = "";
    renderIndustries();
  }

  function renderIndustries() {
    renderMarkets();
    const list = picker.industries.filter((i) => (i.market ?? "") === picker.market);
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
    loadIndustry();
  }

  function selectIndustry(name) {
    picker.current = name;
    store.set("industry", name);
    $("picker-filter").value = "";
    renderIndustries();
  }

  async function loadIndustry() {
    const key = pickKey();
    if (!picker.stocks.has(key)) {
      pickNote(`讀取 ${picker.current} 中`);
      $("pick-list").replaceChildren();
      const q = new URLSearchParams({ industry: picker.current });
      if (picker.market) q.set("market", picker.market);
      try {
        picker.stocks.set(key, await request(`/api/industries/stocks?${q}`));
      } catch (e) {
        if (pickKey() === key) pickNote(`${picker.current} 讀取失敗：${e.message}`, loadIndustry);
        return;
      }
      if (pickKey() !== key) return; // 讀取期間已切換類股
    }
    renderPicks();
  }

  function renderPicks() {
    if (!picker.stocks.has(pickKey())) return; // 還在讀取
    const all = picker.stocks.get(pickKey());
    const where = `${marketLabel(picker.market)}・${picker.current}`;
    const q = $("picker-filter").value.trim().toUpperCase();
    const rows = q ? all.filter((s) => s.stock_id.includes(q) || s.name.toUpperCase().includes(q)) : all;
    const watched = new Set(state.quotes.map((s) => s.stock_id));
    pickNote(!q ? `${where}：共 ${all.length} 檔，點選加入自選股`
      : rows.length ? `${where}：符合「${q}」${rows.length} 檔${rows.length === 1 ? "，按 Enter 直接加入" : ""}`
      : `${where}沒有符合「${q}」的股票，可以換個類股，或關掉視窗直接輸入代號`);
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
      b.addEventListener("click", () => pickStock(s.stock_id));
      li.append(b);
      return li;
    }));
  }

  function pickStock(id) {
    $("picker").close();
    if (state.quotes.some((q) => q.stock_id === id)) selectStock(id);
    else addStock(id);
  }

  $("pick-open").addEventListener("click", openPicker);
  // 篩選到只剩一檔時按 Enter 直接加入，不用再移動到清單點選
  $("picker-filter").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing) return;
    e.preventDefault();
    const only = $("pick-list").querySelectorAll(".pick-stock");
    if (only.length === 1) only[0].click();
  });
  $("picker-close").addEventListener("click", () => $("picker").close());
  $("picker-filter").addEventListener("input", renderPicks);
  // 點對話框外的半透明背景也關閉
  $("picker").addEventListener("click", (e) => { if (e.target === $("picker")) $("picker").close(); });

  // ---------- 網路狀態 ----------
  window.addEventListener("online", () => {
    if (state.backend === "ready") setStatus("ok", "已連線"); // 已喚醒時 wakeBackend 不會再更新狀態列
    loadQuotes();
    openFrame();
  });
  window.addEventListener("offline", () => setStatus("down", "離線"));

  function render() { renderViews(); }

  // ---------- 啟動 ----------
  renderQuotes();
  renderViews();
  openFrame();
  loadQuotes();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch((e) => console.warn("Service worker 註冊失敗，離線時無法開啟：", e));
  }
})();
