"""AI 거버넌스(server.ai_policy.AIPolicy) 단위 테스트 — 표준 스크립트(헤르메틱).

실행:  cd src && python ../tests/test_ai_policy.py
검증:  모델 허용목록 / 이미지 게이트 / count 클램프 / 레이트 / 동시 / 일일토큰 쿼타 /
       Operator 무제한 / 재시작 영속 / 감사로그.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from server.ai_policy import AIPolicy  # noqa: E402

MEMBER, OPERATOR = 1, 2

CFG = {"ai_policy": {
    "member": {"models": ["gemini-2.5-flash"], "allow_image": False, "rate_per_min": 2,
               "concurrent": 1, "daily_tokens": 100, "max_count": 3},
    "operator": {"models": ["*"], "allow_image": True, "rate_per_min": 0,
                 "concurrent": 0, "daily_tokens": 0, "max_count": 8},
}}


def _fresh():
    return AIPolicy(CFG, state_dir=Path(tempfile.mkdtemp()))


def main() -> int:
    p = _fresh()

    ok, why, _ = p.authorize("alice", MEMBER, "gpt-4o", 1, False)
    assert not ok and "gpt-4o" in why, (ok, why)
    print("  [OK] member blocked from non-allowlisted model")

    ok, why, _ = p.authorize("alice", MEMBER, "gemini-2.5-flash", 1, True)
    assert not ok and "이미지" in why, (ok, why)
    print("  [OK] member blocked from image model")

    ok, _, c = p.authorize("alice", MEMBER, "gemini-2.5-flash", 8, False)
    assert ok and c == 3, (ok, c)
    print("  [OK] count clamped to max_count")

    ok, _, c = p.authorize("boss", OPERATOR, "gpt-image-1", 8, True)
    assert ok and c == 8, (ok, c)
    print("  [OK] operator unrestricted (model/image/count)")

    # 레이트 2/min
    for _ in range(2):
        p.begin("alice"); p.end("alice", "gemini-2.5-flash", 10, 5, 1)
    ok, why, _ = p.authorize("alice", MEMBER, "gemini-2.5-flash", 1, False)
    assert not ok and "분당" in why, (ok, why)
    print("  [OK] rate limit enforced")

    # 동시 1
    p2 = _fresh()
    ok, _, _ = p2.authorize("bob", MEMBER, "gemini-2.5-flash", 1, False); assert ok
    p2.begin("bob")
    ok, why, _ = p2.authorize("bob", MEMBER, "gemini-2.5-flash", 1, False)
    assert not ok and "동시" in why, (ok, why)
    p2.end("bob", "gemini-2.5-flash", 1, 1, 1)
    ok, _, _ = p2.authorize("bob", MEMBER, "gemini-2.5-flash", 1, False); assert ok
    print("  [OK] concurrency limit enforced + released")

    # 일일 토큰 100
    p3 = _fresh()
    p3.begin("carol"); p3.end("carol", "gemini-2.5-flash", 60, 50, 1)
    ok, why, _ = p3.authorize("carol", MEMBER, "gemini-2.5-flash", 1, False)
    assert not ok and "토큰 한도" in why, (ok, why)
    print("  [OK] daily token quota enforced")

    # 재시작 영속 + 감사로그
    sd = Path(tempfile.mkdtemp())
    pa = AIPolicy(CFG, state_dir=sd)
    pa.begin("dave"); pa.end("dave", "gemini-2.5-flash", 40, 30, 1)
    pb = AIPolicy(CFG, state_dir=sd)
    snap = pb.snapshot()
    assert any(r["user"] == "dave" and r["tokens_in"] == 40 and r["tokens_out"] == 30
               for r in snap), snap
    assert (sd / "usage.jsonl").exists()
    print("  [OK] usage persisted across restart + audit log written")

    # 기본값(설정 없음) = 전부 무제한 → 하위호환
    p4 = AIPolicy({}, state_dir=Path(tempfile.mkdtemp()))
    ok, _, c = p4.authorize("eve", MEMBER, "any-model", 8, True)
    assert ok and c == 8, (ok, c)
    print("  [OK] default config = unlimited (backward compatible)")

    print("\n9 checks passed\n[PASS] all ai_policy checks green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
