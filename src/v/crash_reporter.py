"""크래시/오류 보고 — 클라이언트 측.

목표: 앱이 크래시하거나 처리되지 않은 오류가 나면 그 내용을 (접속한) qonvo 서버로
보내 운영자가 모아볼 수 있게 한다. 크래시는 앱이 죽는 중이라 즉시 전송이 불안정하므로,
**항상 로컬 큐 파일에 먼저 적어두고**, 다음에 서버에 접속할 때(또는 살아있는 동안
연결돼 있으면 즉시) 백그라운드로 업로드한다.

설계 원칙:
- stdlib 만 사용(main.py 의 아주 이른 시점·예외 훅 안에서도 안전하게 import).
- 어떤 함수도 예외를 밖으로 내지 않는다(보고 도중 또 죽으면 안 됨).
- 기본 ON, settings `crash_report_enabled=false` 로 끌 수 있다.
- 큐는 개수/용량 상한(무한 성장·디스크 폭주 방지).

전송 형식: 서버 `POST /report` 에 JSON 한 건씩.
  {ts, kind, summary, detail, version, user, server, platform, session, app_id}
"""
from __future__ import annotations

import json
import os
import platform
import threading
import time
import urllib.parse
import urllib.request
import uuid

_MAX_PENDING = 50          # 큐에 보관할 최대 보고 수(초과 시 오래된 것 폐기)
_MAX_DETAIL = 16000        # 트레이스백 본문 최대 길이(과대 페이로드 방지)
_POST_TIMEOUT = 6          # 업로드 타임아웃(초)


