"""
Gemini 채팅 프로바이더
"""
import os
import base64
import time
import threading
from typing import List, Generator
from dataclasses import dataclass

from google import genai
from google.genai import types


# 모델 정의 (ID → 표시 이름)
MODELS = {
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
    "gemini-3-flash-preview": "Gemini 3.0 Flash",
    "gemini-2.5-pro": "Gemini 2.5 Pro",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-3.1-flash-image-preview": "🍌Nanobanana 2",
    "gemini-3-pro-image-preview": "🍌Nanobanana Pro",
    "gemini-2.5-flash-image": "🍌Nanobanana",
    "imagen-4.0-generate-001": "🎨 Imagen 4",
    "imagen-4.0-ultra-generate-001": "🎨 Imagen 4 Ultra",
    "imagen-4.0-fast-generate-001": "🎨 Imagen 4 Fast",
}

# UI용 모델 ID 목록
MODEL_IDS = list(MODELS.keys())

# 공통 생성 옵션 (temperature, top_p, max_output_tokens)
_COMMON_GEN_OPTIONS = {
    "temperature": {
        "type": "float",
        "label": "Temperature",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "default": 1.0,
    },
    "top_p": {
        "type": "float",
        "label": "Top P",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "default": 0.95,
    },
    "max_output_tokens": {
        "type": "int",
        "label": "Max Tokens",
        "min": 1,
        "max": 65536,
        "default": 8192,
    },
}

# 이미지 모델용 (max_output_tokens 제외)
_IMAGE_GEN_OPTIONS = {
    "temperature": _COMMON_GEN_OPTIONS["temperature"],
    "top_p": _COMMON_GEN_OPTIONS["top_p"],
}

# 고급 텍스트 생성 옵션 (stop / seed / JSON 모드) — 텍스트 모델 공통.
# provider 의 _advanced_config_kwargs() 가 GenerateContentConfig 로 변환한다.
_ADVANCED_TEXT_OPTIONS = {
    "stop_sequences": {
        "type": "string_list",
        "label": "Stop",
        "default": [],
        "placeholder": "쉼표로 구분",
    },
    "seed": {
        "type": "int",
        "label": "Seed",
        "min": -1,
        "max": 2147483647,
        "default": -1,  # -1 = 미지정(랜덤)
    },
    "json_mode": {
        "type": "bool",
        "label": "JSON",
        "default": False,
    },
}

_IMAGEN_ASPECT_RATIO_OPTION = {
    "aspect_ratio": {
        "type": "choice",
        "label": "비율",
        "values": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "default": "1:1",
    },
}

