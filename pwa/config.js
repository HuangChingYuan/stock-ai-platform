/*
 * PWA 外殼設定：部署後只需修改這個檔案。
 * 新增一種 Python UI ＝ 在 views 加一筆；網址中的 {api} 與 {stock} 會自動替換。
 */
window.APP_CONFIG = {
  // Render 上 FastAPI 服務的網址（結尾不要斜線）
  apiBase: "https://stock-ai-api-bebh.onrender.com",
  defaultStock: "2330",
  views: [
    { id: "chart",    label: "K 線",   engine: "Gradio",    url: "{api}/ui/gradio/?stock={stock}&view=chart" },
    { id: "report",   label: "AI 報告", engine: "Gradio",    url: "{api}/ui/gradio/?stock={stock}&view=report" },
    { id: "revenue",  label: "月營收",  engine: "Dash",      url: "{api}/ui/dash/?stock={stock}" },
    // Streamlit Community Cloud 的網址；embed=true 會隱藏 Streamlit 自己的工具列
    { id: "overview", label: "總覽",   engine: "Streamlit", url: "https://stock-ai-overview.streamlit.app/?embed=true&stock={stock}", needsApi: false },
  ],
};
