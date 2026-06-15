"""헤드리스 운영 콘솔 (마인크래프트 서버 스타일).

stdin 을 읽는 별도 스레드에서 명령을 파싱하고, asyncio 작업은
run_coroutine_threadsafe 로 서버 루프에 위임한다.

명령:
  help            명령 목록
  list            접속자 목록
  boards          저장된 보드 목록
  say <msg>       전체 공지
  kick <user>     강제 퇴장
  op <user>       Operator(2) 승격
  deop <user>     Member(1) 강등
  adduser <u> <p> [level]   로컬 계정 추가
  whitelist add|remove|list [user]   화이트리스트 관리
  ban <user> / pardon <user> / banlist   밴 관리
  save            모든 보드 즉시 저장
  stop / quit     서버 종료
"""
from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from . import auth as auth_mod
from .board_store import list_boards

if TYPE_CHECKING:
    from .app import QonvoServer


class Console:
    """stdin 명령 루프."""

    def __init__(self, server: "QonvoServer", stop_event: asyncio.Event, loop: asyncio.AbstractEventLoop):
        self._server = server
        self._stop = stop_event
        self._loop = loop
        self._thread = threading.Thread(target=self._run, name="console", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        print(f"\n[{self._server.name}] console ready. Type 'help' for commands.\n")
        while not self._stop.is_set():
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                self._schedule_stop()
                break
            if not line:
                continue
            try:
                self._dispatch(line)
            except Exception as e:
                print(f"command error: {e}")

    # ---- 디스패치 -------------------------------------------------------
    def _dispatch(self, line: str) -> None:
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]

        if cmd in ("stop", "quit", "exit"):
            print("stopping...")
            self._schedule_stop()
        elif cmd == "help":
            print(__doc__)
        elif cmd in ("list", "players"):
            users = self._server.online_users()
            if not users:
                print("no players online")
            else:
                for name, lvl, board in users:
                    print(f"  {name:20} {lvl:10} board={board or '-'}")
                print(f"({len(users)} online)")
        elif cmd == "boards":
            bs = list_boards()
            print("  " + ("\n  ".join(bs) if bs else "(none)"))
        elif cmd == "say":
            if args:
                text = " ".join(args)
                self._run_coro(self._server.say(f"[Server] {text}"))
                print(f"[Server] {text}")
        elif cmd == "kick":
            if args:
                fut = self._run_coro(self._server.kick_user(args[0]))
                print("kicked" if fut.result(timeout=5) else "no such user online")
        elif cmd == "op":
            self._set_level(args, auth_mod.OPERATOR)
        elif cmd == "deop":
            self._set_level(args, auth_mod.MEMBER)
        elif cmd == "adduser":
            if len(args) >= 2:
                level = int(args[2]) if len(args) >= 3 else auth_mod.MEMBER
                auth_mod.add_user(args[0], args[1], level)
                print(f"user '{args[0]}' added (level={level})")
            else:
                print("usage: adduser <username> <password> [level]")
        elif cmd == "save":
            self._server.boards.save_all()
            print("all boards saved")
        elif cmd == "whitelist":
            self._cmd_whitelist(args)
        elif cmd == "ban":
            if args:
                auth_mod.ban_user(args[0])
                self._run_coro(self._server.kick_user(args[0]))
                print(f"banned: {args[0]}")
            else:
                print("usage: ban <user>")
        elif cmd == "pardon":
            if args:
                auth_mod.pardon_user(args[0])
                print(f"pardoned: {args[0]}")
            else:
                print("usage: pardon <user>")
        elif cmd == "banlist":
            bl = auth_mod.list_banned()
            print("  " + ("\n  ".join(bl) if bl else "(none)"))
        else:
            print(f"unknown command: {cmd} (try 'help')")

    def _cmd_whitelist(self, args) -> None:
        sub = args[0].lower() if args else "list"
        if sub == "add" and len(args) >= 2:
            auth_mod.add_whitelist(args[1]); print(f"whitelisted: {args[1]}")
        elif sub in ("remove", "rm") and len(args) >= 2:
            auth_mod.remove_whitelist(args[1]); print(f"removed: {args[1]}")
        elif sub == "list":
            wl = auth_mod.list_whitelist()
            print("  " + ("\n  ".join(wl) if wl else "(none)"))
        else:
            print("usage: whitelist add|remove|list [user]")

    def _set_level(self, args, level: int) -> None:
        if not args:
            print("usage: op|deop <user>")
            return
        username = args[0]
        # 접속 중 세션 즉시 반영
        sess = self._server.registry.find_user(username)
        if sess:
            sess.level = level
        # 로컬 계정이면 영속화
        from .auth import _save_users, _load_users
        data = _load_users()
        if username in data:
            data[username]["level"] = level
            _save_users(data)
        print(f"{username} -> level {level} ({auth_mod.LEVEL_NAMES.get(level)})")

    # ---- 루프 위임 ------------------------------------------------------
    def _run_coro(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _schedule_stop(self) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
