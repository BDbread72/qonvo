"""Qonvo Anthropic Plugin -- Claude models"""
from v.model_plugin import ModelPlugin


class AnthropicPlugin(ModelPlugin):
    NAME = "Anthropic"
    VERSION = "1.0"
    DESCRIPTION = "Anthropic Claude models"
    MODELS = {
        "claude-sonnet-4-5-20250929": "Claude 4.5 Sonnet",
        "claude-opus-4-6": "Claude Opus 4.6",
        "claude-haiku-4-5-20251001": "Claude 4.5 Haiku",
    }
    MODEL_OPTIONS = {
        "claude-sonnet-4-5-20250929": {
            "temperature": {
                "type": "float", "label": "Temperature",
                "min": 0.0, "max": 1.0, "step": 0.05, "default": 1.0,
            },
            "max_output_tokens": {
                "type": "int", "label": "Max Tokens",
                "min": 1, "max": 64000, "default": 8192,
            },
        },
        "claude-opus-4-6": {
            "temperature": {
                "type": "float", "label": "Temperature",
                "min": 0.0, "max": 1.0, "step": 0.05, "default": 1.0,
            },
            "max_output_tokens": {
                "type": "int", "label": "Max Tokens",
                "min": 1, "max": 64000, "default": 8192,
            },
        },
        "claude-haiku-4-5-20251001": {
            "temperature": {
                "type": "float", "label": "Temperature",
                "min": 0.0, "max": 1.0, "step": 0.05, "default": 1.0,
            },
            "max_output_tokens": {
                "type": "int", "label": "Max Tokens",
                "min": 1, "max": 64000, "default": 8192,
            },
        },
    }

    def chat(self, model, messages, stream=True, **options):
        import anthropic

        if not self._api_keys:
            raise ValueError("Anthropic API key not configured")
        client = anthropic.Anthropic(api_key=self._api_keys[0])

        # ChatMessage -> Anthropic format
        sys_prompt = options.pop("system_prompt", "")
        ant_messages = []
        for msg in messages:
            content_parts = [{"type": "text", "text": msg.content}]
            if msg.attachments:
                import base64
                for path in msg.attachments:
                    with open(path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    content_parts.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        }
                    })
            ant_messages.append({"role": msg.role, "content": content_parts})

        params = {"model": model, "messages": ant_messages, "max_tokens": 8192}
        if sys_prompt:
            params["system"] = sys_prompt
        if "temperature" in options:
            params["temperature"] = options["temperature"]
        if "max_output_tokens" in options:
            params["max_tokens"] = options["max_output_tokens"]

        if stream:
            def _stream():
                with client.messages.stream(**params) as resp:
                    for text in resp.text_stream:
                        yield text
                    msg = resp.get_final_message()
                    yield {
                        "__usage__": True,
                        "prompt_tokens": msg.usage.input_tokens,
                        "candidates_tokens": msg.usage.output_tokens,
                    }
            return _stream()
        else:
            resp = client.messages.create(**params)
            return resp.content[0].text


PLUGIN_CLASS = AnthropicPlugin
