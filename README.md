# 台股 AI 分析平台（專案骨架）

Python AI 財經交易實戰：從資料到策略實作。整套部署都使用免費方案。

```
            GitHub Actions（每個交易日 18:30 台灣時間）
  FinMind ──► 擷取 → 指標 → LLM 報告 → Telegram 推播
                        │ 寫入
                        ▼
                 Neon PostgreSQL（免費）
                  ▲                    ▲ 只讀
                  │ 只讀               │
  Render Web Service（免費）      Streamlit Community Cloud（免費）
  FastAPI                          streamlit_app/  自選股總覽
   ├ /api/*        JSON API
   ├ /ui/gradio/   Gradio：K 線＋AI 報告
   ├ /ui/dash/     Dash：月營收
   └ /telegram/webhook
                  ▲ iframe                 ▲ iframe
                  └──── PWA 外殼 ──────────┘
                    Render Static Site（免費、不休眠）
```

## 使用流程

**每個交易日的時間軸**（台灣時間）

| 時間 | 發生什麼事 | 由誰執行 |
|---|---|---|
| 09:00–14:00 | 每 14 分鐘喚醒 Render，盤中開網頁不必等 | `keep-warm.yml` |
| 18:30 起 | 抓股價、營收、法人、本益比、新聞 → 算指標 → 產生 AI 報告 → Telegram 推播一則彙整 | `daily-etl.yml` |
| 每 6 小時 | 喚醒 Streamlit 總覽 | `streamlit-wake.yml` |
| 週六 10:00 | 回測 LLM 與規則式勝率，結果在 Actions 的 Summary | `backtest.yml` |

**三種入口**

| 想做的事 | PWA（網頁） | Telegram | Streamlit |
|---|---|---|---|
| 看一檔股票 | 「代號」輸入後按「查看」，或按「類股」挑選 | 直接傳代號或名稱，例如 `2330`、`台積電` | — |
| 加入自選股 | 按「查看」就會加入；沒有資料時立刻下載（約 10 秒） | `/watch 2330`；沒有資料時立刻下載，完成後通知 | — |
| 看 AI 報告 | 「AI 報告」分頁；可按「重新產生報告」 | `/report 2330` | 「AI 建議」欄 |
| 篩選自選股 | — | `/list` 列出收盤與漲跌 | 依 AI 建議、RSI、訊號篩選 |
| 每日通知 | — | 自選股盤後自動推播 | — |

新加入的股票當天沒有 AI 報告，會在下一次盤後排程產生；急著看可以在 PWA 的「AI 報告」分頁按「重新產生報告」。

## 目錄

```
core/            共用核心（Web、排程、Streamlit 都匯入）
  config.py        環境變數
  db.py models.py  SQLAlchemy；Neon PostgreSQL，本機退回 SQLite
  repository.py    upsert 與查詢
  data_sources.py  FinMind API
  indicators.py    MA、RSI、KD、MACD、布林（純 pandas）
  llm.py           多家 LLM 免費額度，依序備援
  report.py        建議買賣報告
  backtest.py      回測：AI 建議與規則式判斷的勝率
  notify.py        Telegram 發訊
app/             Render Web Service
  main.py          FastAPI，掛載 Gradio 與 Dash
  api.py           /api/*
  telegram_bot.py  Telegram 指令
  charts.py        Plotly 圖表（紅漲綠跌）
  ui/gradio_app.py ui/dash_app.py
streamlit_app/   Streamlit Community Cloud
pwa/             PWA 外殼（純靜態）
jobs/            排程與工具腳本（backtest.py：回測報告）
.github/workflows/  daily-etl.yml、backtest.yml、keep-warm.yml、streamlit-wake.yml
render.yaml      Render Blueprint（一次建立 API 與 PWA 兩個服務）
```

## 多種 Python UI 並存

| UI | 內容 | 放在哪裡 | 怎麼接進來 |
|---|---|---|---|
| Gradio | 個股 K 線、指標、AI 報告 | Render，同一個 FastAPI 服務 | `gr.mount_gradio_app` 掛在 `/ui/gradio` |
| Dash | 月營收與年增率 | Render，同一個 FastAPI 服務 | `a2wsgi.WSGIMiddleware` 掛在 `/ui/dash` |
| Streamlit | 自選股總覽與篩選 | Streamlit Community Cloud | 獨立部署，直接連 Neon |

