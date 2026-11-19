"""LLM 网关：默认离线桩（真实结构化输出、不假装通过）；可接 OpenAI 兼容端点。

接入真实模型：设置环境变量 NOVEL_REVIEW_LLM_URL / NOVEL_REVIEW_LLM_KEY / NOVEL_REVIEW_LLM_MODEL，
或调用 set_gateway(CustomGateway())。CustomGateway 需实现 async chat(prompt: str) -> str。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Protocol

import asyncio

# 用户偏好：Promise 一律用 async/await，不用 .then() 链式调用。
# 本模块所有异步路径均用 async/await。


class Gateway(Protocol):
    backend: str
    model: str

    async def chat(self, prompt: str) -> str: ...


class StubGateway:
    """离线桩：返回严格 JSON，明确标注 stub=true，绝不伪造高分或放行。"""

    backend = "stub"
    model = "offline-stub"

    async def chat(self, prompt: str) -> str:
        await asyncio.sleep(0)  # 让出事件循环，保持并发语义
        # 从模板 JSON 示例里抽取 stage 值，例如 {"stage":"first_review",...}
        stage = "unknown"
        m = re.search(r'["\']stage["\']\s*[:：]\s*["\']([a-z_]+)["\']', prompt)
        if m:
            stage = m.group(1)
        payload = {
            "_stub": True,
            "stage": stage,
            "score": 0,
            "pass": False,
            "verdict": "stub_no_judgement",
            "issues": [{
                "level": "mid",
                "location": "全文",
                "evidence": "离线桩模式未调用真实模型",
                "suggestion": "配置 NOVEL_REVIEW_LLM_URL/KEY/MODEL 或注入 CustomGateway 以启用 L3 语义评审",
            }],
        }
        return json.dumps(payload, ensure_ascii=False)

    async def chat_detailed(self, prompt: str) -> tuple[str, dict]:
        raw = await self.chat(prompt)
        return raw, {}


class OpenAICompatGateway:
    """OpenAI 兼容 /chat/completions，用标准库 urllib，无额外依赖。"""

    backend = "openai_compat"

    def __init__(self, url: str, key: str, model: str, timeout: int = 90):
        self.url = url
        self.key = key
        self.model = model
        self.timeout = timeout

    async def chat(self, prompt: str) -> str:
        raw, _ = await self.chat_detailed(prompt)
        return raw

    async def chat_detailed(self, prompt: str) -> tuple[str, dict]:
        import urllib.request

        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是小说评审器。只输出严格 JSON，不要 markdown 代码块，不要解释。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
        )
        # urllib 是阻塞 IO，放到线程里跑，保持 async 语义
        def _do():
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        data = await asyncio.to_thread(_do)
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return content, usage


_gateway: Gateway | None = None
_secondary_gateway: Gateway | None = None


def set_gateway(gw: Gateway) -> None:
    global _gateway
    _gateway = gw


def set_secondary_gateway(gw: Gateway | None) -> None:
    """注入第二评审员网关；传 None 表示不独立，复用主网关（多视角而非独立投票）。"""
    global _secondary_gateway
    _secondary_gateway = gw


def get_gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        url = os.getenv("NOVEL_REVIEW_LLM_URL", "").strip()
        key = os.getenv("NOVEL_REVIEW_LLM_KEY", "").strip()
        model = os.getenv("NOVEL_REVIEW_LLM_MODEL", "gpt-4o-mini").strip()
        if url and key:
            _gateway = OpenAICompatGateway(url, key, model)
        else:
            _gateway = StubGateway()
    return _gateway


def get_secondary_gateway() -> tuple[Gateway, bool]:
    """返回 (第二评审员网关, 是否独立后端)。

    独立后端优先用显式注入，其次用 NOVEL_REVIEW_LLM2_* 环境变量；
    都没有则复用主网关——此时 independent=False，只算多视角，不冒充独立投票。
    """
    global _secondary_gateway
    if _secondary_gateway is not None:
        return _secondary_gateway, True
    url = os.getenv("NOVEL_REVIEW_LLM2_URL", "").strip()
    key = os.getenv("NOVEL_REVIEW_LLM2_KEY", "").strip()
    model = os.getenv("NOVEL_REVIEW_LLM2_MODEL", "").strip()
    if url and key:
        if _secondary_gateway is None:
            _secondary_gateway = OpenAICompatGateway(url, key, model or "gpt-4o-mini")
        return _secondary_gateway, True
    return get_gateway(), False


def parse_json(raw: str) -> dict:
    """容忍 ```json 包裹与前后解释，失败返回结构化错误而非抛异常。"""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I | re.M).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {"_parse_error": "非对象 JSON", "raw": s[:200]}
    except Exception as exc:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
        return {"_parse_error": str(exc), "raw": s[:200]}


async def _chat_with_usage(gw: Gateway, prompt: str) -> tuple[str, dict, bool]:
    """优先走 chat_detailed 拿 usage；自定义网关只实现 chat 时也能用。"""
    detailed = getattr(gw, "chat_detailed", None)
    if callable(detailed):
        raw, usage = await detailed(prompt)
        return raw, (usage if isinstance(usage, dict) else {}), True
    return await gw.chat(prompt), {}, False


async def judge(prompt: str, gateway: Gateway | None = None) -> dict:
    """单次评审调用；gateway 显式指定第二评审员，未指定走主网关。"""
    gw = gateway or get_gateway()
    t0 = time.perf_counter()
    raw, usage, usage_available = await _chat_with_usage(gw, prompt)
    latency = round(time.perf_counter() - t0, 4)
    obj = parse_json(raw)
    obj["_backend"] = gw.backend
    obj["_model"] = gw.model
    obj["_telemetry"] = {
        "latency_sec": latency,
        "usage": usage,
        "usage_available": usage_available,
    }
    return obj
