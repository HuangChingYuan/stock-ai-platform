"""LLM 免費額度串接：多家 OpenAI 相容端點，依序備援（fallback）。

- 全部走 openai SDK，只換 base_url 與 api_key。
- 某家回 429（額度用完）或出錯，就換下一家；全部失敗時回傳 None，由呼叫端改用規則式報告。
- 各家免費額度與可用模型常調整，模型名稱請用環境變數覆寫，並以官方主控台為準。
- 每家的免費 RPM／TPM 不同：呼叫間隔與輸出上限各自設定，可用 <名稱>_MIN_INTERVAL、<名稱>_MAX_TOKENS 覆寫。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass

from core.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str
    model_env: str
    default_model: str
    min_interval: float = 0.0  # 同一家兩次呼叫的最短秒數（依免費 RPM／TPM 換算）；實際取與 LLM_MIN_INTERVAL 較大者
    max_tokens: int = 4000     # 推理模型的思考 tokens 也算在內，要留餘裕

    def interval(self) -> float:
        return max(get_settings().llm_min_interval,
                   float(os.getenv(f"{self.name.upper()}_MIN_INTERVAL") or self.min_interval))

    def output_limit(self) -> int:
        return int(os.getenv(f"{self.name.upper()}_MAX_TOKENS") or self.max_tokens)


PROVIDERS: dict[str, Provider] = {
    # Google AI Studio：免費層目前只含 Flash / Flash-Lite 系列
    "gemini": Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/",
                       "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-3.8-flash"),
    # Groq：速度快；gpt-oss-120b 免費層每分鐘只有 8K tokens，一份報告（含思考）約 3K，間隔 20 秒
    "groq": Provider("groq", "https://api.groq.com/openai/v1",
                     "GROQ_API_KEY", "GROQ_MODEL", "openai/gpt-oss-120b", min_interval=20),
    # OpenRouter：模型名稱帶 :free 後綴者為免費；20 RPM，未儲值每天只有 50 次
    "openrouter": Provider("openrouter", "https://openrouter.ai/api/v1",
                           "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "openrouter/free"),
    # Cerebras：每日 token 額度較大，適合批次；免費層 5 RPM、context 8K，輸出上限要壓低才放得下輸入
    "cerebras": Provider("cerebras", "https://api.cerebras.ai/v1",
                         "CEREBRAS_API_KEY", "CEREBRAS_MODEL", "llama-3.3-70b", min_interval=13, max_tokens=1500),
}

_last_call: dict[str, float] = {}
_down_until: dict[str, float] = {}  # 失敗過的 provider 暫停到這個時間，不再每次都先試它


def available() -> list[Provider]:
    names = get_settings().llm_providers
    now = time.time()
    return [PROVIDERS[n] for n in names
            if n in PROVIDERS and os.getenv(PROVIDERS[n].key_env) and _down_until.get(n, 0) <= now]


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("回應中找不到 JSON")
    return json.loads(text[start : end + 1])


def chat_json(system: str, user: str, max_tokens: int | None = None) -> tuple[dict, str, str] | None:
    """回傳 (解析後的 JSON, provider 名稱, 模型名稱)；全部失敗回傳 None。max_tokens 留空用各家預設。"""
    from openai import OpenAI  # 延遲匯入：Streamlit 等不需要 LLM 的地方不必安裝

    for p in available():
        model = os.getenv(p.model_env, p.default_model)
        wait = p.interval() - (time.time() - _last_call.get(p.name, 0))
        if wait > 0:
            time.sleep(wait)  # 免費層 RPM 很低，同一家呼叫之間保留間隔
        try:
            client = OpenAI(base_url=p.base_url, api_key=os.environ[p.key_env], timeout=60, max_retries=0)
            _last_call[p.name] = time.time()
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2,
                max_tokens=max_tokens or p.output_limit(),
            )
            return _extract_json(resp.choices[0].message.content or ""), p.name, model
        except Exception as exc:  # 429、模型下架、JSON 解析失敗…都換下一家
            if isinstance(exc, ValueError):  # JSON 解析失敗：單次輸出格式不對，不代表這家壞掉
                log.warning("LLM %s/%s 回傳格式錯誤，改用下一家：%s", p.name, model, exc)
            else:
                # 額度用完、逾時、金鑰或模型錯誤：之後一段時間直接跳過，省下批次中每檔的等待時間
                _down_until[p.name] = time.time() + get_settings().llm_provider_cooldown
                log.warning("LLM %s/%s 失敗，暫停使用 %.0f 秒：%s",
                            p.name, model, get_settings().llm_provider_cooldown, exc)
    return None
