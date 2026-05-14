"""Qonvo OpenAI Plugin -- GPT-4o, o3-mini, DALL-E 3, GPT Image"""
from v.model_plugin import ModelPlugin

_DALLE_MODELS = {"dall-e-3"}
_GPT_IMAGE_MODELS = {
    "gpt-image-1", "gpt-image-1-mini",
    "gpt-image-2", "gpt-image-2-mini",
}

_IMAGE_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "gif": "image/gif",
}


class OpenAIPlugin(ModelPlugin):
    NAME = "OpenAI"
    VERSION = "1.0"
    DESCRIPTION = "OpenAI GPT + DALL-E models"
    MODELS = {
        "gpt-4o": "GPT-4o",
        "gpt-4o-mini": "GPT-4o mini",
        "o3-mini": "o3-mini",
        "dall-e-3": "DALL-E 3",
        "gpt-image-1": "🎨 GPT Image",
        "gpt-image-1-mini": "🎨 GPT Image Mini",
        "gpt-image-2": "🎨 GPT Image 2",
        "gpt-image-2-mini": "🎨 GPT Image 2 Mini",
    }
    MODEL_OPTIONS = {
        "gpt-4o": {
            "temperature": {
                "type": "float", "label": "Temperature",
                "min": 0.0, "max": 2.0, "step": 0.05, "default": 1.0,
            },
            "max_output_tokens": {
                "type": "int", "label": "Max Tokens",
                "min": 1, "max": 16384, "default": 4096,
            },
        },
        "gpt-4o-mini": {
            "temperature": {
                "type": "float", "label": "Temperature",
                "min": 0.0, "max": 2.0, "step": 0.05, "default": 1.0,
            },
            "max_output_tokens": {
                "type": "int", "label": "Max Tokens",
                "min": 1, "max": 16384, "default": 4096,
            },
        },
        "o3-mini": {
            "max_output_tokens": {
                "type": "int", "label": "Max Tokens",
                "min": 1, "max": 65536, "default": 8192,
            },
        },
        "dall-e-3": {
            "aspect_ratio": {
                "type": "choice",
                "label": "Size",
                "values": ["1024x1024", "1792x1024", "1024x1792"],
                "default": "1024x1024",
            },
            "image_quality": {
                "type": "choice",
                "label": "Quality",
                "values": ["standard", "hd"],
                "default": "standard",
            },
        },
        "gpt-image-1": {
            "aspect_ratio": {
                "type": "choice",
                "label": "Size",
                "values": ["1024x1024", "1536x1024", "1024x1536"],
                "default": "1024x1024",
            },
            "image_quality": {
                "type": "choice",
                "label": "Quality",
                "values": ["low", "medium", "high"],
                "default": "high",
            },
            "background": {
                "type": "choice",
                "label": "Background",
                "values": ["auto", "transparent", "opaque"],
                "default": "auto",
            },
        },
        "gpt-image-1-mini": {
            "aspect_ratio": {
                "type": "choice",
                "label": "Size",
                "values": ["1024x1024", "1536x1024", "1024x1536"],
                "default": "1024x1024",
            },
            "image_quality": {
                "type": "choice",
                "label": "Quality",
                "values": ["low", "medium", "high"],
                "default": "medium",
            },
            "background": {
                "type": "choice",
                "label": "Background",
                "values": ["auto", "transparent", "opaque"],
                "default": "auto",
            },
        },
        "gpt-image-2": {
            "aspect_ratio": {
                "type": "choice",
                "label": "Size",
                "values": ["1024x1024", "1536x1024", "1024x1536", "auto"],
                "default": "1024x1024",
            },
            "image_quality": {
                "type": "choice",
                "label": "Quality",
                "values": ["low", "medium", "high", "auto"],
                "default": "high",
            },
            "background": {
                "type": "choice",
                "label": "Background",
                "values": ["auto", "transparent", "opaque"],
                "default": "auto",
            },
        },
        "gpt-image-2-mini": {
            "aspect_ratio": {
                "type": "choice",
                "label": "Size",
                "values": ["1024x1024", "1536x1024", "1024x1536", "auto"],
                "default": "1024x1024",
            },
            "image_quality": {
                "type": "choice",
                "label": "Quality",
                "values": ["low", "medium", "high", "auto"],
                "default": "medium",
            },
            "background": {
                "type": "choice",
                "label": "Background",
                "values": ["auto", "transparent", "opaque"],
                "default": "auto",
            },
        },
    }

    def __init__(self):
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    def chat(self, model, messages, stream=True, **options):
        if not self._api_keys:
            raise ValueError("OpenAI API key not configured")
        self._cancel_requested = False

        if model in _DALLE_MODELS:
            return self._generate_image(model, messages, **options)

        if model in _GPT_IMAGE_MODELS:
            return self._generate_gpt_image(model, messages, **options)

        return self._chat_text(model, messages, stream, **options)

    def chat_candidates(self, model, messages, n, **options):
        """OpenAI Batch API로 N개 결과 생성 (50% 할인, 24h SLA).

        텍스트 모델: /v1/chat/completions
        이미지 모델: /v1/images/generations  (※ images.edit i2i는 batch 미지원 — 첨부 무시)
        """
        if not self._api_keys:
            raise ValueError("OpenAI API key not configured")
        count = n
        if count <= 1:
            r = self.chat(model, messages, stream=False, **options)
            return [r] if r is not None else None

        import json, time, base64
        from openai import OpenAI
        try:
            from v.logger import get_logger
            log = get_logger("qonvo.openai.batch")
        except Exception:
            log = None

        client = OpenAI(api_key=self._api_keys[0])
        self._cancel_requested = False

        is_image = model in _DALLE_MODELS or model in _GPT_IMAGE_MODELS
        if is_image:
            endpoint = "/v1/images/generations"
            body = self._build_image_body(model, messages, **options)
            if body is None:
                return None
        else:
            endpoint = "/v1/chat/completions"
            body = self._build_chat_body(model, messages, **options)

        # JSONL: N개 동일 요청
        lines = [
            json.dumps({
                "custom_id": f"req-{i}",
                "method": "POST",
                "url": endpoint,
                "body": body,
            })
            for i in range(count)
        ]
        jsonl_data = ("\n".join(lines) + "\n").encode("utf-8")

        # 1) 파일 업로드
        try:
            file_obj = client.files.create(
                file=("batch.jsonl", jsonl_data, "application/jsonl"),
                purpose="batch",
            )
        except Exception as e:
            if log: log.error(f"[BATCH] file upload failed: {e}")
            return None

        # 2) batch 생성
        # NOTE: SDK 타입 스텁은 /v1/images/generations를 미허용으로 표시하지만
        # 런타임에서는 OpenAI 서버가 받아주므로 cast로 통과시킨다. 미지원이면 except로 빠짐.
        from typing import cast, Any
        try:
            batch = client.batches.create(
                input_file_id=file_obj.id,
                endpoint=cast(Any, endpoint),
                completion_window="24h",
            )
        except Exception as e:
            if log: log.error(f"[BATCH] create failed: {e}")
            try: client.files.delete(file_obj.id)
            except Exception: pass
            return None

        if log:
            log.info(f"[BATCH] created id={batch.id} count={count} model={model} endpoint={endpoint}")

        # 3) 폴링
        completed = {"completed", "failed", "expired", "cancelled"}
        while batch.status not in completed:
            if self._cancel_requested:
                try: client.batches.cancel(batch.id)
                except Exception: pass
                if log: log.info(f"[BATCH] cancelled by user: {batch.id}")
                return None
            time.sleep(5)
            try:
                batch = client.batches.retrieve(batch.id)
            except Exception as e:
                if log: log.warning(f"[BATCH] retrieve failed: {e}")
                time.sleep(5)

        if batch.status != "completed":
            if log: log.warning(f"[BATCH] {batch.id} ended with status={batch.status}")
            return None

        # 4) 결과 다운로드
        out_id: str | None = getattr(batch, "output_file_id", None)
        if not out_id:
            if log: log.warning(f"[BATCH] no output_file_id on {batch.id}")
            return None
        try:
            content = client.files.content(cast(str, out_id)).read()
        except Exception as e:
            if log: log.error(f"[BATCH] output read failed: {e}")
            return None

        # 5) JSONL 파싱
        results_by_id = {}
        for line in content.decode("utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            cid = entry.get("custom_id")
            resp = entry.get("response") or {}
            if resp.get("status_code") != 200:
                continue
            body_resp = resp.get("body") or {}
            if is_image:
                images = []
                for item in body_resp.get("data") or []:
                    b64 = item.get("b64_json")
                    if b64:
                        try:
                            images.append(base64.b64decode(b64))
                        except Exception:
                            pass
                results_by_id[cid] = {"text": "", "images": images, "thought_signatures": []}
            else:
                choices = body_resp.get("choices") or []
                if choices:
                    text = (choices[0].get("message") or {}).get("content", "")
                    results_by_id[cid] = text

        results = [results_by_id[f"req-{i}"] for i in range(count) if f"req-{i}" in results_by_id]
        if log:
            log.info(f"[BATCH] {batch.id} done: {len(results)}/{count} results")
        return results if results else None

    def _build_chat_body(self, model, messages, **options):
        """Batch JSONL용 /v1/chat/completions 바디 빌드."""
        import base64
        oai_messages = []
        sys_prompt = options.pop("system_prompt", None)
        if sys_prompt:
            oai_messages.append({"role": "system", "content": sys_prompt})
        for msg in messages:
            parts = [{"type": "text", "text": msg.content or ""}]
            if msg.attachments:
                for path in msg.attachments:
                    try:
                        with open(path, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode()
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        })
                    except Exception:
                        continue
            oai_messages.append({"role": msg.role, "content": parts})
        body = {"model": model, "messages": oai_messages}
        if "temperature" in options:
            body["temperature"] = options["temperature"]
        if "max_output_tokens" in options:
            body["max_tokens"] = options["max_output_tokens"]
        return body

    def _build_image_body(self, model, messages, **options):
        """Batch JSONL용 /v1/images/generations 바디 빌드 (t2i only)."""
        prompt_parts = []
        sys_prompt = options.pop("system_prompt", None)
        if sys_prompt:
            prompt_parts.append(sys_prompt)
        for msg in messages:
            if msg.content:
                prompt_parts.append(msg.content)
        prompt = "\n\n".join(p for p in prompt_parts if p).strip()
        if not prompt:
            return None
        body = {"model": model, "prompt": prompt, "n": 1}
        size = options.get("aspect_ratio")
        if size:
            body["size"] = size
        if model in _GPT_IMAGE_MODELS:
            body["quality"] = options.get("image_quality", "high")
            bg = options.get("background", "auto")
            if bg and bg != "auto":
                body["background"] = bg
        else:
            body["quality"] = options.get("image_quality", "standard")
            body["response_format"] = "b64_json"
        return body

    def _chat_text(self, model, messages, stream, **options):
        from openai import OpenAI
        client = OpenAI(api_key=self._api_keys[0])

        # ChatMessage -> OpenAI format
        oai_messages = []
        sys_prompt = options.pop("system_prompt", None)
        if sys_prompt:
            oai_messages.append({"role": "system", "content": sys_prompt})

        for msg in messages:
            content_parts = [{"type": "text", "text": msg.content}]
            if msg.attachments:
                import base64
                for path in msg.attachments:
                    with open(path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}
                    })
            oai_messages.append({"role": msg.role, "content": content_parts})

        params = {"model": model, "messages": oai_messages}
        if "temperature" in options:
            params["temperature"] = options["temperature"]
        if "max_output_tokens" in options:
            params["max_tokens"] = options["max_output_tokens"]

        if stream:
            def _stream():
                resp = client.chat.completions.create(**params, stream=True)
                total_in, total_out = 0, 0
                for chunk in resp:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        yield delta.content
                    if hasattr(chunk, "usage") and chunk.usage:
                        total_in = chunk.usage.prompt_tokens or 0
                        total_out = chunk.usage.completion_tokens or 0
                if total_in or total_out:
                    yield {
                        "__usage__": True,
                        "prompt_tokens": total_in,
                        "candidates_tokens": total_out,
                    }
            return _stream()
        else:
            resp = client.chat.completions.create(**params)
            return resp.choices[0].message.content

    def _generate_image(self, model, messages, **options):
        """DALL-E 이미지 생성 -- dict 반환 (StreamWorker가 image_received로 처리)"""
        from openai import OpenAI
        client = OpenAI(api_key=self._api_keys[0])

        # 마지막 유저 메시지를 프롬프트로 사용
        prompt = ""
        for msg in reversed(messages):
            if msg.role == "user" and msg.content:
                prompt = msg.content
                break
        if not prompt:
            return {"text": "", "images": [], "thought_signatures": []}

        size = options.get("aspect_ratio", "1024x1024")
        quality = options.get("image_quality", "standard")

        resp = client.images.generate(
            model=model,
            prompt=prompt,
            n=1,
            size=size,
            quality=quality,
            response_format="b64_json",
        )

        import base64
        images = []
        for item in resp.data:
            images.append(base64.b64decode(item.b64_json))

        return {
            "text": "",
            "images": images,
            "thought_signatures": [],
        }


    def _generate_gpt_image(self, model, messages, **options):
        """GPT Image 생성 — 첨부 이미지가 있으면 images.edit() (i2i), 없으면 generate() (t2i).

        OpenAI 이미지 API는 chat-format 미지원. 모든 메시지(+system_prompt)의
        텍스트를 단일 prompt로 합치고, 모든 메시지의 attachments를 모두 수집한다.
        """
        import os
        import base64
        from openai import OpenAI
        try:
            from v.logger import get_logger
            log = get_logger("qonvo.openai")
        except Exception:
            log = None
        client = OpenAI(api_key=self._api_keys[0])

        # system_prompt + 모든 메시지 본문을 하나의 prompt로 통합
        prompt_parts = []
        sys_prompt = options.pop("system_prompt", None)
        if sys_prompt:
            prompt_parts.append(sys_prompt)

        attachments = []
        for msg in messages:
            if msg.content:
                prompt_parts.append(msg.content)
            if msg.attachments:
                attachments.extend(msg.attachments)

        prompt = "\n\n".join(p for p in prompt_parts if p).strip()

        if log:
            log.info(
                f"[GPT_IMAGE] model={model} msgs={len(messages)} "
                f"prompt_len={len(prompt)} attachments={len(attachments)} sys={bool(sys_prompt)}"
            )

        if not prompt and not attachments:
            return {"text": "[GPT Image: 프롬프트도 첨부도 없음]", "images": [], "thought_signatures": []}
        if not prompt:
            prompt = "Edit this image."  # OpenAI는 prompt 필수

        size = options.get("aspect_ratio", "1024x1024")
        quality = options.get("image_quality", "high")
        background = options.get("background", "auto")

        # 보드 temp dir에서 상대 경로 해석을 위해 후보 디렉토리들 수집
        boards_temp = os.path.join(os.environ.get("APPDATA", ""), "Qonvo", "boards", ".temp")

        def _resolve(path):
            if os.path.isfile(path):
                return path
            base = os.path.basename(path)
            # boards/.temp/<board>/attachments/<base> 검색
            if os.path.isdir(boards_temp):
                for board_name in os.listdir(boards_temp):
                    cand = os.path.join(boards_temp, board_name, "attachments", base)
                    if os.path.isfile(cand):
                        return cand
            return None

        image_attachments = []
        for path in attachments:
            ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
            if ext not in _IMAGE_MIME:
                if log:
                    log.warning(f"[GPT_IMAGE] skip non-image attachment: {path}")
                continue
            resolved = _resolve(path)
            if resolved is None:
                if log:
                    log.warning(f"[GPT_IMAGE] attachment not found: {path}")
                continue
            try:
                with open(resolved, "rb") as f:
                    data = f.read()
                image_attachments.append(
                    (os.path.basename(resolved), data, _IMAGE_MIME[ext])
                )
                if log:
                    log.info(f"[GPT_IMAGE] loaded {resolved} ({len(data)} bytes)")
            except Exception as e:
                if log:
                    log.warning(f"[GPT_IMAGE] failed to read {resolved}: {e}")
                continue

        try:
            if image_attachments:
                kwargs = {
                    "model": model,
                    "image": image_attachments[0] if len(image_attachments) == 1 else image_attachments,
                    "prompt": prompt,
                    "n": 1,
                    "size": size,
                    "quality": quality,
                }
                if background and background != "auto":
                    kwargs["background"] = background
                resp = client.images.edit(**kwargs)
            else:
                resp = client.images.generate(
                    model=model,
                    prompt=prompt,
                    n=1,
                    size=size,
                    quality=quality,
                    background=background,
                )
        except Exception as e:
            return {
                "text": f"[이미지 생성 실패: {e}]",
                "images": [],
                "thought_signatures": [],
            }

        images = []
        for item in resp.data:
            if getattr(item, "b64_json", None):
                images.append(base64.b64decode(item.b64_json))

        return {
            "text": "",
            "images": images,
            "thought_signatures": [],
        }


PLUGIN_CLASS = OpenAIPlugin
