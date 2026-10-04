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
  notify.py        Telegram 發訊
app/             Render Web Service
  main.py          FastAPI，掛載 Gradio 與 Dash
  api.py           /api/*
  telegram_bot.py  Telegram 指令
  charts.py        Plotly 圖表（紅漲綠跌）
  ui/gradio_app.py ui/dash_app.py
streamlit_app/   Streamlit Community Cloud
pwa/             PWA 外殼（純靜態）
jobs/            排程與工具腳本
.github/workflows/  daily-etl.yml、keep-warm.yml、streamlit-wake.yml
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

Render 免費 Web Service 只有 512 MB 記憶體。Gradio＋Dash＋FastAPI 在本機實測約 240 MB。再加更多 UI 前，先觀察 Render 的記憶體圖表；不需要的 UI 可用 `ENABLE_GRADIO=false`、`ENABLE_DASH=false` 關閉。

> 為什麼不放 Hugging Face Spaces：目前新建 Gradio／Docker Space 需要付費方案，只有 Static Space 免費。

## LLM 免費額度

`core/llm.py` 以 OpenAI SDK 串接多家 OpenAI 相容端點，依 `LLM_PROVIDERS` 的順序嘗試。某家額度用完（HTTP 429）或出錯，就換下一家。全部失敗時改用技術指標規則產生報告，所以沒有任何金鑰也能跑。

| 名稱 | 取得金鑰 | 預設模型（可用環境變數覆寫） |
|---|---|---|
| `gemini` | Google AI Studio | `gemini-2.5-flash` |
| `groq` | console.groq.com | `llama-3.3-70b-versatile` |
| `openrouter` | openrouter.ai（模型名稱帶 `:free`） | `meta-llama/llama-3.3-70b-instruct:free` |
| `cerebras` | cloud.cerebras.ai | `llama-3.3-70b` |

注意事項：

- 各家免費額度與可用模型經常調整。部署前到各家主控台確認目前額度與模型名稱，名稱不對時改 `*_MODEL` 環境變數即可。
- 免費層的每分鐘請求數很低，`LLM_MIN_INTERVAL`（預設 6 秒）會在同一家的呼叫之間等待。
- 部分免費方案可能把輸入資料用於改善模型。這裡送出的是公開市場資料，但不要放入個人或公司機密。
- 報告只在盤後排程時批次產生並存進資料庫。網頁與 Telegram 只讀取已存的報告，不會每次都呼叫 LLM。

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
- `streamlit-wake.yml`：Streamlit Community Cloud 約 12 小時沒人瀏覽就休眠，PWA 的「總覽」會變成休眠頁。這個 workflow 每 6 小時用無頭瀏覽器開啟 app，休眠中就按喚醒按鈕（`jobs/wake_streamlit.py`）。換了網址就設定 Variables 的 `STREAMLIT_URL`；失敗時到該次執行的 Artifacts 下載截圖。PWA 另外在「總覽」下方提示「開新分頁喚醒」，作為排程失效時的備援。
- `keepalive.yml`：公開 repo 連續 60 天沒有活動時，GitHub 會停用排程 workflow；這個 workflow 每月重新啟用它們。若仍收到 GitHub 的停用通知信，到 Actions 頁面手動 Enable 即可。

## 部署步驟

1. **Neon**：建立專案，複製 **pooled** 連線字串（主機名含 `-pooler`）。
2. **GitHub**：推上 repo，到 Settings → Secrets and variables → Actions 設定：
   - Secrets：`DATABASE_URL`、`FINMIND_TOKEN`、各家 LLM 金鑰、`TELEGRAM_BOT_TOKEN`
   - Variables：`WATCHLIST`、`LLM_PROVIDERS`、`TELEGRAM_DEFAULT_CHAT_IDS`、`RENDER_URL`
   - 選用：`MAX_STOCKS`（排程股票總數上限，預設 50）、`LLM_PROVIDER_COOLDOWN`（某家 LLM 失敗後暫停使用的秒數，預設 1800）
   - Render 上可選設：`MAX_WATCH_PER_CHAT`（每個 Telegram 對話的自選股上限，預設 10）、`REPORT_COOLDOWN_MINUTES`（同一檔報告重新產生的冷卻分鐘數，預設 30）、`REPORT_REGEN_PER_HOUR`（每小時重新產生報告的總次數上限，預設 20）
3. **初始化資料**：Actions → daily-etl → Run workflow。第一次會抓約 400 天股價與建立資料表。排程只在當天有新股價時才產生報告與推播（休市日自動略過、同一天重跑不重複推播）；假日想先產生報告，勾選 `force`。
4. **Render**：New → Blueprint，選這個 repo，會建立 `stock-ai-api` 與 `stock-ai-pwa` 兩個服務。在 `stock-ai-api` 填入 `DATABASE_URL`、LLM 金鑰、`PUBLIC_BASE_URL`（服務網址）。
5. **Streamlit Community Cloud**：New app，Main file 選 `streamlit_app/streamlit_app.py`，在 Secrets 填入：
   ```toml
   DATABASE_URL = "postgresql://..."
   WATCHLIST = "2330,2317,2454"
   ```
6. **PWA**：修改 `pwa/config.js` 的 `apiBase` 與 Streamlit 網址後推送，Render 會自動重新部署。`render.yaml` 已把 API 的 `CORS_ORIGINS` 設成 `https://stock-ai-pwa.onrender.com`；PWA 網址不同時記得一起改。
7. **Telegram**：跟 @BotFather 建立機器人，設定好環境變數後在本機執行 `python -m jobs.set_webhook`。本機的 `TELEGRAM_WEBHOOK_SECRET` 必須和 Render 上的值相同（Render 會自動產生，到服務的 Environment 頁面複製）；webhook 會拒絕沒有正確 secret 的請求。

## 免費方案的限制與對策

| 平台 | 限制 | 本專案的對策 |
|---|---|---|
| Render Web Service | 閒置 15 分鐘休眠，喚醒約 1 分鐘；每月 750 小時；檔案系統不保留；不支援 cron | PWA 外殼先開，並顯示「後端喚醒中」；資料全存 Neon；排程交給 GitHub Actions；只開一個 Web Service；`keep-warm.yml` 只在盤中喚醒 |
| Render Postgres | 免費版 30 天後到期 | 改用 Neon |
| Neon | 每專案 0.5 GB、每月 100 CU-hours，閒置 5 分鐘暫停 | 只存結構化資料；新聞只存標題與連結；指標每次只回寫最近 20 日 |
| Streamlit Community Cloud | 一段時間沒人使用會休眠 | 直接讀 Neon，不依賴 Render |
| GitHub Actions | 排程可能延遲；公開 repo 長期沒有活動時，排程可能被自動停用 | 保留 `workflow_dispatch` 手動執行；定期推送 commit |

各平台額度會調整，以官網公告為準。

## 常見問題

- **iframe 空白**：先直接開啟 `https://<api>/ui/gradio/` 確認服務正常，再檢查 `config.js` 的網址。
- **PWA 離線**：service worker 只快取外殼。iframe 裡的 Python UI 在其他網域，一定要連線。
- **Telegram 沒回應**：Render 休眠時第一則訊息會等喚醒，Telegram 會自動重送。

---

所有報告與訊號僅供學習研究，不構成投資建議。
