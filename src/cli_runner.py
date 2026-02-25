"""배치/단건 프롬프트 실행을 위한 러너 모듈."""

import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple


class BatchRunner:
    """단건 및 배치 실행을 담당하는 러너 클래스."""

    def __init__(self, router, default_model: str = "gemini-2.5-flash"):
        """라우터와 기본 모델을 초기화한다."""
        self._router = router
        self._default_model = default_model

    def run_single(
        self,
        prompt: str,
        model: str = None,
        system: str = None,
        options: Dict[str, Any] = None,
        on_chunk=None,
        attachments: list = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """단일 프롬프트를 실행하고 결과 텍스트와 메타 정보를 반환한다."""
        from v.provider import ChatMessage

        model = model or self._default_model
        opts = dict(options or {})
        if system:
            opts["system_prompt"] = system

        messages = [ChatMessage(role="user", content=prompt, attachments=attachments or None)]

        meta = {
            "model": model,
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "elapsed_seconds": 0,
            "error": None,
        }

        start = time.time()
        chunks = []

        try:
            result = self._router.chat(model, messages, stream=True, **opts)

            # 반환 타입에 따라 스트림/단건 결과를 처리한다.
            if isinstance(result, str):
                chunks.append(result)
                if on_chunk:
                    on_chunk(result)
            elif isinstance(result, dict):
                text = result.get("text", "")
                chunks.append(text)
                if on_chunk:
                    on_chunk(text)
            else:
                for item in result:
                    if isinstance(item, dict):
                        if "__usage__" in item:
                            meta["prompt_tokens"] = item.get("prompt_tokens", 0)
                            meta["candidates_tokens"] = item.get("candidates_tokens", 0)
                        elif "__error__" in item:
                            meta["error"] = item["__error__"]
                    else:
                        chunks.append(item)
                        if on_chunk:
                            on_chunk(item)
        except Exception as e:
            # 실행 중 예외는 메타에 기록한다.
            meta["error"] = str(e)

        meta["elapsed_seconds"] = round(time.time() - start, 2)
        return "".join(chunks), meta

    def run_file(self, path: Path) -> Tuple[str, Dict[str, Any]]:
        """파일(.txt/.json) 입력을 읽어 단건 실행한다."""
        path = Path(path)
        suffix = path.suffix.lower()

        if suffix == ".json":
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            prompt = data.get("prompt", "")
            model = data.get("model")
            system = data.get("system")
            options = data.get("options", {})
        elif suffix == ".txt":
            prompt = path.read_text(encoding="utf-8").strip()
            model = None
            system = None
            options = {}
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

        if not prompt:
            raise ValueError(f"Empty prompt in {path}")

        return self.run_single(prompt, model=model, system=system, options=options)

    def run_batch(
        self,
        input_dir: str,
        output_dir: str,
        model_override: str = None,
        on_progress=None,
    ) -> list:
        """입력 디렉터리의 파일들을 일괄 처리하고 결과를 저장한다."""
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        files = sorted(
            [f for f in input_path.iterdir() if f.suffix.lower() in (".txt", ".json")],
            key=lambda f: f.name,
        )

        if not files:
            raise FileNotFoundError(f"No .txt or .json files in {input_dir}")

        results = []
        total = len(files)

        for idx, file in enumerate(files, 1):
            stem = file.stem
            if on_progress:
                on_progress(idx, total, stem)

            try:
                text, meta = self.run_file(file)

                # 배치 실행에서 모델을 강제할 경우 메타만 덮어쓴다.
                if model_override:
                    meta["model"] = model_override

                result_file = output_path / f"{stem}_result.md"
                meta_file = output_path / f"{stem}_meta.json"

                result_file.write_text(text, encoding="utf-8")
                meta_file.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                results.append({"file": file.name, "status": "ok", "meta": meta})
            except Exception as e:
                # 개별 실패는 에러 메타로 기록하고 계속 진행한다.
                error_meta = {"file": file.name, "error": str(e)}
                meta_file = output_path / f"{stem}_meta.json"
                meta_file.write_text(
                    json.dumps(error_meta, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                results.append({"file": file.name, "status": "error", "error": str(e)})

        return results
