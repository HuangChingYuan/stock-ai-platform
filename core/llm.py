"""LLM 免費額度串接：多家 OpenAI 相容端點，依序備援（fallback）。

- 全部走 openai SDK，只換 base_url 與 api_key。
- 某家回 429（額度用完）或出錯，就換下一家；全部失敗時回傳 None，由呼叫端改用規則式報告。
- 各家免費額度與可用模型常調整，模型名稱請用環境變數覆寫，並以官方主控台為準。
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


PROVIDERS: dict[str, Provider] = {
    # Google AI Studio：免費層目前只含 Flash / Flash-Lite 系列
    "gemini": Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/",
                       "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-3.8-flash"),
    # Groq：速度快，每分鐘與每日請求數有上限
    "groq": Provider("groq", "https://api.groq.com/openai/v1",
                     "GROQ_API_KEY", "GROQ_MODEL", "openai/gpt-oss-120b"),
    # OpenRouter：模型名稱帶 :free 後綴者為免費
    "openrouter": Provider("openrouter", "https://openrouter.ai/api/v1",
                           "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "openrouter/free"),
    # Cerebras：每日 token 額度較大，適合批次
    "cerebras": Provider("cerebras", "https://api.cerebras.ai/v1",
                         "CEREBRAS_API_KEY", "CEREBRAS_MODEL", "llama-3.3-70b"),
}

_last_call: dict[str, float] = {}


def available() -> list[Provider]:
    names = get_settings().llm_providers
    return [PROVIDERS[n] for n in names if n in PROVIDERS and os.getenv(PROVIDERS[n].key_env)]


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("回應中找不到 JSON")
    return json.loads(text[start : end + 1])


def chat_json(system: str, user: str, max_tokens: int = 4000) -> tuple[dict, str, str] | None:
    """回傳 (解析後的 JSON, provider 名稱, 模型名稱)；全部失敗回傳 None。"""
    from openai import OpenAI  # 延遲匯入：Streamlit 等不需要 LLM 的地方不必安裝

    interval = get_settings().llm_min_interval
    for p in available():
        model = os.getenv(p.model_env, p.default_model)
        wait = interval - (time.time() - _last_call.get(p.name, 0))
        if wait > 0:
            time.sleep(wait)  # 免費層 RPM 很低，同一家呼叫之間保留間隔
        try:
            client = OpenAI(base_url=p.base_url, api_key=os.environ[p.key_env], timeout=60, max_retries=0)
            _last_call[p.name] = time.time()
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2,
                max_tokens=max_tokens,
            )
            return _extract_json(resp.choices[0].message.content or ""), p.name, model
        except Exception as exc:  # 429、模型下架、JSON 解析失敗…都換下一家
            log.warning("LLM %s/%s 失敗，改用下一家：%s", p.name, model, exc)
    return None