_NANOBANANA_ASPECT_RATIO_OPTION = {
    "aspect_ratio": {
        "type": "choice",
        "label": "비율",
        "values": ["1:1", "1:4", "1:8", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5", "5:4", "8:1", "9:16", "16:9", "21:9"],
        "default": "1:1",
    },
}

_NANOBANANA_31_SIZE_OPTION = {
    "image_size": {
        "type": "choice",
        "label": "해상도",
        "values": ["512px", "1K", "2K", "4K"],
        "default": "1K",
    },
}

_NANOBANANA_SIZE_OPTION = {
    "image_size": {
        "type": "choice",
        "label": "해상도",
        "values": ["1K", "2K", "4K"],
        "default": "1K",
    },
}

_IMAGEN_SIZE_OPTION = {
    "image_size": {
        "type": "choice",
        "label": "해상도",
        "values": ["1K", "2K"],
        "default": "1K",
    },
}

# 모델별 옵션 스키마 (UI 동적 생성용)
MODEL_OPTIONS = {
    "gemini-3.1-pro-preview": {
        "thinking_level": {
            "type": "choice",
            "label": "Thinking",
            "values": ["HIGH", "MEDIUM", "LOW"],
            "default": "HIGH",
        },
        **_COMMON_GEN_OPTIONS,
        **_ADVANCED_TEXT_OPTIONS,
    },
    "gemini-3-pro-preview": {
        "thinking_level": {
            "type": "choice",
            "label": "Thinking",
            "values": ["HIGH", "MEDIUM", "LOW"],
            "default": "HIGH",
        },
        **_COMMON_GEN_OPTIONS,
        **_ADVANCED_TEXT_OPTIONS,
    },
    "gemini-3-flash-preview": {
        "thinking_level": {
            "type": "choice",
            "label": "Thinking",
            "values": ["HIGH", "MEDIUM", "LOW"],
            "default": "HIGH",
        },
        **_COMMON_GEN_OPTIONS,
        **_ADVANCED_TEXT_OPTIONS,
    },
    "gemini-2.5-pro": {
        "thinking_budget": {
            "type": "int",
            "label": "Thinking Budget",
            "min": 0,
            "max": 24576,
            "default": 2804,
        },
        **_COMMON_GEN_OPTIONS,
        **_ADVANCED_TEXT_OPTIONS,
    },
    "gemini-2.5-flash": {
        "thinking_budget": {
            "type": "int",
            "label": "Thinking Budget",
            "min": 0,
            "max": 24576,
            "default": 0,
        },
        **_COMMON_GEN_OPTIONS,
        **_ADVANCED_TEXT_OPTIONS,
    },
    "gemini-3.1-flash-image-preview": {
        "thinking_level": {
            "type": "choice",
            "label": "Thinking",
            "values": ["MINIMAL", "LOW", "MEDIUM", "HIGH"],
            "default": "MINIMAL",
        },
        **_NANOBANANA_ASPECT_RATIO_OPTION,
        **_NANOBANANA_31_SIZE_OPTION,
        **_IMAGE_GEN_OPTIONS,
        "google_search": {
            "type": "bool",
            "label": "Google Search",
            "default": False,
        },
        "image_search": {
            "type": "bool",
            "label": "Image Search",
            "default": False,
        },
    },
    "gemini-3-pro-image-preview": {
        **_NANOBANANA_ASPECT_RATIO_OPTION,
        **_NANOBANANA_SIZE_OPTION,
        **_IMAGE_GEN_OPTIONS,
    },
    "gemini-2.5-flash-image": {
        **_NANOBANANA_ASPECT_RATIO_OPTION,
        **_NANOBANANA_SIZE_OPTION,
        **_IMAGE_GEN_OPTIONS,
    },
    "imagen-4.0-generate-001": {
        **_IMAGEN_ASPECT_RATIO_OPTION,
        **_IMAGEN_SIZE_OPTION,
    },
    "imagen-4.0-ultra-generate-001": {
        **_IMAGEN_ASPECT_RATIO_OPTION,
        **_IMAGEN_SIZE_OPTION,
    },
    "imagen-4.0-fast-generate-001": {
        **_IMAGEN_ASPECT_RATIO_OPTION,
    },
}


def get_default_options(model: str) -> dict:
    """모델의 기본 옵션 반환 (내장 + 플러그인)"""
    from v.model_plugin import get_all_model_options
    schema = get_all_model_options().get(model, {})
    if not schema:
        return {}
    return {
        key: opt["default"]
        for key, opt in schema.items()
        if "default" in opt
    }


@dataclass
class ChatMessage:
    """채팅 메시지"""
    role: str  # "user" or "assistant"
    content: str
    attachments: List[str] = None  # 이미지 파일 경로 목록
    thought_signatures: list = None  # Gemini 3 멀티턴 시 필수 (모델 응답 파트별 서명)


class GeminiProvider:
    """Gemini API 프로바이더 (다중 키 라운드 로빈 지원)"""

    def __init__(self, api_key: str = None, api_keys: list[str] = None):
        if api_keys:
            self._api_keys = list(api_keys)
        else:
            single = api_key or os.environ.get("GEMINI_API_KEY")
            self._api_keys = [single] if single else []

        self.api_key = self._api_keys[0] if self._api_keys else None
        self._cancel_requested = False
        self._clients: list[genai.Client | None] = [None] * len(self._api_keys)
        self._client_index = 0
        self._lock = threading.Lock()

    @property
    def key_count(self) -> int:
        return len(self._api_keys)

    def _get_client(self) -> genai.Client:
        """클라이언트 인스턴스 (라운드 로빈, thread-safe)"""
        if not self._api_keys:
            raise RuntimeError("API key is required")

        with self._lock:
            idx = self._client_index
            self._client_index = (idx + 1) % len(self._api_keys)

        if self._clients[idx] is None:
            try:
                self._clients[idx] = genai.Client(api_key=self._api_keys[idx])
            except Exception:
                # P1: 생성 실패 시 캐시에 남기지 않음
                self._clients[idx] = None
                raise
        return self._clients[idx]

    def invalidate_client(self, key_index: int = -1):
        """P1: 캐시된 클라이언트 무효화 (인증 실패 시 호출)"""
        with self._lock:
            if key_index < 0:
                self._clients = [None] * len(self._api_keys)
            elif 0 <= key_index < len(self._clients):
                self._clients[key_index] = None

    def _get_client_with_index(self) -> tuple:
        """클라이언트 + 사용된 key index 반환 (batch job 추적용)"""
        if not self._api_keys:
            raise RuntimeError("API key is required")

        with self._lock:
            idx = self._client_index
            self._client_index = (idx + 1) % len(self._api_keys)

        if self._clients[idx] is None:
            self._clients[idx] = genai.Client(api_key=self._api_keys[idx])
        return self._clients[idx], idx

    def _get_client_at(self, key_index: int) -> genai.Client:
        """특정 API key index로 클라이언트 반환 (batch resume용)"""
        if key_index < 0 or key_index >= len(self._api_keys):
            raise IndexError(f"Key index {key_index} out of range (have {len(self._api_keys)} keys)")
        if self._clients[key_index] is None:
            self._clients[key_index] = genai.Client(api_key=self._api_keys[key_index])
        return self._clients[key_index]

    def _advanced_config_kwargs(self, options: dict) -> dict:
        """노드 옵션(stop_sequences / seed / json_mode)을 GenerateContentConfig 키워드로 변환.
        미지정(빈 리스트 / seed<0 / json_mode False)은 제외해 기본 동작을 건드리지 않는다."""
        kwargs = {}
        stop = options.get("stop_sequences")
        if stop:
            if isinstance(stop, str):
                stop = [s.strip() for s in stop.split(",") if s.strip()]
            stop = [s for s in stop if s]
            if stop:
                kwargs["stop_sequences"] = stop
        seed = options.get("seed")
        if seed is not None and isinstance(seed, int) and seed >= 0:
            kwargs["seed"] = seed
        if options.get("json_mode"):
            kwargs["response_mime_type"] = "application/json"
        return kwargs

    def _get_safety_settings(self) -> list:
        """공통 안전 설정 (모두 BLOCK_NONE)"""
        return [
            types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="BLOCK_NONE",
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="BLOCK_NONE",
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                threshold="BLOCK_NONE",
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="BLOCK_NONE",
            ),
        ]

    _MIME_MAP = {
        "png": "image/png", "jpg": "image/jpeg",
        "jpeg": "image/jpeg", "gif": "image/gif",
        "webp": "image/webp", "bmp": "image/bmp",
        "pdf": "application/pdf",
    }
    _SKIP_SIG = b"skip_thought_signature_validator"

    def _build_system_instruction(self, text, files):
        """시스템 프롬프트 텍스트/파일 → system_instruction Content 빌드"""
        if not text and not files:
            return None
        parts = []
        for fpath in (files or []):
            if not os.path.isfile(fpath):
                continue
            ext = fpath.lower().rsplit(".", 1)[-1] if "." in fpath else ""
            if ext in self._MIME_MAP:
                try:
                    with open(fpath, "rb") as f:
                        parts.append(types.Part.from_bytes(
                            data=f.read(),
                            mime_type=self._MIME_MAP[ext],
                        ))
                except Exception as e:
                    from v.logger import get_logger
                    logger = get_logger("qonvo.provider")
                    logger.warning(f"Failed to read file {fpath}: {e}")
            else:
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                    fname = os.path.basename(fpath)
                    parts.append(types.Part.from_text(text=f"[{fname}]:\n{content}"))
                except Exception as e:
                    from v.logger import get_logger
                    logger = get_logger("qonvo.provider")
                    logger.warning(f"Failed to read text file {fpath}: {e}")
        if text:
            parts.append(types.Part.from_text(text=text))
        return types.Content(parts=parts) if parts else None

    def _convert_messages(self, messages: List[ChatMessage]) -> list:
        """ChatMessage → types.Content 변환

        모델 응답 파트는 반드시 thought_signature를 포함해야 한다.
        저장된 서명이 있으면 그대로 복원, 없으면 skip validator로 fallback.
        """
        contents = []
        for msg in messages:
            parts = []
            is_model = (msg.role == "assistant")

            if is_model:
                # ── 모델 응답: 서명 복원 (텍스트 → 이미지 순서) ──
                decoded_sigs = []
                for s in (msg.thought_signatures or []):
                    if isinstance(s, str):
                        try:
                            decoded_sigs.append(base64.b64decode(s))
                        except Exception:
                            decoded_sigs.append(None)
                    elif isinstance(s, bytes):
                        decoded_sigs.append(s)
                    else:
                        decoded_sigs.append(None)

                sig_idx = 0
                def _next_sig():
                    nonlocal sig_idx
                    sig = decoded_sigs[sig_idx] if sig_idx < len(decoded_sigs) else None
                    sig_idx += 1
                    return sig if sig is not None else self._SKIP_SIG

                # 텍스트 파트 (항상 첫 번째)
                parts.append(types.Part(
                    text=msg.content,
                    thought_signature=_next_sig(),
                ))

                # 첨부 파일 파트
                if msg.attachments:
                    for fpath in msg.attachments:
                        ext = fpath.lower().rsplit(".", 1)[-1] if "." in fpath else ""
                        if ext in self._MIME_MAP:
                            try:
                                with open(fpath, "rb") as f:
                                    file_bytes = f.read()
                                parts.append(types.Part(
                                    inline_data=types.Blob(
                                        data=file_bytes,
                                        mime_type=self._MIME_MAP[ext],
                                    ),
                                    thought_signature=_next_sig(),
                                ))
                            except Exception as e:
                                from v.logger import get_logger
                                logger = get_logger("qonvo.provider")
                                logger.warning(f"Failed to read attachment {fpath}: {e}")
                        else:
                            try:
                                with open(fpath, "r", encoding="utf-8") as f:
                                    content = f.read()
                                fname = os.path.basename(fpath)
                                parts.append(types.Part(
                                    text=f"[{fname}]:\n{content}",
                                    thought_signature=_next_sig(),
                                ))
                            except Exception as e:
                                from v.logger import get_logger
                                logger = get_logger("qonvo.provider")
                                logger.warning(f"Failed to read text attachment {fpath}: {e}")
            else:
                # ── 유저 메시지: 서명 불필요 ──
                if msg.attachments:
                    pass  # attachments processing
                    for fpath in msg.attachments:
                        ext = fpath.lower().rsplit(".", 1)[-1] if "." in fpath else ""
                        fname = os.path.basename(fpath)
                        pass  # per-attachment
                        if ext in self._MIME_MAP:
                            try:
                                with open(fpath, "rb") as f:
                                    file_bytes = f.read()
                                pass  # file read ok
                                parts.append(types.Part.from_text(text=f"[{fname}]:"))
                                parts.append(types.Part.from_bytes(
                                    data=file_bytes,
                                    mime_type=self._MIME_MAP[ext],
                                ))
                            except Exception as e:
                                from v.logger import get_logger
                                logger = get_logger("qonvo.provider")
                                logger.warning(f"Failed to read user file {fpath}: {e}")
                                pass  # logged above
                        else:
                            try:
                                with open(fpath, "r", encoding="utf-8") as f:
                                    content = f.read()
                                parts.append(types.Part.from_text(
                                    text=f"[{fname}]:\n{content}"))
                            except Exception as e:
                                from v.logger import get_logger
                                logger = get_logger("qonvo.provider")
                                logger.warning(f"Failed to read user text file {fpath}: {e}")
                parts.append(types.Part.from_text(text=msg.content))

            role = "model" if is_model else msg.role
            contents.append(types.Content(role=role, parts=parts))
        return contents

    def chat(
        self,
        model: str,
        messages: List[ChatMessage],
        stream: bool = True,
        **options
    ) -> Generator[str, None, None] | str:
        """
        채팅 요청 (모델별 분기)
        - model: 모델 ID
        - messages: 대화 기록
        - stream: 스트리밍 여부
        - **options: 모델별 추가 옵션 (thinking_level, thinking_budget, aspect_ratio 등)
        """
        self._cancel_requested = False

        # 플러그인 모델 디스패치
        from v.model_plugin import PluginRegistry
        plugin = PluginRegistry.instance().get_plugin_for_model(model)
        if plugin:
            options.pop("system_prompt", None)
            options.pop("system_files", None)
            return plugin.chat(model, messages, stream, **options)

        # 시스템 프롬프트 빌드 (내장 모델용)
        sys_text = options.pop("system_prompt", "")
        sys_files = options.pop("system_files", [])
        options["_sys_instr"] = self._build_system_instruction(sys_text, sys_files)

        # 모델별 메서드 분기
        if model in ("gemini-3.1-pro-preview", "gemini-3-pro-preview"):
            return self._chat_gemini_3_pro(messages, stream, _model_id=model, **options)
        elif model == "gemini-3-flash-preview":
            return self._chat_gemini_3_flash(messages, stream, **options)
        elif model == "gemini-2.5-pro":
            return self._chat_gemini_25_pro(messages, stream, **options)
        elif model == "gemini-2.5-flash":
            return self._chat_gemini_25_flash(messages, stream, **options)
        elif model == "gemini-3.1-flash-image-preview":
            return self._chat_nanobanana_31(messages, stream, **options)
        elif model == "gemini-3-pro-image-preview":
            return self._chat_nanobanana_pro(messages, stream, **options)
        elif model == "gemini-2.5-flash-image":
            return self._chat_nanobanana(messages, stream, **options)
        elif model.startswith("imagen-4.0-"):
            return self._chat_imagen(model, messages, stream, **options)
        else:
            raise ValueError(f"Unknown model: {model}")

    def cancel(self):
        self._cancel_requested = True

    def _is_retryable(self, error):
        """에러가 재시도 가능한지 판별한다. 503/429 상태코드 및 unavailable, deadline, overloaded, resource exhausted 유형을 재시도 대상으로 본다."""
        msg = str(error).lower()
        return (
            "503" in msg or "unavailable" in msg
            or "deadline" in msg or "overloaded" in msg
            or "429" in msg or "resource exhausted" in msg
            or "10053" in msg or "10054" in msg
            or "connection" in msg and ("aborted" in msg or "reset" in msg)
        )

    def _retry_call(self, fn, max_retries=3, base_delay=2.0):
        """지수 백오프(2s, 4s, 8s)로 fn을 재시도한다. 매 시도 전에 cancel 상태를 확인하고, 재시도 시 로그 경고를 남긴다."""
        from v.logger import get_logger
        logger = get_logger("qonvo.provider")
        for attempt in range(max_retries + 1):
            try:
                return fn()
            except Exception as e:
                if self._cancel_requested:
                    raise
                if attempt == max_retries or not self._is_retryable(e):
                    raise
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"[RETRY] attempt {attempt+1}/{max_retries} failed: {e} — retrying in {delay:.0f}s"
                )
                time.sleep(delay)

    # ============================================================
    # Gemini 3.0 Pro
    # ============================================================
    def _chat_gemini_3_pro(
        self,
        messages: List[ChatMessage],
        stream: bool,
        **options
    ) -> Generator[str, None, None] | str:
        """Gemini 3.x Pro 채팅 (thinking 지원, 3.0/3.1 공용)"""
        model_id = options.pop("_model_id", "gemini-3-pro-preview")
        client = self._get_client()
        contents = self._convert_messages(messages)

        thinking_level = options.get("thinking_level", "HIGH")

        config = types.GenerateContentConfig(
            system_instruction=options.get("_sys_instr"),
            thinking_config=types.ThinkingConfig(
                thinking_level=thinking_level,
            ),
            temperature=options.get("temperature"),
            top_p=options.get("top_p"),
            max_output_tokens=options.get("max_output_tokens"),
            safety_settings=self._get_safety_settings(),
            **self._advanced_config_kwargs(options),
        )

        if stream:
            return self._stream_with_signatures(model_id, contents, config)
        else:
            response = self._retry_call(
                lambda: client.models.generate_content(
                    model=model_id, contents=contents, config=config,
                )
            )
            return response.text

    # ============================================================
    # Gemini 3.0 Flash
    # ============================================================
    def _chat_gemini_3_flash(
        self,
        messages: List[ChatMessage],
        stream: bool,
        **options
    ) -> Generator[str, None, None] | str:
        """Gemini 3.0 Flash 채팅 (thinking 지원)"""
        client = self._get_client()
        contents = self._convert_messages(messages)

        thinking_level = options.get("thinking_level", "HIGH")

        config = types.GenerateContentConfig(
            system_instruction=options.get("_sys_instr"),
            thinking_config=types.ThinkingConfig(
                thinking_level=thinking_level,
            ),
            temperature=options.get("temperature"),
            top_p=options.get("top_p"),
            max_output_tokens=options.get("max_output_tokens"),
            safety_settings=self._get_safety_settings(),
            **self._advanced_config_kwargs(options),
        )

        if stream:
            return self._stream_with_signatures("gemini-3-flash-preview", contents, config)
        else:
            response = self._retry_call(
                lambda: client.models.generate_content(
                    model="gemini-3-flash-preview", contents=contents, config=config,
                )
            )
            return response.text

    # ============================================================
    # Gemini 2.5 Pro
    # ============================================================
    def _chat_gemini_25_pro(
        self,
        messages: List[ChatMessage],
        stream: bool,
        **options
    ) -> Generator[str, None, None] | str:
        """Gemini 2.5 Pro 채팅 (thinking budget 지원)"""
        client = self._get_client()
        contents = self._convert_messages(messages)

        thinking_budget = options.get("thinking_budget", 2804)

        config = types.GenerateContentConfig(
            system_instruction=options.get("_sys_instr"),
            thinking_config=types.ThinkingConfig(
                thinking_budget=thinking_budget,
            ),
            temperature=options.get("temperature"),
            top_p=options.get("top_p"),
            max_output_tokens=options.get("max_output_tokens"),
            safety_settings=self._get_safety_settings(),
            **self._advanced_config_kwargs(options),
        )

        if stream:
            return self._stream_with_signatures("gemini-2.5-pro", contents, config)
        else:
            response = self._retry_call(
                lambda: client.models.generate_content(
                    model="gemini-2.5-pro", contents=contents, config=config,
                )
            )
            return response.text

    # ============================================================
    # Gemini 2.5 Flash
    # ============================================================
    def _chat_gemini_25_flash(
        self,
        messages: List[ChatMessage],
        stream: bool,
        **options
    ) -> Generator[str, None, None] | str:
        """Gemini 2.5 Flash 채팅 (thinking budget 지원)"""
        client = self._get_client()
        contents = self._convert_messages(messages)

        thinking_budget = options.get("thinking_budget", 0)

        config = types.GenerateContentConfig(
            system_instruction=options.get("_sys_instr"),
            thinking_config=types.ThinkingConfig(
                thinking_budget=thinking_budget,
            ),
            temperature=options.get("temperature"),
            top_p=options.get("top_p"),
            max_output_tokens=options.get("max_output_tokens"),
            safety_settings=self._get_safety_settings(),
            **self._advanced_config_kwargs(options),
        )

        if stream:
            return self._stream_with_signatures("gemini-2.5-flash", contents, config)
        else:
            response = self._retry_call(
                lambda: client.models.generate_content(
                    model="gemini-2.5-flash", contents=contents, config=config,
                )
            )
            return response.text

    # ============================================================
    # Nanobanana Pro (이미지 생성)
    # ============================================================
    def _chat_nanobanana_pro(
        self,
        messages: List[ChatMessage],
        stream: bool,
        **options
    ) -> dict:
        """
        🍌Nanobanana Pro 이미지 생성
        반환: {"text": str, "images": [bytes, ...]}
        스트리밍 미지원 (이미지 생성은 동기식만)
        """
        _ = stream  # unused
        client = self._get_client()
        contents = self._convert_messages(messages)

        aspect_ratio = options.get("aspect_ratio", "1:1")
        image_size = options.get("image_size", "1K")

        config = types.GenerateContentConfig(
            system_instruction=options.get("_sys_instr"),
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=image_size,
            ),
            temperature=options.get("temperature"),
            top_p=options.get("top_p"),
            safety_settings=self._get_safety_settings(),
        )

        response = self._retry_call(
            lambda: client.models.generate_content(
                model="gemini-3-pro-image-preview", contents=contents, config=config,
            )
        )

        return self._parse_image_response(response)

    # ============================================================
    # Nanobanana (이미지 생성)
    # ============================================================
    def _chat_nanobanana(
        self,
        messages: List[ChatMessage],
        stream: bool,
        **options
    ) -> dict:
        """
        🍌Nanobanana 이미지 생성
        반환: {"text": str, "images": [bytes, ...]}
        스트리밍 미지원 (이미지 생성은 동기식만)
        """
        _ = stream  # unused
        client = self._get_client()
        contents = self._convert_messages(messages)

        aspect_ratio = options.get("aspect_ratio", "1:1")
        image_size = options.get("image_size", "1K")

        config = types.GenerateContentConfig(
            system_instruction=options.get("_sys_instr"),
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=image_size,
            ),
            temperature=options.get("temperature"),
            top_p=options.get("top_p"),
            safety_settings=self._get_safety_settings(),
        )

        response = self._retry_call(
            lambda: client.models.generate_content(
                model="gemini-2.5-flash-image", contents=contents, config=config,
            )
        )

        return self._parse_image_response(response)

    # ============================================================
    # Nanobanana 3.1 (스트리밍 이미지 생성)
    # ============================================================
    def _chat_nanobanana_31(
        self,
        messages: List[ChatMessage],
        stream: bool,
        **options
    ) -> dict:
        _ = stream
        client = self._get_client()
        contents = self._convert_messages(messages)

        aspect_ratio = options.get("aspect_ratio", "1:1")
        thinking_level = options.get("thinking_level", "MINIMAL")

        image_size = options.get("image_size", "1K")

        tools = None
        use_google_search = options.get("google_search", False)
        use_image_search = options.get("image_search", False)
        if use_google_search or use_image_search:
            search_types = None
            if use_google_search and use_image_search:
                search_types = types.SearchTypes(
                    web_search=types.WebSearch(),
                    image_search=types.ImageSearch(),
                )
            elif use_image_search:
                search_types = types.SearchTypes(
                    image_search=types.ImageSearch(),
                )
            tools = [types.Tool(
                googleSearch=types.GoogleSearch(
                    search_types=search_types,
                ),
            )]

        config = types.GenerateContentConfig(
            system_instruction=options.get("_sys_instr"),
            response_modalities=["IMAGE", "TEXT"],
            thinking_config=types.ThinkingConfig(
                thinking_level=thinking_level,
            ),
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=image_size,
            ),
            temperature=options.get("temperature"),
            top_p=options.get("top_p"),
            safety_settings=self._get_safety_settings(),
            tools=tools,
        )

        result = {"text": "", "images": [], "thought_signatures": []}
        image_sigs = []
        text_sig = None
        last_chunk = None

        for chunk in self._retry_call(
            lambda: client.models.generate_content_stream(
                model="gemini-3.1-flash-image-preview", contents=contents, config=config,
            )
        ):
            last_chunk = chunk
            if self._cancel_requested:
                break
            if chunk.parts is None:
                continue
            for part in chunk.parts:
                if getattr(part, 'thought', False):
                    continue
                sig = getattr(part, 'thought_signature', None)
                if isinstance(sig, bytes):
                    sig = base64.b64encode(sig).decode('ascii')
                if part.inline_data and part.inline_data.data:
                    result["images"].append(part.inline_data.data)
                    image_sigs.append(sig)
                elif part.text:
                    result["text"] += part.text
                    text_sig = sig

        result["thought_signatures"] = [text_sig] + image_sigs

        if last_chunk and hasattr(last_chunk, 'usage_metadata') and last_chunk.usage_metadata:
            result["prompt_tokens"] = getattr(last_chunk.usage_metadata, 'prompt_token_count', 0)
            result["candidates_tokens"] = getattr(last_chunk.usage_metadata, 'candidates_token_count', 0)

        if not result["images"] and not result["text"] and last_chunk:
            try:
                cands = getattr(last_chunk, 'candidates', None) or []
                if cands:
                    finish_reason = str(getattr(cands[0], 'finish_reason', '') or '')
                    if finish_reason and finish_reason not in ('STOP', 'FinishReason.STOP'):
                        result["text"] = f"[이미지 생성 실패: {finish_reason}]"
                if not result["text"]:
                    pf = getattr(last_chunk, 'prompt_feedback', None)
                    if pf:
                        br = str(getattr(pf, 'block_reason', '') or '')
                        if br:
                            result["text"] = f"[프롬프트 차단됨: {br}]"
            except Exception:
                pass

        return result

    # ============================================================
    # Imagen 4.0 (순수 이미지 생성)
    # ============================================================
    def _chat_imagen(
        self,
        model: str,
        messages: List[ChatMessage],
        stream: bool,
        **options
    ) -> dict:
        """
        🎨 Imagen 4.0 이미지 생성
        generate_images API 사용 (멀티턴 미지원, 마지막 메시지만 프롬프트로 사용)

        Phase 4: 장시간 작업 시간 로깅 추가
        """
        _ = stream
        client = self._get_client()

        # Imagen은 참조 이미지를 지원하지 않음 (text-to-image 전용) — 경고 출력
        has_images = any(msg.attachments for msg in messages if msg.attachments)
        if has_images:
            from v.logger import get_logger
            logger = get_logger("qonvo.provider")
            logger.warning(
                "Imagen does not support reference images (text-to-image only). "
                "Use Nanobanana Pro for image-to-image tasks."
            )

        # 마지막 유저 메시지를 프롬프트로 사용
        prompt = ""
        for msg in reversed(messages):
            if msg.role == "user" and msg.content:
                prompt = msg.content
                break
        if not prompt:
            return {"text": "", "images": [], "thought_signatures": []}

        aspect_ratio = options.get("aspect_ratio", "1:1")

        config_dict = {
            "number_of_images": 1,
            "output_mime_type": "image/jpeg",
            "person_generation": "ALLOW_ADULT",
            "aspect_ratio": aspect_ratio,
        }
        if "fast" not in model:
            config_dict["image_size"] = options.get("image_size", "1K")

        # Phase 4: 이미지 생성 소요 시간 로깅
        try:
            from v.logger import get_logger
            logger = get_logger("qonvo.provider")
            logger.info(f"Image generation started: {prompt[:50]}... (model: {model})")
        except:
            logger = None

        start_time = time.time()
        result = self._retry_call(
            lambda: client.models.generate_images(
                model=f"models/{model}", prompt=prompt, config=config_dict,
            )
        )
        elapsed = time.time() - start_time

        if logger:
            logger.info(f"Image generation completed in {elapsed:.2f}s")

        if not result.generated_images:
            reason = ""
            try:
                fr = str(getattr(result, 'finish_reason', '') or '')
                if fr:
                    reason = fr
            except Exception:
                pass
            error_text = f"[이미지 생성 실패: {reason}]" if reason else "[이미지 생성 실패: 결과 없음]"
            return {"text": error_text, "images": [], "thought_signatures": []}

        images = []
        for generated_image in result.generated_images:
            images.append(generated_image.image.image_bytes)

        return {
            "text": "",
            "images": images,
            "thought_signatures": [],
        }

    def _stream_with_signatures(self, model, contents, config):
        """스트리밍 생성 결과를 전달하면서 thought_signature를 수집한다. 초기 연결 실패 시 재시도한다."""
        def stream_gen():
            usage = None
            sigs = []
            stream_error = None
            try:
                stream_iter = None
                for attempt in range(4):
                    try:
                        stream_iter = self._get_client().models.generate_content_stream(
                            model=model, contents=contents, config=config,
                        )
                        break
                    except Exception as e:
                        if self._cancel_requested or attempt == 3 or not self._is_retryable(e):
                            raise
                        delay = 2.0 * (2 ** attempt)
                        from v.logger import get_logger
                        get_logger("qonvo.provider").warning(
                            f"[RETRY-STREAM] attempt {attempt+1}/3 failed: {e} — retrying in {delay:.0f}s"
                        )
                        time.sleep(delay)
                for chunk in stream_iter:
                    if self._cancel_requested:
                        break
                    if chunk.text:
                        yield chunk.text
                    if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                        usage = chunk.usage_metadata
                    if hasattr(chunk, 'candidates') and chunk.candidates:
                        for part in (chunk.candidates[0].content.parts or []):
                            if getattr(part, 'thought', False):
                                continue
                            sig = getattr(part, 'thought_signature', None)
                            if sig:
                                encoded = base64.b64encode(sig).decode('ascii') if isinstance(sig, bytes) else sig
                                sigs.append(encoded)
            except Exception as e:
                stream_error = e
            # 메타데이터는 오류 여부와 무관하게 항상 전달
            if usage:
                yield {"__usage__": True,
                       "prompt_tokens": getattr(usage, 'prompt_token_count', 0),
                       "candidates_tokens": getattr(usage, 'candidates_token_count', 0)}
            if sigs:
                yield {"__thought_signatures__": sigs}
            if stream_error:
                yield {"__error__": str(stream_error)}
        return stream_gen()

    def _parse_image_response(self, response) -> dict:
        """이미지 생성 응답 파싱"""
        result = {"text": "", "images": [], "thought_signatures": []}

        # 응답 차단(검열) 감지
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            finish_reason = getattr(candidate, 'finish_reason', None)
            if finish_reason and str(finish_reason) not in ('STOP', 'FinishReason.STOP', 'MAX_TOKENS', 'FinishReason.MAX_TOKENS'):
                reason_str = str(finish_reason)
                pass  # blocked
                result["text"] = f"[응답 차단됨: {reason_str}]"
                return result
        if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
            block_reason = getattr(response.prompt_feedback, 'block_reason', None)
            if block_reason:
                reason_str = str(block_reason)
                pass  # prompt blocked
                result["text"] = f"[프롬프트 차단됨: {reason_str}]"
                return result

        if not (response.parts or []):
            pass  # empty response

        text_sig = None   # 마지막 텍스트 파트의 서명 (다중 텍스트 파트 대응)
        image_sigs = []   # 이미지별 서명

        for part in (response.parts or []):
            # thinking 파트 스킵
            if getattr(part, 'thought', False):
                continue
            sig = getattr(part, 'thought_signature', None)
            # bytes → base64 문자열 (JSON 직렬화 가능하게)
            if isinstance(sig, bytes):
                sig = base64.b64encode(sig).decode('ascii')
            if part.text:
                result["text"] += part.text
                text_sig = sig   # 마지막 텍스트 서명 유지
            elif part.inline_data:
                result["images"].append(part.inline_data.data)
                image_sigs.append(sig)

        # 정렬 보장: [text_sig, img0_sig, img1_sig, ...]
        result["thought_signatures"] = [text_sig] + image_sigs

        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            result["prompt_tokens"] = getattr(response.usage_metadata, 'prompt_token_count', 0)
            result["candidates_tokens"] = getattr(response.usage_metadata, 'candidates_token_count', 0)

        return result

    # ============================================================
    # Batch API 다중 결과 생성 (50% 할인)
    # ============================================================
    def chat_candidates(
        self,
        model: str,
        messages: List[ChatMessage],
        count: int = 1,
        on_job_created=None,
        **options
    ) -> list | None:
        """
        Gemini Batch API (client.batches.create)로 N개 요청을 배치 처리.

        인라인 요청 방식으로 N개의 동일 요청을 한 번에 제출.
        표준 API 대비 50% 할인. 비동기 처리 후 폴링으로 결과 수신.

        Args:
            model: 모델 ID (Imagen 제외 모든 모델 지원)
            messages: 대화 기록
            count: 요청할 결과 수
            on_job_created: 콜백(job_name, key_index) — job 생성 직후 호출 (큐 저장용)
            **options: 모델별 추가 옵션

        Returns:
            list — 결과 리스트 (str: 텍스트 모델, dict: 이미지 모델)
            None — 실패 시 (caller가 병렬 Worker로 fallback)
        """
        if count <= 1:
            result = self.chat(model, messages, stream=False, **options)
            return [result] if result is not None else None

        # 플러그인 모델 디스패치
        from v.model_plugin import PluginRegistry
        plugin = PluginRegistry.instance().get_plugin_for_model(model)
        if plugin:
            return plugin.chat_candidates(model, messages, count, **options)

        self._cancel_requested = False

        # System instruction + contents 빌드
        sys_text = options.get("system_prompt", "")
        sys_files = options.get("system_files", [])
        sys_instr = self._build_system_instruction(sys_text, sys_files)

        client, key_index = self._get_client_with_index()
        contents = self._convert_messages(messages)

        # 모델별 GenerateContentConfig 빌드
        is_nanobanana = model in ("gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview", "gemini-2.5-flash-image")

        config_kwargs = {"safety_settings": self._get_safety_settings()}
        if sys_instr:
            config_kwargs["system_instruction"] = sys_instr

        if is_nanobanana:
            config_kwargs["response_modalities"] = ["IMAGE", "TEXT"]
            img_cfg = {"aspect_ratio": options.get("aspect_ratio", "1:1")}
            if model == "gemini-3.1-flash-image-preview":
                img_cfg["image_size"] = options.get("image_size", "1K")
                config_kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_level=options.get("thinking_level", "MINIMAL"),
                )
            config_kwargs["image_config"] = types.ImageConfig(**img_cfg)
        elif model in ("gemini-3.1-pro-preview", "gemini-3-pro-preview", "gemini-3-flash-preview"):
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=options.get("thinking_level", "HIGH"),
            )
        elif model in ("gemini-2.5-pro", "gemini-2.5-flash"):
            budget = options.get("thinking_budget", 2804 if "pro" in model else 0)
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=budget,
            )

        # 공통 생성 옵션
        for key in ("temperature", "top_p", "max_output_tokens"):
            val = options.get(key)
            if val is not None:
                config_kwargs[key] = val

        config = types.GenerateContentConfig(**config_kwargs)

        # 인라인 요청 N개 빌드
        inline_requests = [
            {"contents": contents, "config": config}
            for _ in range(count)
        ]

        # Batch job 생성
        try:
            batch_job = client.batches.create(
                model=model,
                src=inline_requests,
                config={"display_name": f"qonvo-batch-{count}"},
            )
        except Exception:
            return None

        # 큐 저장 콜백 (디스크에 즉시 저장)
        if on_job_created:
            try:
                on_job_created(batch_job.name, key_index)
            except Exception:
                pass

        # 완료까지 폴링 (5초 간격)
        completed_states = {
            'JOB_STATE_SUCCEEDED', 'JOB_STATE_FAILED',
            'JOB_STATE_CANCELLED', 'JOB_STATE_EXPIRED',
        }
        while batch_job.state.name not in completed_states:
            if self._cancel_requested:
                try:
                    client.batches.cancel(name=batch_job.name)
                except Exception:
                    pass
                return None
            time.sleep(5)
            batch_job = client.batches.get(name=batch_job.name)

        if batch_job.state.name != 'JOB_STATE_SUCCEEDED':
            return None

        # 결과 추출
        results = []
        for inline_resp in (batch_job.dest.inlined_responses or []):
            if not inline_resp.response:
                continue
            resp = inline_resp.response
            if is_nanobanana:
                results.append(self._parse_image_response(resp))
            else:
                text = ""
                for candidate in (resp.candidates or []):
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if getattr(part, 'thought', False):
                                continue
                            if part.text:
                                text += part.text
                if text:
                    results.append(text)

        return results if results else None

    # ============================================================
    # Batch job 폴링 재개 (앱 재시작 후)
    # ============================================================
    def poll_batch_job(
        self,
        job_name: str,
        key_index: int,
        is_nanobanana: bool,
    ) -> list | None:
        """
        기존 batch job의 폴링 재개.

        앱 재시작 후 batch_queue.json에서 읽은 job_name으로
        결과를 수신. 동일 API key(key_index)로 조회.

        Returns:
            list — 결과 리스트 (str 또는 dict)
            None — 실패/만료/취소
        """
        self._cancel_requested = False

        try:
            client = self._get_client_at(key_index)
        except (IndexError, RuntimeError) as e:
            from v.logger import get_logger
            get_logger("qonvo.provider").error(
                f"[BATCH_POLL] Client init failed for key_index={key_index}: {e}"
            )
            return None

        try:
            batch_job = client.batches.get(name=job_name)
        except Exception as e:
            from v.logger import get_logger
            get_logger("qonvo.provider").error(
                f"[BATCH_POLL] Failed to get batch job {job_name}: {e}"
            )
            return None

        # 완료까지 폴링 (5초 간격)
        completed_states = {
            'JOB_STATE_SUCCEEDED', 'JOB_STATE_FAILED',
            'JOB_STATE_CANCELLED', 'JOB_STATE_EXPIRED',
        }
        while batch_job.state.name not in completed_states:
            if self._cancel_requested:
                try:
                    client.batches.cancel(name=job_name)
                except Exception:
                    pass
                return None
            time.sleep(5)
            try:
                batch_job = client.batches.get(name=job_name)
            except Exception:
                return None

        if batch_job.state.name != 'JOB_STATE_SUCCEEDED':
            return None

        # 결과 추출
        results = []
        for inline_resp in (batch_job.dest.inlined_responses or []):
            if not inline_resp.response:
                continue
            resp = inline_resp.response
            if is_nanobanana:
                results.append(self._parse_image_response(resp))
            else:
                text = ""
                for candidate in (resp.candidates or []):
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if getattr(part, 'thought', False):
                                continue
                            if part.text:
                                text += part.text
                if text:
                    results.append(text)

        return results if results else None
