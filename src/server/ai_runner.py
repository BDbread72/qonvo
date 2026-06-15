"""헤드리스 AI 실행기.

기존 ProviderRouter(GeminiProvider + 플러그인)를 그대로 재사용한다.
CLI(cli_runner.BatchRunner)와 동일한 방식으로 router.chat() 을 소비하되,
스트리밍 청크를 on_chunk 콜백으로 흘리고 이미지를 base64 문자열로 변환한다.

블로킹 호출이므로 app.py 에서 executor 스레드로 실행한다.
"""
from __future__ import annotations

import base64
from typing import Any, Callable, Dict, List, Optional


def build_router(config: dict):
    """config(또는 데스크톱 settings)의 키로 ProviderRouter 를 구성한다."""
    from v.settings import get_api_keys
    from v.provider import GeminiProvider
    from v.model_plugin import PluginRegistry, ProviderRouter

    keys = list(config.get("ai", {}).get("gemini_keys") or [])
    if not keys:
        # 데스크톱 앱에 저장된 키로 폴백
        try:
            keys = get_api_keys()
        except Exception:
            keys = []

    gemini = GeminiProvider(api_keys=keys) if keys else None
    registry = PluginRegistry.instance()
    try:
        registry.load_all()
    except Exception:
        pass
    return ProviderRouter(gemini_provider=gemini)


def _b64(img: Any) -> Optional[str]:
    """이미지 데이터(bytes/str)를 base64 문자열로 변환한다."""
    if isinstance(img, bytes):
        return base64.b64encode(img).decode("ascii")
    if isinstance(img, str):
        return img  # 이미 base64/경로 문자열로 가정
    return None


def run_ai(
    router,
    model: str,
    message: str,
    files: Optional[List[str]] = None,
    system_prompt: str = "",
    options: Optional[Dict[str, Any]] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """단일 AI 요청을 실행하고 결과 dict 를 반환한다.

    반환: {text, images:[b64...], tokens_in, tokens_out, error}
    on_chunk: 텍스트 청크가 생성될 때마다 호출(스트리밍 진행률).
    """
    from v.provider import ChatMessage

    result: Dict[str, Any] = {
        "text": "", "images": [], "tokens_in": 0, "tokens_out": 0, "error": None,
    }

    opts = dict(options or {})
    if system_prompt:
        opts["system_prompt"] = system_prompt

    attachments = [f for f in (files or []) if isinstance(f, str)] or None
    messages = [ChatMessage(role="user", content=message or "", attachments=attachments)]

    chunks: List[str] = []
    try:
        out = router.chat(model, messages, stream=True, **opts)

        if isinstance(out, str):
            chunks.append(out)
            if on_chunk and out:
                on_chunk(out)
        elif isinstance(out, dict):
            # 이미지 생성 모델 등 — 단건 dict 반환
            text = out.get("text", "")
            if text:
                chunks.append(text)
                if on_chunk:
                    on_chunk(text)
            for img in out.get("images", []) or []:
                b = _b64(img)
                if b:
                    result["images"].append(b)
            result["tokens_in"] = out.get("prompt_tokens", 0)
            result["tokens_out"] = out.get("candidates_tokens", 0)
        else:
            # 제너레이터 — 텍스트 청크 + 마커 dict
            for item in out:
                if isinstance(item, dict):
                    if "__usage__" in item:
                        result["tokens_in"] = item.get("prompt_tokens", 0)
                        result["tokens_out"] = item.get("candidates_tokens", 0)
                    elif "__error__" in item:
                        result["error"] = item["__error__"]
                    elif "images" in item:
                        for img in item.get("images", []) or []:
                            b = _b64(img)
                            if b:
                                result["images"].append(b)
                else:
                    chunks.append(item)
                    if on_chunk and item:
                        on_chunk(item)
    except Exception as e:
        result["error"] = str(e)

    result["text"] = "".join(chunks)
    return result
