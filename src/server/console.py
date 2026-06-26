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
  usage           오늘 AI 사용량(유저별 토큰/요청/동시) + 레벨별 한도
  plugins         로드된 모델 플러그인(모델 수·키 수)
  reports [N]     클라가 보낸 최근 크래시/오류 보고 N건(기본 20)
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
        elif cmd == "usage":
            self._cmd_usage()
        elif cmd == "plugins":
            self._cmd_plugins()
        elif cmd == "reports":
            self._cmd_reports(args)
        else:
            print(f"unknown command: {cmd} (try 'help')")

    def _cmd_reports(self, args) -> None:
        """클라가 보낸 최근 크래시/오류 보고를 출력한다. usage: reports [N=20]"""
        try:
            n = int(args[0]) if args else 20
        except Exception:
            n = 20
        import json as _json
        from .config import get_server_dir
        path = get_server_dir() / "reports" / "reports.jsonl"
        if not path.exists():
            print("(no reports)")
            return
        try:
            lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except Exception as e:
            print(f"read error: {e}")
            return
        recent = lines[-n:]
        print(f"--- 최근 {len(recent)}건 / 총 {len(lines)}건 ---")
        for l in recent:
            try:
                r = _json.loads(l)
            except Exception:
                continue
            print(f"  [{r.get('recv_ts','')}] {r.get('kind',''):10} "
                  f"v{r.get('version','?')} user={r.get('user') or '-'} "
                  f"board={r.get('server') or '-'}")
            print(f"      {r.get('summary','')}")
        print(f"(상세 트레이스백: {path})")

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

    def _cmd_usage(self) -> None:
        """오늘 AI 사용량(유저별) + 레벨별 한도를 출력한다."""
        policy = getattr(self._server, "policy", None)
        if policy is None:
            print("(정책 비활성)")
            return
        rows = policy.snapshot()
        if not rows:
            print("오늘 AI 사용 없음")
        else:
            print(f"  {'user':18} {'req':>5} {'tok in':>10} {'tok out':>10} {'live':>5}  top model")
            for r in rows:
                print(f"  {r['user']:18} {r['req']:>5} {r['tokens_in']:>10,} "
                      f"{r['tokens_out']:>10,} {r['active']:>5}  {r['top_model']}")
        lim = policy.limits_view()
        print("  한도(0=무제한):")
        for lvl in ("member", "operator"):
            d = lim.get(lvl, {})
            models = d.get("models") or ["*"]
            mstr = "전체" if "*" in models else ",".join(models)
            print(f"    {lvl:9} models={mstr} image={'O' if d.get('allow_image', True) else 'X'} "
                  f"rate/min={d.get('rate_per_min', 0)} conc={d.get('concurrent', 0)} "
                  f"daily_tok={d.get('daily_tokens', 0)} max_count={d.get('max_count', 8)}")

    def _cmd_plugins(self) -> None:
        """로드된 모델 플러그인과 모델/키 수를 출력한다(키 값은 노출 안 함)."""
        try:
            from v.model_plugin import PluginRegistry
            reg = PluginRegistry.instance()
        except Exception as e:
            print(f"플러그인 레지스트리 접근 실패: {e}")
            return
        discovered = reg.get_discovered_plugins()
        if not discovered:
            print("발견된 플러그인 없음")
            return
        for p in discovered:
            loaded = p.get("enabled")
            inst = reg._plugins.get(p["id"]) if loaded else None
            keys = len(getattr(inst, "_api_keys", []) or []) if inst else 0
            mark = "●" if loaded else "○"
            print(f"  {mark} {p['id']:18} {p['name']:12} models={len(p.get('models', {}))} "
                  f"keys={keys} v{p.get('version', '?')}")
        print("  (● 활성/키 주입됨, ○ 발견만 됨)")

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
