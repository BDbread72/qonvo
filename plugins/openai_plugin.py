"""Qonvo OpenAI Plugin -- GPT-4o, o3-mini, DALL-E 3"""
from v.model_plugin import ModelPlugin

# DALL-E 이미지 모델 ID
_DALLE_MODELS = {"dall-e-3"}


class OpenAIPlugin(ModelPlugin):
    NAME = "OpenAI"
    VERSION = "1.0"
    DESCRIPTION = "OpenAI GPT + DALL-E models"
    MODELS = {
        "gpt-4o": "GPT-4o",
        "gpt-4o-mini": "GPT-4o mini",
        "o3-mini": "o3-mini",
        "dall-e-3": "DALL-E 3",
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
    }

    def chat(self, model, messages, stream=True, **options):
        if not self._api_keys:
            raise ValueError("OpenAI API key not configured")

        if model in _DALLE_MODELS:
            return self._generate_image(model, messages, **options)

        return self._chat_text(model, messages, stream, **options)

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


PLUGIN_CLASS = OpenAIPlugin
