"""Qonvo CLI 엔트리 포인트와 서브커맨드 정의 모듈."""

import sys
import os
import io
import argparse

# 콘솔 인코딩이 UTF-8이 아닐 때를 대비해 재설정한다.
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _init_provider(model: str = None):
    """프로바이더와 플러그인을 초기화하고 라우터를 반환한다."""
    from v.settings import get_api_keys
    from v.provider import GeminiProvider
    from v.model_plugin import PluginRegistry, ProviderRouter

    keys = get_api_keys()
    gemini = GeminiProvider(api_keys=keys) if keys else None

    registry = PluginRegistry.instance()
    registry.load_all()

    return ProviderRouter(gemini_provider=gemini)


def cmd_run(args):
    """단건 실행 커맨드 핸들러."""
    router = _init_provider(args.model)

    from cli_runner import BatchRunner

    runner = BatchRunner(router, default_model=args.model)

    sys.stdout.write(f"[{args.model}] Processing...\n")
    sys.stdout.flush()

    def on_chunk(chunk):
        """스트리밍 청크를 즉시 출력한다."""
        sys.stdout.write(chunk)
        sys.stdout.flush()

    images = args.image or []
    text, meta = runner.run_single(
        prompt=args.prompt,
        model=args.model,
        system=args.system,
        options=_parse_options(args),
        on_chunk=on_chunk,
        attachments=images or None,
    )

    sys.stdout.write("\n")

    if meta.get("error"):
        sys.stderr.write(f"\nError: {meta['error']}\n")

    tokens = meta.get("prompt_tokens", 0) + meta.get("candidates_tokens", 0)
    sys.stderr.write(
        f"---\n"
        f"Model: {meta['model']} | "
        f"Tokens: {tokens} (in:{meta.get('prompt_tokens',0)} out:{meta.get('candidates_tokens',0)}) | "
        f"Time: {meta.get('elapsed_seconds',0)}s\n"
    )

    if args.output:
        from pathlib import Path
        import json

        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        meta_path = out.with_suffix(".meta.json")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.stderr.write(f"Saved: {out}\n")


def cmd_batch(args):
    """배치 실행 커맨드 핸들러."""
    router = _init_provider(args.model)

    from cli_runner import BatchRunner

    runner = BatchRunner(router, default_model=args.model)
    repeat = getattr(args, "repeat", 1) or 1  # 반복 횟수 기본값 적용

    def on_progress(idx, total, name):
        """진행 상황을 콘솔에 출력한다."""
        sys.stdout.write(f"[{idx}/{total}] {name}...\n")
        sys.stdout.flush()

    all_results = []
    for r_idx in range(repeat):
        if repeat > 1:
            sys.stdout.write(f"\n=== Round {r_idx + 1}/{repeat} ===\n")
            sys.stdout.flush()

        output_dir = args.output
        if repeat > 1:
            output_dir = f"{args.output}/round_{r_idx + 1:02d}"  # 회차별 출력 경로

        results = runner.run_batch(
            input_dir=args.input_dir,
            output_dir=output_dir,
            model_override=args.model if args.model != "gemini-2.5-flash" else None,
            on_progress=on_progress,
        )
        all_results.extend(results)

    ok = sum(1 for r in all_results if r["status"] == "ok")
    fail = sum(1 for r in all_results if r["status"] == "error")
    total_label = f" ({repeat} rounds)" if repeat > 1 else ""
    sys.stdout.write(f"\nDone{total_label}: {ok} succeeded, {fail} failed\n")

    for r in all_results:
        if r["status"] == "error":
            sys.stderr.write(f"  FAIL: {r['file']} — {r['error']}\n")


def cmd_models(args):
    """모델 목록 출력 커맨드 핸들러."""
    from v.model_plugin import get_all_models

    models = get_all_models()
    sys.stdout.write(f"Available models ({len(models)}):\n\n")

    for model_id, display_name in models.items():
        sys.stdout.write(f"  {model_id:<40} {display_name}\n")


def cmd_config(args):
    """API 키 설정/조회 커맨드 핸들러."""
    if args.set_key:
        from v.settings import save_api_key
        save_api_key(args.set_key)
        sys.stdout.write("API key saved (encrypted).\n")
    elif args.list_keys:
        from v.settings import get_api_keys
        keys = get_api_keys()
        if keys:
            for i, k in enumerate(keys, 1):
                masked = k[:8] + "..." + k[-4:] if len(k) > 12 else "***"
                sys.stdout.write(f"  [{i}] {masked}\n")
        else:
            sys.stdout.write("No API keys configured.\n")
    elif args.set_keys:
        from v.settings import save_api_keys
        key_list = [k.strip() for k in args.set_keys.split(",") if k.strip()]
        save_api_keys(key_list)
        sys.stdout.write(f"Saved {len(key_list)} API key(s) (encrypted).\n")
    else:
        sys.stdout.write("Use --set-key, --set-keys, or --list-keys.\n")


def _parse_options(args) -> dict:
    """CLI 옵션을 실행 옵션으로 변환한다."""
    opts = {}
    if hasattr(args, "temperature") and args.temperature is not None:
        opts["temperature"] = args.temperature
    if hasattr(args, "max_tokens") and args.max_tokens is not None:
        opts["max_output_tokens"] = args.max_tokens
    return opts


def main():
    parser = argparse.ArgumentParser(
        prog="qonvo-cli",
        description="Qonvo CLI - interactive REPL & batch automation",
    )
    parser.add_argument("--workers", "-w", type=int, default=4,
                        help="Max concurrent workers for REPL mode (default: 4)")
    parser.add_argument("--results-dir", default=None,
                        help="Directory for REPL results (default: %%APPDATA%%/Qonvo/cli_results)")

    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Run a single prompt")
    p_run.add_argument("prompt", help="Prompt text")
    p_run.add_argument("--model", "-m", default="gemini-2.5-flash")
    p_run.add_argument("--system", "-s", default=None, help="System prompt")
    p_run.add_argument("--output", "-o", default=None, help="Save result to file")
    p_run.add_argument("--image", "-i", action="append", default=None,
                        help="Attach image file (repeatable)")
    p_run.add_argument("--temperature", "-t", type=float, default=None)
    p_run.add_argument("--max-tokens", type=int, default=None)
    p_run.set_defaults(func=cmd_run)

    p_batch = sub.add_parser("batch", help="Batch process a folder of requests")
    p_batch.add_argument("input_dir", help="Input directory with .txt/.json files")
    p_batch.add_argument("--output", "-o", default="results", help="Output directory")
    p_batch.add_argument("--model", "-m", default="gemini-2.5-flash")
    p_batch.add_argument("--repeat", "-r", type=int, default=1,
                         help="Run each file N times (results in round_01/, round_02/, ...)")
    p_batch.set_defaults(func=cmd_batch)

    p_models = sub.add_parser("models", help="List available models")
    p_models.set_defaults(func=cmd_models)

    p_config = sub.add_parser("config", help="Configure API keys")
    p_config.add_argument("--set-key", default=None, help="Set single Gemini API key")
    p_config.add_argument("--set-keys", default=None, help="Set multiple keys (comma-separated)")
    p_config.add_argument("--list-keys", action="store_true", help="List configured keys")
    p_config.set_defaults(func=cmd_config)

    args = parser.parse_args()

    if not args.command:
        from pathlib import Path
        from cli_repl import start_repl
        results_dir = Path(args.results_dir) if args.results_dir else None
        start_repl(
            model="gemini-2.5-flash",
            workers=args.workers,
            results_dir=results_dir,
        )
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
