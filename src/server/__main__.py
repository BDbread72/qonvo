"""Qonvo 서버 진입점.

사용:
  python -m server run                  # 서버 실행 (헤드리스 콘솔)
  python -m server adduser <u> <p> [lvl]
  python -m server listusers
  python -m server config               # config.toml 경로 출력/생성

또는 src 디렉토리에서:  python src/server/__main__.py run

main.py 와 동일한 Windows/Py3.14 WMI 우회 + crash 로깅을 적용한다
(aiohttp 가 platform.win32_ver() 호출 시 WMI hang 방지 — CLAUDE.md 참조).
"""
import os
import sys

# src/ 를 import path 에 추가 (스크립트/패키지 양쪽 실행 지원)
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 한국어 콘솔(cp949)에서도 UTF-8 출력이 깨지지 않도록 재설정
for _stream in (sys.stdout, sys.stderr):
    try:
        # line_buffering=True: 파일/파이프로 출력해도 줄 단위로 즉시 flush
        # (백그라운드 실행 시 접속 배너가 server.log 에 바로 보이도록)
        _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

# --- Windows + Python 3.14 WMI hang 우회 (main.py 와 동일) ---
if sys.platform == "win32":
    import platform as _platform

    def _no_wmi(*_a, **_k):
        raise OSError("WMI disabled (qonvo bypass)")

    _platform._wmi_query = _no_wmi
    _wv = sys.getwindowsversion()
    _build = f"{_wv.major}.{_wv.minor}.{_wv.build}"
    _release = "11" if _wv.major == 10 and _wv.build >= 22000 else str(_wv.major)
    _platform.win32_ver = lambda: (_release, _build, "", "")

# --- crash 로깅 ---
import faulthandler
import threading
import traceback
from datetime import datetime

_log_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Qonvo", "logs")
os.makedirs(_log_dir, exist_ok=True)
_fault_log = open(os.path.join(_log_dir, "server_crash.log"), "a", encoding="utf-8")
faulthandler.enable(file=_fault_log)


def _log_exc(exc_type, exc_value, exc_tb):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _fault_log.write(f"\n[{ts}] UNHANDLED\n" + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    _fault_log.flush()


sys.excepthook = _log_exc
threading.excepthook = lambda a: _log_exc(a.exc_type, a.exc_value, a.exc_traceback)


def _cmd_run() -> int:
    import asyncio

    from server.config import load_config, ensure_config
    from server.app import QonvoServer
    from server.console import Console

    ensure_config()
    config = load_config()
    server = QonvoServer(config)

    async def main():
        import signal

        await server.start()
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()
        host = server.connect_host or "<this-server-ip>"
        print(f"\n{'='*50}")
        print(f"  {server.name} 가동 중")
        print(f"\n  접속: Connect 창의 Host 칸에 입력")
        print(f"\n      {host}")
        print(f"\n  Port: {server.port}  (기본값 그대로)")
        if not server.connect_host:
            print("  (공인 IP 자동감지 실패 — 도메인/IP를 config의 public_host에 지정)")
        print(f"{'='*50}")

        # SIGINT/SIGTERM 으로 깔끔하게 종료 (systemd/백그라운드용)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except (NotImplementedError, RuntimeError):
                pass  # Windows 등 미지원 환경

        # 대화형(tty)일 때만 운영 콘솔 기동. 백그라운드/서비스면 콘솔 생략
        # (stdin 이 /dev/null 이면 input() 이 EOF → 서버 즉시 종료되는 문제 방지)
        if sys.stdin and sys.stdin.isatty():
            Console(server, stop_event, loop).start()
        else:
            print("(non-interactive: console disabled; stop with SIGTERM/Ctrl+C)")

        try:
            await stop_event.wait()
        finally:
            await server.stop()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_adduser(argv) -> int:
    from server.auth import add_user, MEMBER
    if len(argv) < 2:
        print("usage: adduser <username> <password> [level]")
        return 2
    level = int(argv[2]) if len(argv) >= 3 else MEMBER
    add_user(argv[0], argv[1], level)
    print(f"user '{argv[0]}' added (level={level})")
    return 0


def _cmd_listusers() -> int:
    from server.auth import list_users, LEVEL_NAMES
    users = list_users()
    if not users:
        print("(no local users — use 'adduser')")
    for name, lvl in users.items():
        print(f"  {name:20} {LEVEL_NAMES.get(lvl, '?')} ({lvl})")
    return 0


def _cmd_config() -> int:
    from server.config import ensure_config
    print(ensure_config())
    return 0


def _cmd_access(cmd, rest) -> int:
    """whitelist/ban 관리 — 서버 실행 중에도 즉시 적용(auth가 파일을 매번 읽음)."""
    from server import auth as A
    if cmd == "whitelist":
        sub = rest[0].lower() if rest else "list"
        if sub == "add" and len(rest) >= 2:
            A.add_whitelist(rest[1]); print(f"whitelisted: {rest[1]}")
        elif sub in ("remove", "rm") and len(rest) >= 2:
            A.remove_whitelist(rest[1]); print(f"removed: {rest[1]}")
        else:
            wl = A.list_whitelist(); print("\n".join(f"  {u}" for u in wl) or "(none)")
    elif cmd == "ban":
        if rest:
            A.ban_user(rest[0]); print(f"banned: {rest[0]} (다음 접속/재접속부터 차단)")
        else:
            print("usage: ban <user>")
    elif cmd == "pardon":
        if rest:
            A.pardon_user(rest[0]); print(f"pardoned: {rest[0]}")
        else:
            print("usage: pardon <user>")
    elif cmd == "banlist":
        bl = A.list_banned(); print("\n".join(f"  {u}" for u in bl) or "(none)")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "run"
    rest = argv[1:]

    if cmd == "run":
        return _cmd_run()
    if cmd == "adduser":
        return _cmd_adduser(rest)
    if cmd == "listusers":
        return _cmd_listusers()
    if cmd == "config":
        return _cmd_config()
    if cmd in ("whitelist", "ban", "pardon", "banlist"):
        return _cmd_access(cmd, rest)
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
