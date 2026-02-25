"""prompt_toolkit 기반 인터랙티브 REPL. 상단 상태 테이블 + 하단 입력창 split 레이아웃."""

import re
import sys
import threading
import time
from io import StringIO
from pathlib import Path
from typing import List, Optional

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.processors import BeforeInput

from rich.console import Console
from rich.table import Table


STATUS_ICONS = {
    "QUEUED": "\u25cc",
    "RUNNING": "\u25cf",
    "DONE": "\u2713",
    "ERROR": "\u2717",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# @path 패턴: 공백 뒤 또는 줄 시작의 @, 따옴표 경로 또는 공백 없는 경로
_AT_PATTERN = re.compile(r'(?:^|\s)@(?:"([^"]+)"|(\S+))')


def _parse_inline_attachments(text: str) -> tuple:
    """프롬프트에서 @path 토큰을 추출하고, 실존 이미지만 첨부로 분리한다.
    Returns: (cleaned_prompt, list_of_paths)"""
    found = []
    to_remove = []

    for m in _AT_PATTERN.finditer(text):  # @토큰 추출
        raw_path = m.group(1) or m.group(2)  # 따옴표/일반 경로
        p = Path(raw_path)
        if not p.is_absolute():
            p = Path.cwd() / p  # 상대경로 보정
        if p.exists() and p.suffix.lower() in IMAGE_EXTENSIONS:  # 실존 이미지 확인
            found.append(str(p))
            to_remove.append(m.group(0))  # 토큰 제거 대상

    cleaned = text
    for token in to_remove:
        cleaned = cleaned.replace(token, "", 1)
    cleaned = cleaned.strip()

    return cleaned, found


def _merge_attachments(inline: list, session: list) -> list:
    """인라인(@)과 세션(/attach) 첨부를 합산하고 중복을 제거한다."""
    seen = set()  # 중복 추적
    result = []
    for p in inline + session:  # inline+session 합산
        norm = str(Path(p).resolve())  # 절대경로 정규화
        if norm not in seen:  # 중복 제거
            seen.add(norm)
            result.append(p)
    return result


def _render_table(jobs, default_model, system_prompt, max_workers) -> str:
    """rich.Table을 ANSI 문자열로 렌더링하여 반환한다."""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=100,
        no_color=False,
    )

    # 상태 테이블 구성
    table = Table(
        show_header=True,
        header_style="bold cyan",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Status", width=12)
    table.add_column("Model", width=24)
    table.add_column("Preview", ratio=1)

    for j in jobs:
        icon = STATUS_ICONS.get(j.status, "?")

        # 상태별 표시 문자열 생성
        if j.status == "DONE" and j.finished_at and j.started_at:
            elapsed = f"{j.finished_at - j.started_at:.1f}s"
            status_str = f"[green]{icon} DONE {elapsed}[/green]"
        elif j.status == "RUNNING":
            elapsed = f"{time.time() - (j.started_at or j.created_at):.0f}s"
            status_str = f"[yellow]{icon} RUN {elapsed}[/yellow]"
        elif j.status == "ERROR":
            status_str = f"[red]{icon} ERROR[/red]"
        else:
            status_str = f"[dim]{icon} QUEUED[/dim]"

        # 미리보기를 1줄 요약 + 길이 제한
        preview = j.preview.replace("\n", " | ")
        if len(preview) > 60:
            preview = preview[:57] + "..."

        table.add_row(str(j.id), status_str, j.model, preview)

    if not jobs:
        table.add_row("-", "[dim]no jobs[/dim]", "-", "-")

    console.print(table)

    # 하단 정보 라인 렌더링
    info_parts = [f"[dim]model:[/dim] {default_model}"]
    info_parts.append(f"[dim]workers:[/dim] {max_workers}")
    if system_prompt:
        sp_short = system_prompt[:30] + "..." if len(system_prompt) > 30 else system_prompt
        info_parts.append(f"[dim]system:[/dim] {sp_short}")
    console.print("  ".join(info_parts))

    return buf.getvalue()


COMMANDS = [
    "/model", "/models", "/workers", "/show", "/status",
    "/system", "/attach", "/repeat", "/bulk", "/clear", "/help", "/quit",
]

HELP_TEXT = """
[Commands]
  /model <id>       Change default model
  /models           List available models
  /workers <N>      Set concurrent workers
  /show <job_id>    Show full result of a job
  /status           Detailed status of all jobs
  /system <text>    Set system prompt (empty = clear)
  /attach <path>    Add image to session attachments
  /attach           Show current attachments
  /attach clear     Clear all attachments
  /attach rm <N>    Remove attachment by index
  /repeat N <text>  Submit same prompt N times (1-50)
  /bulk <file>      Submit each line of file as a prompt
  /clear            Remove completed jobs from table
  /help             Show this help
  /quit             Exit REPL

[Image Attachments]
  @path/to/img.png  Attach image to current prompt only
  @"path with spaces/img.png"  Quoted path
  /attach img.png   Add to session (persists across prompts)
"""


class QonvoCompleter(Completer):
    """/명령어 및 /model 뒤 모델 ID 자동완성을 제공한다."""

    def __init__(self, get_model_ids):
        """모델 ID 목록을 제공하는 콜백을 설정한다."""
        self._get_model_ids = get_model_ids

    def get_completions(self, document, complete_event):
        """현재 입력에 맞는 Completion을 생성한다."""
        text = document.text_before_cursor
        if text.startswith("/"):
            # 슬래시 명령어 자동완성
            if " " not in text:
                word = text
                for cmd in COMMANDS:
                    if cmd.startswith(word):
                        yield Completion(cmd, start_position=-len(word))
            # /model 뒤에 모델 ID 자동완성
            elif text.startswith("/model "):
                partial = text[len("/model "):]
                for mid in self._get_model_ids():
                    if mid.startswith(partial):
                        yield Completion(mid, start_position=-len(partial))


def start_repl(
    model: str = "gemini-2.5-flash",
    workers: int = 4,
    results_dir: Optional[Path] = None,
):
    """prompt_toolkit 기반 인터랙티브 REPL을 시작한다."""
    from cli_job_manager import JobManager

    router = _init_provider_for_repl()  # 프로바이더 초기화
    mgr = JobManager(
        router=router,
        default_model=model,
        max_workers=workers,
        results_dir=results_dir,
    )

    all_model_ids_cache = []

    def get_model_ids():
        """모델 ID 목록을 캐싱하여 반환한다."""
        nonlocal all_model_ids_cache
        if not all_model_ids_cache:
            try:
                from v.model_plugin import get_all_model_ids
                all_model_ids_cache = get_all_model_ids()
            except Exception:
                all_model_ids_cache = []
        return all_model_ids_cache

    history_path = _get_history_path()
    history = FileHistory(str(history_path))

    output_lines = []  # 출력 패널에 표시할 최근 메시지
    session_attachments = []  # /attach로 설정한 세션 첨부 목록

    def get_table_text():
        """현재 작업 스냅샷을 ANSI 테이블 문자열로 생성."""
        jobs = mgr.get_status_snapshot()
        ansi_str = _render_table(jobs, mgr.default_model, mgr.system_prompt, mgr.max_workers)
        return ANSI(ansi_str)

    kb = KeyBindings()

    input_buffer = Buffer(
        name="input",
        completer=QonvoCompleter(get_model_ids),
        history=history,
        multiline=False,
    )

    output_buffer = Buffer(name="output", read_only=True)

    def _update_output(text: str):
        """출력 버퍼 내용을 교체한다."""
        output_buffer.set_document(
            output_buffer.document.__class__(text),
            bypass_readonly=True,
        )

    @kb.add("enter", filter=True)
    def on_enter(event):
        """입력 확인 시 명령/프롬프트를 처리한다."""
        text = input_buffer.text.strip()
        input_buffer.reset()
        if not text:
            return
        input_buffer.history.append_string(text)
        _handle_input(text, mgr, get_model_ids, output_lines, _update_output, session_attachments)
        if app._is_running:
            app.invalidate()

    @kb.add("c-c")
    def on_ctrl_c(event):
        """Ctrl+C로 종료 처리."""
        _handle_quit(mgr, event)

    @kb.add("c-d")
    def on_ctrl_d(event):
        """Ctrl+D로 종료 처리."""
        _handle_quit(mgr, event)

    table_window = Window(
        content=FormattedTextControl(get_table_text),
        height=15,
    )

    output_window = Window(
        content=BufferControl(buffer=output_buffer),
        height=6,
        wrap_lines=True,
    )

    separator = Window(height=1, char="\u2500", style="class:separator")
    separator2 = Window(height=1, char="\u2500", style="class:separator")

    input_window = Window(
        content=BufferControl(
            buffer=input_buffer,
            input_processors=[BeforeInput("qonvo> ")],
        ),
        height=1,
    )

    # 테이블 + 출력 + 입력의 수직 분할 레이아웃
    layout = Layout(
        HSplit([
            table_window,
            separator,
            output_window,
            separator2,
            input_window,
        ]),
        focused_element=input_window,
    )

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        mouse_support=False,
    )

    stop_event = threading.Event()

    def refresh_loop():
        """0.5초마다 화면을 갱신한다."""
        while not stop_event.is_set():
            try:
                if app._is_running:
                    app.invalidate()  # 테이블/출력 갱신 트리거
            except Exception:
                pass
            stop_event.wait(0.5)

    refresh_thread = threading.Thread(target=refresh_loop, daemon=True)
    refresh_thread.start()

    try:
        sys.stdout.write("Qonvo REPL starting... (type /help for commands)\n")
        sys.stdout.flush()
        app.run()
    finally:
        stop_event.set()
        mgr.shutdown()