PWA 外殼把它們組合在一起，切換畫面只是換 iframe 的網址。要新增一種 UI：

1. **ASGI 框架**（例如 NiceGUI、另一個 FastAPI／Starlette 應用）：在 `app/main.py` 用 `app.mount("/ui/xxx", ...)` 掛上。
2. **WSGI 框架**（例如 Flask、Dash）：用 `a2wsgi.WSGIMiddleware` 包起來再掛載。
3. **自帶伺服器的框架**（例如 Streamlit）：另外部署，資料庫連同一個 Neon。
4. 在 `pwa/config.js` 的 `views` 加一筆，網址可用 `{api}` 與 `{stock}` 參數。

**PWA 自選股**：在「代號」輸入股票代號按「查看」，會呼叫 `POST /api/watchlist/{代號}` 加入自選股。資料庫還沒有這檔的股價時，後端會立刻向 FinMind 抓（約 10 秒），之後由每日排程更新。PWA 沒有登入，網頁上加的股票共用一份清單（存在 `watchlist` 資料表，`chat_id` 為 `pwa`，不推播），上限同 `MAX_WATCH_PER_CHAT`；報價牌右上角的 × 可移除。`WATCHLIST` 環境變數裡的股票固定顯示，不能從網頁移除。

**依類股選股**：不知道代號時按「類股」，參考 [Yahoo 股市類股](https://tw.stock.yahoo.com/class/) 的做法，上方切換上市／上櫃（FinMind 有興櫃資料時也會出現「興櫃」），左邊選類股（FinMind 的產業別，例如半導體業、航運業），右邊列出該類股的代號與名稱，可再用名稱或代號篩選；點一檔就跟按「查看」一樣加入自選股。資料來自 `stocks` 資料表（`market` 欄位：`twse` 上市、`tpex` 上櫃、`emerging` 興櫃），API 為 `GET /api/industries` 與 `GET /api/industries/stocks?industry=類股名稱&market=twse`。升級後第一次執行每日排程時，發現股票還沒有上市櫃別會自動重抓股票清單補上；在那之前選單只會顯示「未分類」。

Render 免費 Web Service 只有 512 MB 記憶體。Gradio＋Dash＋FastAPI 在本機實測約 240 MB。再加更多 UI 前，先觀察 Render 的記憶體圖表；不需要的 UI 可用 `ENABLE_GRADIO=false`、`ENABLE_DASH=false` 關閉。

> 為什麼不放 Hugging Face Spaces：目前新建 Gradio／Docker Space 需要付費方案，只有 Static Space 免費。

## LLM 免費額度

`core/llm.py` 以 OpenAI SDK 串接多家 OpenAI 相容端點，依 `LLM_PROVIDERS` 的順序嘗試。某家額度用完（HTTP 429）或出錯，就換下一家。全部失敗時改用技術指標規則產生報告，所以沒有任何金鑰也能跑。

| 名稱 | 取得金鑰 | 預設模型（`*_MODEL` 覆寫） | 呼叫間隔 | 輸出上限 | 免費額度（2026/10） |
|---|---|---|---|---|---|
| `gemini` | Google AI Studio | `gemini-3.8-flash` | 6 秒 | 4000 | 只有 Flash 系列；額度依專案而定，以 AI Studio 主控台為準 |
| `groq` | console.groq.com | `openai/gpt-oss-120b` | 20 秒 | 4000 | 30 RPM、1,000 RPD、8K TPM、200K TPD（整個組織共用） |
| `openrouter` | openrouter.ai（模型名稱帶 `:free`） | `openrouter/free` | 6 秒 | 4000 | 20 RPM；未儲值每天 50 次，累計儲值 ≥ $10 後每天 1,000 次 |
| `cerebras` | cloud.cerebras.ai | `llama-3.3-70b` | 13 秒 | 1500 | 5 RPM、30K TPM、每日 1M tokens；context 8K |

注意事項：

- 各家免費額度與可用模型經常調整。部署前到各家主控台確認目前額度與模型名稱，名稱不對時改 `*_MODEL` 環境變數即可。
- 免費層的每分鐘請求數與 tokens 很低，同一家的呼叫之間會等待上表的間隔（依各家 RPM／TPM 換算）。`LLM_MIN_INTERVAL`（預設 6 秒）是所有供應商的下限；個別調整用 `GROQ_MIN_INTERVAL`、`CEREBRAS_MAX_TOKENS` 這類 `<名稱>_MIN_INTERVAL`、`<名稱>_MAX_TOKENS` 環境變數。輸出上限包含推理模型的思考 tokens，太低會截斷 JSON。
- Gemini 放第一順位：Groq 的每日 200K tokens 大約只夠 50 份報告，OpenRouter 未儲值每天只有 50 次，適合當備援。
- 部分免費方案可能把輸入資料用於改善模型。這裡送出的是公開市場資料，但不要放入個人或公司機密。
- 報告只在盤後排程時批次產生並存進資料庫。網頁與 Telegram 只讀取已存的報告，不會每次都呼叫 LLM。

### 給 LLM 的資料與報告依據

報告的輸入包含：近 20 日收盤與均量、技術指標與訊號、近 10 日三大法人買賣超（FinMind `TaiwanStockInstitutionalInvestorsBuySell`，依外資／投信／自營商彙總，單位張）、本益比／股價淨值比／殖利率與近一年本益比區間（`TaiwanStockPER`）、近 6 個月營收年增率、近期新聞標題。法人與本益比抓不到時只記錄警告，報告照常產生。

每份報告會在 `reports` 存下：

- `context`：當時送給 LLM 的完整資料。Gradio「AI 報告」分頁底下可展開「產生報告時提供的資料」，對照 `reasons` 檢查理由是不是有根據。
- `rule_action`、`rule_confidence`：同一天的規則式判斷。LLM 成功時也會算，回測才能在相同日期比較兩者。

## 回測：LLM 有沒有比規則好

```bash
python -m jobs.backtest                          # 全部股票，持有 5 與 20 個交易日
python -m jobs.backtest --stocks 2330 --horizons 1,5,10 --csv backtest.csv
```

把歷史 `reports` 和報告日之後的收盤價比對：買進在 N 個交易日後上漲算贏、賣出在 N 日後下跌算贏，觀望不計入勝率。表格列出 LLM、規則式，以及對照組「每天都買」（同一批日子 N 日後上漲的比例）。預設只比較有 LLM 報告的日子，兩者樣本相同；`--all-days` 讓規則式改用全部報告日。新增 `rule_action` 欄位前的舊報告，會用報告日當天以前的指標重算規則式判斷。

`backtest.yml` 每週六自動執行一次，也可以手動執行；結果在該次執行的 Summary 頁，逐筆明細在 Artifacts。

解讀時注意：

- 勝率要和「每天都買」比。多頭時期每天都買的勝率本來就高，只有贏過它才代表判斷有用。
- 每天都產生報告，持有 20 日的報酬彼此重疊，樣本不是獨立的；累積幾個月、樣本數上百後再下結論。
- 沒有計入手續費、證交稅與滑價，也不是完整的交易策略模擬。

## 本機開發

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r streamlit_app/requirements.txt pytest ruff

python -m jobs.seed_demo                  # 產生模擬資料（不需網路與金鑰）
uvicorn app.main:app --reload             # http://127.0.0.1:8000/ui/gradio/
streamlit run streamlit_app/streamlit_app.py
cd pwa && python -m http.server 5500      # 先把 config.js 的 apiBase 改成 http://127.0.0.1:8000
python -m pytest -q tests
```

`DATABASE_URL` 留空時使用 `./local.db`（SQLite）。要抓真實資料，執行 `python -m jobs.daily_etl --no-push`。

### 套件版本

`requirements*.in` 只列直接依賴與最低版本；`requirements*.txt` 是由它們產生、鎖定所有版本的檔案，Render、Streamlit Cloud 與 GitHub Actions 都安裝鎖定版。新增或升級套件：改 `.in` 後執行

```bash
for f in requirements requirements-jobs streamlit_app/requirements; do
  uv pip compile --universal --python-version 3.12 $f.in -o $f.txt            # 加 --upgrade 可升級到最新版
done
```

CI 會檢查 `.in` 與 `.txt` 是否一致。

### 資料表結構（Alembic）

API 啟動與每日排程會自動把資料庫升級到最新版本。改了 `core/models.py` 要新增 migration：

```bash
DATABASE_URL=sqlite:///./local.db alembic revision --autogenerate -m "說明"   # 產生在 migrations/versions/
```

檢查產生的檔案後一起提交。忘了產生 migration 時，`tests/test_migrations.py` 會失敗。導入 Alembic 前就建立的資料庫，第一次執行時會自動標記為基準版 `0001`，不會重建資料表。

### CI 與排程

- `ci.yml`：PR 與推送到 main 時執行 ruff、鎖定檔檢查、匯入完整 Web 服務與 pytest。
- `daily-etl.yml`：每個交易日盤後排程；每週一順便更新上市櫃股票名稱。
- `backtest.yml`：每週六回測一次，比較 LLM 與規則式的勝率（只需要 `DATABASE_URL`）。
- `streamlit-wake.yml`：Streamlit Community Cloud 約 12 小時沒人瀏覽就休眠，PWA 的「總覽」會變成休眠頁。這個 workflow 每 6 小時用無頭瀏覽器開啟 app，休眠中就按喚醒按鈕（`jobs/wake_streamlit.py`）。換了網址就設定 Variables 的 `STREAMLIT_URL`；失敗時到該次執行的 Artifacts 下載截圖。PWA 另外在「總覽」下方提示「開新分頁喚醒」，作為排程失效時的備援。
- `keepalive.yml`：公開 repo 連續 60 天沒有活動時，GitHub 會停用排程 workflow；這個 workflow 每月重新啟用它們（daily-etl、backtest、keep-warm、streamlit-wake 與自己）。新增排程 workflow 時記得加進清單。若仍收到 GitHub 的停用通知信，到 Actions 頁面手動 Enable 即可。

## 部署步驟

1. **Neon**：建立專案，複製 **pooled** 連線字串（主機名含 `-pooler`）。
2. **GitHub**：推上 repo，到 Settings → Secrets and variables → Actions 設定：
   - Secrets：`DATABASE_URL`、`FINMIND_TOKEN`、各家 LLM 金鑰、`TELEGRAM_BOT_TOKEN`
   - Variables：`WATCHLIST`、`LLM_PROVIDERS`、`TELEGRAM_DEFAULT_CHAT_IDS`、`RENDER_URL`
   - 選用：`MAX_STOCKS`（排程股票總數上限，預設 50）、`LLM_PROVIDER_COOLDOWN`（某家 LLM 失敗後暫停使用的秒數，預設 1800）
   - Render 上可選設：`MAX_WATCH_PER_CHAT`（每個 Telegram 對話的自選股上限，預設 10）、`REPORT_COOLDOWN_MINUTES`（同一檔報告重新產生的冷卻分鐘數，預設 30）、`REPORT_REGEN_PER_HOUR`（每小時重新產生報告的總次數上限，預設 20）
3. **初始化資料**：Actions → daily-etl → Run workflow。第一次會抓約 400 天股價與建立資料表。排程只在當天有新股價時才產生報告與推播（休市日自動略過、同一天重跑不重複推播）；假日想先產生報告，勾選 `force`。
4. **Render**：New → Blueprint，選這個 repo，會建立 `stock-ai-api` 與 `stock-ai-pwa` 兩個服務。在 `stock-ai-api` 填入 `DATABASE_URL`、LLM 金鑰與 `TELEGRAM_BOT_TOKEN`。`PUBLIC_BASE_URL` 可留空，會自動使用 Render 提供的服務網址（用自訂網域時才需要填）。
5. **Streamlit Community Cloud**：New app，Main file 選 `streamlit_app/streamlit_app.py`，在 Secrets 填入：
   ```toml
   DATABASE_URL = "postgresql://..."
   WATCHLIST = "2330,2317,2454"
   ```
6. **PWA**：修改 `pwa/config.js` 的 `apiBase` 與 Streamlit 網址後推送，Render 會自動重新部署。`render.yaml` 已把 API 的 `CORS_ORIGINS` 設成 `https://stock-ai-pwa.onrender.com`；PWA 網址不同時記得一起改。
7. **Telegram**：跟 @BotFather 建立機器人，把 token 填到 Render 的 `TELEGRAM_BOT_TOKEN` 與 GitHub 的 Secrets。Render 服務每次啟動都會自動設定 webhook（含 Render 自動產生的 `TELEGRAM_WEBHOOK_SECRET`）與指令選單，不必在本機執行任何指令。對機器人傳 `/start` 確認有回應，回覆裡會附上你的 chat id；要收盤後推播，把這個 chat id 加到 GitHub Variables 的 `TELEGRAM_DEFAULT_CHAT_IDS`，或直接 `/watch` 股票。
   - 手動設定（例如沒有部署在 Render）：`python -m jobs.set_webhook`，本機的 `TELEGRAM_WEBHOOK_SECRET` 必須和服務上的值相同，否則 webhook 會拒絕 Telegram 的請求。

## 免費方案的限制與對策

| 平台 | 限制 | 本專案的對策 |
|---|---|---|
| Render Web Service | 閒置 15 分鐘休眠，喚醒約 1 分鐘；每月 750 小時；檔案系統不保留；不支援 cron | PWA 外殼先開，並顯示「後端喚醒中」；資料全存 Neon；排程交給 GitHub Actions；只開一個 Web Service；`keep-warm.yml` 只在盤中喚醒 |
| Render 流量 | 2026/8/1 起 Hobby 方案每月含 5 GB 對外流量（Web Service 與 Static Site 共用），超出每 GB $0.15 | Gradio、Dash 的前端檔案每次冷開約數 MB：定期看 Render 的 Bandwidth 圖表，不用的 UI 以 `ENABLE_GRADIO`／`ENABLE_DASH` 關閉 |
| Render Postgres | 免費版 30 天後到期 | 改用 Neon |
| Neon | 每專案 0.5 GB、每月 100 CU-hours，閒置 5 分鐘暫停 | 只存結構化資料；新聞只存標題與連結；指標每次只回寫最近 20 日；`/health`（Render 健康檢查與 keep-warm 會打）不查資料庫，Neon 才能閒置暫停，要連資料庫一起檢查用 `/health/db` |
| Streamlit Community Cloud | 記憶體上限約 2.7 GB；12 小時沒人瀏覽就休眠 | 直接讀 Neon，不依賴 Render；`streamlit-wake.yml` 每 6 小時喚醒 |
| GitHub Actions | 公開 repo 標準 runner 免費不限分鐘（私有 repo 每月 2,000 分鐘）；排程可能延遲；公開 repo 60 天沒有活動會停用排程 | 保留 `workflow_dispatch` 手動執行；`keepalive.yml` 每月重新啟用排程 |
| FinMind | 未帶 token 每小時 300 次，註冊後帶 token 600 次；超過回 HTTP 402，IP 封鎖約一小時 | 一定要設 `FINMIND_TOKEN`；每檔每天 4–5 次（月營收只在每月 1–15 日抓），50 檔約 250 次；遇到 402 不重試、立刻停止整批，已完成的報告照常推播 |
| LLM | 見「LLM 免費額度」 | 多家依序備援，每家各自的呼叫間隔與輸出上限；全部失敗改用規則式報告 |
| Telegram Bot API | 免費；每個對話約每秒 1 則 | 每個對話盤後只發一則彙整 |

各平台額度會調整，以官網公告為準。

## 常見問題

- **FinMind 額度用完**：排程記錄出現「使用量已達上限（HTTP 402）」，或 PWA 按「查看」出現「FinMind 使用量已達上限」：等一小時後再執行；經常發生就降低 `MAX_STOCKS` 或確認 `FINMIND_TOKEN` 有設定。
- **iframe 空白**：先直接開啟 `https://<api>/ui/gradio/` 確認服務正常，再檢查 `config.js` 的網址。
- **PWA 離線**：service worker 只快取外殼。iframe 裡的 Python UI 在其他網域，一定要連線。
- **Telegram 沒回應**：Render 休眠時第一則訊息會等喚醒，Telegram 會自動重送。一直沒回應就看 Render 的 Logs 有沒有「Telegram webhook 設定失敗」，確認 `TELEGRAM_BOT_TOKEN` 正確後重新部署。

---

所有報告與訊號僅供學習研究，不構成投資建議。