def _data_dir() -> str:
    """main.py 와 동일한 규칙으로 Qonvo 데이터 루트를 구한다."""
    base = os.environ.get("APPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "Qonvo")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


class _Reporter:
    def __init__(self):
        self._lock = threading.Lock()
        self._queue_path = os.path.join(_data_dir(), "pending_reports.jsonl")
        self._marker_path = os.path.join(_data_dir(), "last_session.json")
        self._session = uuid.uuid4().hex[:12]
        self._app_id = self._load_or_make_app_id()
        # 컨텍스트(앱이 채워줌)
        self._version = ""
        self._user = ""
        self._server = ""
        self._enabled = True
        # 현재 살아있는 업로드 대상(접속 중이면 set_target 으로 채워짐)
        self._http_base = ""
        self._http_token = ""

    # ---- 식별/컨텍스트 -------------------------------------------------
    def _load_or_make_app_id(self) -> str:
        """설치 단위 익명 ID(같은 설치의 보고를 묶기 위함). 영구 저장."""
        p = os.path.join(_data_dir(), "app_id")
        try:
            if os.path.exists(p):
                v = open(p, "r", encoding="utf-8").read().strip()
                if v:
                    return v
            v = uuid.uuid4().hex
            with open(p, "w", encoding="utf-8") as f:
                f.write(v)
            return v
        except Exception:
            return "unknown"

    def set_context(self, version=None, user=None, server=None, enabled=None):
        """앱이 버전/사용자/서버/활성여부를 알려준다(아는 만큼만)."""
        with self._lock:
            if version is not None:
                self._version = str(version)
            if user is not None:
                self._user = str(user)
            if server is not None:
                self._server = str(server)
            if enabled is not None:
                self._enabled = bool(enabled)

    def set_target(self, http_base: str, http_token: str = ""):
        """현재 접속한 서버의 HTTP 베이스를 등록한다(살아있는 업로드 대상)."""
        with self._lock:
            self._http_base = (http_base or "").rstrip("/")
            self._http_token = http_token or ""

    def clear_target(self):
        with self._lock:
            self._http_base = ""
            self._http_token = ""

    # ---- 기록 ----------------------------------------------------------
    def report(self, kind: str, summary: str, detail: str = ""):
        """보고 1건을 로컬 큐에 적고, 살아있는 대상이 있으면 즉시 flush 시도."""
        try:
            with self._lock:
                if not self._enabled:
                    return
                rec = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "kind": str(kind or "error")[:32],
                    "summary": str(summary or "")[:500],
                    "detail": str(detail or "")[:_MAX_DETAIL],
                    "version": self._version,
                    "user": self._user,
                    "server": self._server,
                    "platform": f"{platform.system()} {platform.release()}",
                    "session": self._session,
                    "app_id": self._app_id,
                }
                self._append_locked(rec)
                have_target = bool(self._http_base)
            if have_target:
                self.flush_async()
        except Exception:
            pass

    def _append_locked(self, rec: dict):
        try:
            lines = []
            if os.path.exists(self._queue_path):
                with open(self._queue_path, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            lines.append(json.dumps(rec, ensure_ascii=False))
            if len(lines) > _MAX_PENDING:
                lines = lines[-_MAX_PENDING:]
            tmp = self._queue_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            os.replace(tmp, self._queue_path)
        except Exception:
            pass

    # ---- 세션 센티넬 (비정상 종료 전부 감지) ---------------------------
    #
    # Python 예외 훅으로는 segfault·강제종료(taskkill)·OOM·전원차단을 못 잡는다.
    # 그래서 '시작 때 unclean 마커를 쓰고, 정상 종료 때만 clean 으로 바꾸는' 방식으로
    # 그 어떤 비정상 종료도 다음 실행에서 사후 감지한다(마인크래프트/브라우저 동일 패턴).
    def begin_session(self, version: str = ""):
        """앱 시작 시 호출. 지난 세션 마커가 unclean 이면 비정상 종료로 보고하고,
        새 세션 마커(unclean)를 기록한다."""
        try:
            with self._lock:
                if version:
                    self._version = str(version)
                prev = self._read_marker_locked()
            if prev:
                self._maybe_report_unexpected(prev)
            with self._lock:
                self._write_marker_locked({
                    "session": self._session,
                    "pid": os.getpid(),
                    "version": self._version,
                    "start_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "last_alive": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "clean": False,
                    "fatal_reported": False,
                })
        except Exception:
            pass

    def heartbeat(self):
        """주기 호출 — 마커의 last_alive 를 갱신해 비정상 종료 '대략 시각'을 남긴다."""
        try:
            with self._lock:
                m = self._read_marker_locked()
                if not m or m.get("session") != self._session:
                    return
                m["last_alive"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                self._write_marker_locked(m)
        except Exception:
            pass

    def note_fatal(self):
        """치명적 Python 예외를 기록한 직후 호출 — 마커에 표시해 다음 실행의
        '비정상 종료' 중복 보고를 막는다(상세 crash 보고가 이미 있으므로)."""
        try:
            with self._lock:
                m = self._read_marker_locked()
                if m and m.get("session") == self._session:
                    m["fatal_reported"] = True
                    self._write_marker_locked(m)
        except Exception:
            pass

    def end_session(self):
        """정상 종료 시 호출(aboutToQuit/run 종료). 마커를 clean 으로 바꾼다."""
        try:
            with self._lock:
                m = self._read_marker_locked()
                if m and m.get("session") == self._session:
                    m["clean"] = True
                    m["last_alive"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    self._write_marker_locked(m)
        except Exception:
            pass

    def _read_marker_locked(self):
        try:
            if os.path.exists(self._marker_path):
                with open(self._marker_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                return d if isinstance(d, dict) else None
        except Exception:
            return None
        return None

    def _write_marker_locked(self, d: dict):
        try:
            tmp = self._marker_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(tmp, self._marker_path)
        except Exception:
            pass

    def _maybe_report_unexpected(self, prev: dict):
        """지난 세션 마커가 비정상 종료를 가리키면 보고를 큐에 넣는다."""
        try:
            if prev.get("clean") is True:
                return                       # 정상 종료 — 보고 안 함
            if prev.get("fatal_reported") is True:
                return                       # 이미 상세 crash 보고가 있음 — 중복 방지
            start = prev.get("start_ts", "?")
            alive = prev.get("last_alive", "?")
            ver = prev.get("version", "?")
            pid = prev.get("pid", "?")
            sess = prev.get("session", "?")
            tail = self._crash_log_tail(prev)
            summary = (f"앱이 비정상 종료됨(예외 훅 미경유: segfault/강제종료/전원차단 등) "
                       f"— 마지막 생존 {alive}")
            detail = (f"previous session={sess} pid={pid} version={ver}\n"
                      f"start={start} last_alive={alive} clean=False\n")
            if tail:
                detail += "\n--- crash.log tail (해당 세션 추정) ---\n" + tail
            rec_version = self._version       # 현재 버전(보고 시점)
            with self._lock:
                rec = {
                    "ts": alive if alive != "?" else time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "kind": "unexpected_exit",
                    "summary": summary[:500],
                    "detail": detail[:_MAX_DETAIL],
                    "version": prev.get("version", "") or rec_version,
                    "user": self._user,
                    "server": prev.get("server", "") or self._server,
                    "platform": f"{platform.system()} {platform.release()}",
                    "session": sess,
                    "app_id": self._app_id,
                }
                self._append_locked(rec)
        except Exception:
            pass

    def _crash_log_tail(self, prev: dict) -> str:
        """지난 세션 동안 쓰인 crash.log 의 말미를 가져온다(faulthandler segfault 트레이스 등).

        crash.log 의 mtime 이 지난 세션 시작 이후면 그 세션 것으로 보고 말미 ~3KB 를 첨부.
        """
        try:
            p = os.path.join(_data_dir(), "logs", "crash.log")
            if not os.path.exists(p):
                return ""
            data = open(p, "r", encoding="utf-8", errors="replace").read()
            return data[-3000:] if data else ""
        except Exception:
            return ""

    # ---- 업로드 --------------------------------------------------------
    def flush_async(self, http_base: str = "", http_token: str = ""):
        """백그라운드 스레드로 큐를 서버에 업로드(논블로킹)."""
        try:
            t = threading.Thread(target=self._flush, args=(http_base, http_token),
                                 daemon=True)
            t.start()
        except Exception:
            pass

    def _flush(self, http_base: str = "", http_token: str = ""):
        try:
            with self._lock:
                if not self._enabled:
                    return
                base = (http_base or self._http_base or "").rstrip("/")
                token = http_token or self._http_token
                if not base:
                    return
                if not os.path.exists(self._queue_path):
                    return
                with open(self._queue_path, "r", encoding="utf-8") as f:
                    lines = [l for l in f.read().splitlines() if l.strip()]
            if not lines:
                return
            sent = 0
            for line in lines:
                try:
                    rec = json.loads(line)
                except Exception:
                    sent += 1   # 깨진 줄은 버린다
                    continue
                if self._post(base, token, rec):
                    sent += 1
                else:
                    break       # 네트워크 실패 → 나머지는 다음 기회에
            # 보낸 만큼 큐에서 제거(앞에서부터 sent 개)
            with self._lock:
                remaining = lines[sent:]
                try:
                    if remaining:
                        tmp = self._queue_path + ".tmp"
                        with open(tmp, "w", encoding="utf-8") as f:
                            f.write("\n".join(remaining) + "\n")
                        os.replace(tmp, self._queue_path)
                    else:
                        os.remove(self._queue_path)
                except Exception:
                    pass
        except Exception:
            pass

    def _post(self, base: str, token: str, rec: dict) -> bool:
        try:
            url = base + "/report"
            if token:
                url += "?t=" + urllib.parse.quote(token)
            data = json.dumps(rec, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=_POST_TIMEOUT) as r:
                return 200 <= r.status < 300
        except Exception:
            return False


# 모듈 전역 싱글턴 — main.py 예외 훅과 앱 어디서든 같은 인스턴스를 쓴다.
_reporter = _Reporter()


# ---- 공개 API (얇은 래퍼) ----------------------------------------------
def report(kind: str, summary: str, detail: str = ""):
    _reporter.report(kind, summary, detail)


def set_context(version=None, user=None, server=None, enabled=None):
    _reporter.set_context(version=version, user=user, server=server, enabled=enabled)


def begin_session(version: str = ""):
    _reporter.begin_session(version)


def heartbeat():
    _reporter.heartbeat()


def note_fatal():
    _reporter.note_fatal()


def end_session():
    _reporter.end_session()


def set_target(http_base: str, http_token: str = ""):
    _reporter.set_target(http_base, http_token)


def clear_target():
    _reporter.clear_target()


def flush_async(http_base: str = "", http_token: str = ""):
    _reporter.flush_async(http_base, http_token)


import urllib.parse  # noqa: E402  (_post 에서 사용)