def _handle_input(text, mgr, get_model_ids, output_lines, update_output, session_attachments=None):
    """슬래시 명령어 또는 프롬프트를 처리한다."""
    if session_attachments is None:
        session_attachments = []
    if text.startswith("/"):
        # 슬래시 명령어 파싱
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # 명령어 분기 처리
        if cmd == "/quit":
            running = [
                j for j in mgr.get_status_snapshot()
                if j.status == "RUNNING"
            ]
            if running:
                output_lines.clear()
                output_lines.append(f"{len(running)} job(s) still running. Use Ctrl+C to force quit.")
                update_output("\n".join(output_lines))
            else:
                mgr.shutdown()
                raise SystemExit(0)

        elif cmd == "/model":
            if not arg:
                output_lines.clear()
                output_lines.append(f"Current model: {mgr.default_model}")
                update_output("\n".join(output_lines))
            else:
                available = get_model_ids()
                if available and arg not in available:
                    output_lines.clear()
                    output_lines.append(f"Unknown model: {arg}")
                    output_lines.append(f"Use /models to see available models")
                    update_output("\n".join(output_lines))
                else:
                    mgr.default_model = arg
                    output_lines.clear()
                    output_lines.append(f"Model set to: {arg}")
                    update_output("\n".join(output_lines))

        elif cmd == "/models":
            try:
                from v.model_plugin import get_all_models
                models = get_all_models()
                output_lines.clear()
                output_lines.append(f"Available models ({len(models)}):")
                for mid, name in models.items():
                    marker = " *" if mid == mgr.default_model else ""
                    output_lines.append(f"  {mid:<40} {name}{marker}")
                update_output("\n".join(output_lines))
            except Exception as e:
                output_lines.clear()
                output_lines.append(f"Error listing models: {e}")
                update_output("\n".join(output_lines))

        elif cmd == "/workers":
            if not arg:
                output_lines.clear()
                output_lines.append(f"Current workers: {mgr.max_workers}")
                update_output("\n".join(output_lines))
            else:
                try:
                    n = int(arg)
                    if n < 1 or n > 16:
                        raise ValueError("Must be 1-16")
                    mgr._max_workers = n
                    # ThreadPoolExecutor 내부 최대 워커 수도 함께 갱신
                    mgr._executor._max_workers = n
                    output_lines.clear()
                    output_lines.append(f"Workers set to: {n}")
                    update_output("\n".join(output_lines))
                except ValueError as e:
                    output_lines.clear()
                    output_lines.append(f"Invalid: {e}")
                    update_output("\n".join(output_lines))

        elif cmd == "/show":
            if not arg:
                output_lines.clear()
                output_lines.append("Usage: /show <job_id>")
                update_output("\n".join(output_lines))
            else:
                try:
                    jid = int(arg)
                    job = mgr.get_job(jid)
                    if not job:
                        output_lines.clear()
                        output_lines.append(f"Job #{jid} not found")
                        update_output("\n".join(output_lines))
                    else:
                        # 전체 결과 텍스트를 출력 패널에 표시
                        output_lines.clear()
                        output_lines.append(f"=== Job #{jid} ({job.model}) [{job.status}] ===")
                        for line in job.full_text.split("\n"):
                            output_lines.append(line)
                        if job.result_file:
                            output_lines.append(f"\nSaved: {job.result_file}")
                        update_output("\n".join(output_lines))
                except ValueError:
                    output_lines.clear()
                    output_lines.append("Usage: /show <number>")
                    update_output("\n".join(output_lines))

        elif cmd == "/status":
            jobs = mgr.get_status_snapshot()
            output_lines.clear()
            if not jobs:
                output_lines.append("No jobs.")
            else:
                # 각 작업 상세정보 출력
                for j in jobs:
                    elapsed = ""
                    if j.finished_at and j.started_at:
                        elapsed = f" ({j.finished_at - j.started_at:.1f}s)"
                    elif j.started_at:
                        elapsed = f" ({time.time() - j.started_at:.0f}s)"
                    tokens = j.meta.get("prompt_tokens", 0) + j.meta.get("candidates_tokens", 0)
                    tok_str = f" tok:{tokens}" if tokens else ""
                    err_str = f" err:{j.error}" if j.error else ""
                    output_lines.append(
                        f"  #{j.id} [{j.status}]{elapsed} {j.model}{tok_str}{err_str}"
                    )
                    if j.result_file:
                        output_lines.append(f"       -> {j.result_file}")
            update_output("\n".join(output_lines))

        elif cmd == "/system":
            if not arg:
                mgr.system_prompt = None
                output_lines.clear()
                output_lines.append("System prompt cleared.")
                update_output("\n".join(output_lines))
            else:
                mgr.system_prompt = arg
                output_lines.clear()
                output_lines.append(f"System prompt set: {arg[:60]}...")
                update_output("\n".join(output_lines))

        elif cmd == "/attach":
            if not arg:
                # 현재 세션 첨부 목록 표시
                output_lines.clear()
                if not session_attachments:
                    output_lines.append("No session attachments.")
                else:
                    output_lines.append(f"Session attachments ({len(session_attachments)}):")
                    for i, p in enumerate(session_attachments, 1):
                        output_lines.append(f"  [{i}] {p}")
                update_output("\n".join(output_lines))
            elif arg.strip().lower() == "clear":
                session_attachments.clear()
                output_lines.clear()
                output_lines.append("All session attachments cleared.")
                update_output("\n".join(output_lines))
            elif arg.strip().lower().startswith("rm "):
                # /attach rm <index>
                try:
                    idx = int(arg.strip().split()[1]) - 1
                    if 0 <= idx < len(session_attachments):
                        removed = session_attachments.pop(idx)
                        output_lines.clear()
                        output_lines.append(f"Removed: {removed}")
                    else:
                        output_lines.clear()
                        output_lines.append(f"Invalid index. Use /attach to see list.")
                    update_output("\n".join(output_lines))
                except (ValueError, IndexError):
                    output_lines.clear()
                    output_lines.append("Usage: /attach rm <number>")
                    update_output("\n".join(output_lines))
            else:
                # /attach <path> — 세션 첨부 추가
                filepath = Path(arg.strip())
                if not filepath.is_absolute():
                    filepath = Path.cwd() / filepath
                if not filepath.exists():
                    output_lines.clear()
                    output_lines.append(f"File not found: {filepath}")
                    update_output("\n".join(output_lines))
                elif filepath.suffix.lower() not in IMAGE_EXTENSIONS:
                    output_lines.clear()
                    output_lines.append(
                        f"Unsupported format: {filepath.suffix}. "
                        f"Supported: {', '.join(sorted(IMAGE_EXTENSIONS))}"
                    )
                    update_output("\n".join(output_lines))
                else:
                    norm = str(filepath)
                    if norm not in session_attachments:
                        session_attachments.append(norm)
                    output_lines.clear()
                    output_lines.append(f"Attached: {filepath.name} ({len(session_attachments)} total)")
                    update_output("\n".join(output_lines))

        elif cmd == "/repeat":
            repeat_parts = arg.split(None, 1)
            if len(repeat_parts) < 2:
                output_lines.clear()
                output_lines.append("Usage: /repeat N <prompt>")
                update_output("\n".join(output_lines))
            else:
                try:
                    n = int(repeat_parts[0])  # 반복 횟수 파싱
                    if n < 1 or n > 50:
                        raise ValueError("N must be 1-50")
                    raw_prompt = repeat_parts[1]  # 프롬프트 추출
                    cleaned, inline_files = _parse_inline_attachments(raw_prompt)
                    all_files = _merge_attachments(inline_files, session_attachments)
                    prompt = cleaned if cleaned else raw_prompt
                    ids = []
                    for _ in range(n):  # n회 제출
                        ids.append(mgr.submit(prompt=prompt, attachments=all_files or None))
                    output_lines.clear()
                    output_lines.append(f"Submitted {n} jobs: #{ids[0]}-#{ids[-1]} ({mgr.default_model})")
                    update_output("\n".join(output_lines))
                except ValueError as e:
                    output_lines.clear()
                    output_lines.append(f"Invalid: {e}")
                    update_output("\n".join(output_lines))

        elif cmd == "/bulk":
            # /bulk <file.txt> — 파일의 각 줄을 프롬프트로 제출
            if not arg:
                output_lines.clear()
                output_lines.append("Usage: /bulk <file_path>")
                update_output("\n".join(output_lines))
            else:
                try:
                    filepath = Path(arg.strip())  # 경로 정리
                    if not filepath.is_absolute():
                        filepath = Path.cwd() / filepath  # 상대 경로를 절대 경로로 변환
                    if not filepath.exists():
                        output_lines.clear()
                        output_lines.append(f"File not found: {filepath}")
                        update_output("\n".join(output_lines))
                    else:
                        lines = filepath.read_text(encoding="utf-8").splitlines()  # 파일 읽기
                        prompts = [  # 유효 프롬프트만 필터 (빈줄, #주석 스킵)
                            ln.strip() for ln in lines
                            if ln.strip() and not ln.strip().startswith("#")
                        ]
                        if not prompts:
                            output_lines.clear()
                            output_lines.append("No prompts found in file (empty or all comments).")
                            update_output("\n".join(output_lines))
                        else:
                            ids = []
                            for p in prompts:
                                # 각 줄별 @ 파싱 + 세션 첨부 합산
                                cleaned, inline_files = _parse_inline_attachments(p)
                                all_files = _merge_attachments(inline_files, session_attachments)
                                ids.append(mgr.submit(
                                    prompt=cleaned if cleaned else p,
                                    attachments=all_files or None,
                                ))
                            output_lines.clear()
                            output_lines.append(
                                f"Submitted {len(ids)} jobs from {filepath.name}: "
                                f"#{ids[0]}-#{ids[-1]} ({mgr.default_model})"
                            )
                            update_output("\n".join(output_lines))
                except Exception as e:
                    output_lines.clear()
                    output_lines.append(f"Error reading file: {e}")
                    update_output("\n".join(output_lines))

        elif cmd == "/clear":
            removed = mgr.clear_done()
            output_lines.clear()
            output_lines.append(f"Cleared {removed} completed job(s).")
            update_output("\n".join(output_lines))

        elif cmd == "/help":
            output_lines.clear()
            for line in HELP_TEXT.strip().split("\n"):
                output_lines.append(line)
            update_output("\n".join(output_lines))

        else:
            output_lines.clear()
            output_lines.append(f"Unknown command: {cmd}. Type /help for commands.")
            update_output("\n".join(output_lines))
    else:
        # 인라인 @ 첨부 파싱 + 세션 첨부 합산
        cleaned, inline_files = _parse_inline_attachments(text)
        all_files = _merge_attachments(inline_files, session_attachments)
        prompt = cleaned if cleaned else text
        job_id = mgr.submit(prompt=prompt, attachments=all_files or None)
        output_lines.clear()
        attach_info = f" +{len(all_files)} img" if all_files else ""
        output_lines.append(f"Job #{job_id} submitted ({mgr.default_model}){attach_info}")
        update_output("\n".join(output_lines))


def _handle_quit(mgr, event):
    """종료 시 작업 상태에 따라 적절한 종료를 수행한다."""
    running = [j for j in mgr.get_status_snapshot() if j.status == "RUNNING"]
    if running:
        mgr.shutdown(timeout=3.0)
    else:
        mgr.shutdown(timeout=1.0)
    event.app.exit()


def _init_provider_for_repl():
    """REPL에서 사용할 프로바이더 라우터를 초기화한다."""
    from v.settings import get_api_keys
    from v.provider import GeminiProvider
    from v.model_plugin import PluginRegistry, ProviderRouter

    keys = get_api_keys()
    gemini = GeminiProvider(api_keys=keys) if keys else None

    registry = PluginRegistry.instance()
    registry.load_all()

    return ProviderRouter(gemini_provider=gemini)


def _get_history_path() -> Path:
    """REPL 입력 히스토리 파일 경로를 반환한다."""
    from v.settings import get_app_data_path
    path = get_app_data_path() / "cli_history.txt"
    return path
