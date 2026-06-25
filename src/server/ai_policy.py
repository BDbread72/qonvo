"""AI 실행 거버넌스 — 레벨별 모델 권한 + 레이트/동시/쿼타 제한 + 사용량 회계.

서버가 운영자의 API 키로 AI 를 대행하므로(서버모드), 접속자(Member 이상)가 무엇을
얼마나 돌릴 수 있는지를 레벨별로 통제한다. Visitor 는 상위(app.py)에서 이미 AI 거부.

- authorize(): 모델 허용 / 이미지 게이트 / preferred count 상한 / 레이트(분당) /
  동시 실행 / 1일 토큰 쿼타를 검사하고, 통과 시 count 를 레벨 상한으로 클램프해 돌려준다.
- begin()/end(): 동시 실행 카운트 증감 + 완료 시 토큰/요청을 유저별로 누적(감사).
- 1일 카운터는 usage_state.json 에 영속(서버 재시작에도 쿼타 유지). 감사 로그는
  usage.jsonl 에 요청당 1줄 append.

기본값은 전부 무제한(0)이라 설정 전엔 기존 동작 그대로다(하위호환). 운영자가
config.toml [ai_policy.member]/[ai_policy.operator] 에서 knob 을 켠다.

스레드: authorize/begin/end 는 asyncio 루프 스레드에서, snapshot/limits_view 는
콘솔 스레드에서 호출되므로 _lock 으로 보호한다.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Optional, Tuple

# 레벨(auth.MEMBER=1 / OPERATOR=2) → 정책 키. Visitor(0)는 AI 자체가 막혀 여기 안 옴.
_LEVEL_KEY = {1: "member", 2: "operator"}

_DEFAULT_LIMIT = {
    "models": ["*"],      # 허용 모델 id 목록. ["*"] = 전체.
    "allow_image": True,  # 이미지 생성 모델 허용 여부(비용 큼)
    "rate_per_min": 0,    # 분당 최대 요청수 (0=무제한)
    "concurrent": 0,      # 동시 실행 상한 (0=무제한)
    "daily_tokens": 0,    # 1일 토큰(in+out) 상한 (0=무제한)
    "max_count": 8,       # preferred 후보 동시생성 상한(요청당 비용배수)
}


def _today() -> str:
    t = time.gmtime()
    return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"


class AIPolicy:
    def __init__(self, config: dict, state_dir: Optional[Path] = None):
        self._lock = threading.Lock()
        self._limits: Dict[str, dict] = {}
        self.configure(config)

        if state_dir is None:
            from .config import get_server_dir
            state_dir = get_server_dir()
        self._dir = Path(state_dir)
        self._state_path = self._dir / "usage_state.json"
        self._audit_path = self._dir / "usage.jsonl"

        # 런타임 상태(전부 _lock 보호)
        self._recent: Dict[str, deque] = defaultdict(deque)  # user -> 최근 요청 timestamps(레이트)
        self._active: Dict[str, int] = defaultdict(int)      # user -> 동시 실행 수
        self._daily: Dict[str, dict] = {}                    # user -> {date,in,out,req,models:{}}
        self._load_state()

    def configure(self, config: dict) -> None:
        """config 의 [ai_policy] 로 레벨별 한도를 (재)구성한다."""
        ap = config.get("ai_policy") or {}
        lim: Dict[str, dict] = {}
        for key in ("member", "operator"):
            d = dict(_DEFAULT_LIMIT)
            d.update(ap.get(key) or {})
            lim[key] = d
        with self._lock:
            self._limits = lim

    def _limit_for(self, level: int) -> dict:
        return self._limits.get(_LEVEL_KEY.get(level, "member"), self._limits["member"])

    # ---- 인가 ----------------------------------------------------------
    def authorize(self, user: str, level: int, model: str, count: int,
                  is_image: bool) -> Tuple[bool, str, int]:
        """AI 요청 허용 여부 + (거부 사유) + 레벨 상한으로 클램프된 count.

        통과해도 begin() 을 호출해야 동시 실행/레이트가 실제로 카운트된다.
        """
        lim = self._limit_for(level)
        models = lim.get("models") or ["*"]
        if "*" not in models and model not in models:
            return False, f"이 서버에서 '{model}' 모델은 현재 권한으로 사용할 수 없습니다.", count
        if is_image and not lim.get("allow_image", True):
            return False, "이미지 생성 모델은 이 서버에서 상위 권한만 사용할 수 있습니다.", count

        maxc = int(lim.get("max_count", 8) or 8)
        count = max(1, min(int(count or 1), maxc))

        with self._lock:
            now = time.time()
            rpm = int(lim.get("rate_per_min", 0) or 0)
            if rpm > 0:
                dq = self._recent[user]
                while dq and now - dq[0] > 60:
                    dq.popleft()
                if len(dq) >= rpm:
                    return False, f"요청이 너무 잦습니다(분당 {rpm}회 한도). 잠시 후 다시 시도하세요.", count
            conc = int(lim.get("concurrent", 0) or 0)
            if conc > 0 and self._active.get(user, 0) >= conc:
                return False, f"동시 AI 실행 한도({conc})를 초과했습니다. 진행 중 작업이 끝나면 다시.", count
            dt = int(lim.get("daily_tokens", 0) or 0)
            if dt > 0:
                rec = self._daily_rec(user)
                if rec["in"] + rec["out"] >= dt:
                    return False, f"오늘 토큰 한도({dt:,})를 모두 사용했습니다.", count
        return True, "", count

    def begin(self, user: str, count: int = 1) -> None:
        """authorize 통과 후 실제 실행 직전 — 레이트 윈도우 기록 + 동시 카운트 증가.

        preferred(N후보)는 실제로 N개 동시 실행이므로 count 만큼 증가시켜야 동시 한도가
        제대로 먹는다(예전엔 항상 +1 이라 count=8 배치가 1슬롯으로만 잡혀 한도 우회).
        """
        with self._lock:
            self._recent[user].append(time.time())
            self._active[user] += max(1, int(count or 1))

    def end(self, user: str, model: str, tokens_in: int, tokens_out: int, count: int = 1) -> None:
        """실행 완료 — 동시 카운트 감소 + 토큰/요청 누적(영속) + 감사 로그."""
        with self._lock:
            self._active[user] = max(0, self._active.get(user, 0) - max(1, int(count or 1)))
            rec = self._daily_rec(user)
            rec["in"] += int(tokens_in or 0)
            rec["out"] += int(tokens_out or 0)
            rec["req"] += max(1, count)
            rec["models"][model] = rec["models"].get(model, 0) + max(1, count)
            self._save_state()
        self._audit(user, model, tokens_in, tokens_out, count)

    def _daily_rec(self, user: str) -> dict:
        """오늘 날짜의 유저 카운터(날짜 바뀌면 리셋). _lock 보유 상태에서 호출."""
        today = _today()
        rec = self._daily.get(user)
        if not rec or rec.get("date") != today:
            rec = {"date": today, "in": 0, "out": 0, "req": 0, "models": {}}
            self._daily[user] = rec
        return rec

    # ---- 조회(콘솔/웹admin) -------------------------------------------
    def snapshot(self) -> list:
        """오늘 사용량 유저별 집계(토큰 내림차순). 콘솔 usage / admin 사용량 카드용."""
        today = _today()
        with self._lock:
            out = []
            for user, rec in self._daily.items():
                if rec.get("date") != today:
                    continue
                models = rec.get("models") or {}
                top = max(models, key=lambda m: models[m]) if models else "-"
                out.append({
                    "user": user, "req": rec["req"],
                    "tokens_in": rec["in"], "tokens_out": rec["out"],
                    "active": self._active.get(user, 0), "top_model": top,
                })
            out.sort(key=lambda d: -(d["tokens_in"] + d["tokens_out"]))
            return out

    def limits_view(self) -> dict:
        with self._lock:
            return {k: dict(v) for k, v in self._limits.items()}

    # ---- 영속 ----------------------------------------------------------
    def _load_state(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._daily = {u: r for u, r in data.items()
                               if isinstance(r, dict) and "date" in r}
        except Exception:
            self._daily = {}

    def _save_state(self) -> None:
        """원자적 저장(.tmp → replace). _lock 보유 상태에서 호출."""
        try:
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._daily, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except Exception:
            pass

    def _audit(self, user: str, model: str, tin: int, tout: int, count: int) -> None:
        try:
            line = json.dumps({
                "ts": int(time.time()), "user": user, "model": model,
                "in": int(tin or 0), "out": int(tout or 0), "count": int(count),
            }, ensure_ascii=False)
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
