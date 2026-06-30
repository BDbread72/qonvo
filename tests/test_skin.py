"""Dynamic Cursor 커서 스킨 — 서버 측 E2E (pytest 아님, 단독 실행).

skin_store 유닛(저장/해시/삭제/검증) + 서버를 인프로세스로 띄워 HTTP 엔드포인트
(PUT/GET/DELETE /skin)와 presence 의 skin 해시 전파를 검증한다.

UPnP/라우터는 안 건드린다(upnp=False → hermetic).

  python tests/test_skin.py
"""
import asyncio
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # cp949 콘솔에서 이모지 출력
except Exception:
    pass

import aiohttp  # noqa: E402

from server import auth as auth_mod  # noqa: E402
from server import skin_store  # noqa: E402
from server.app import QonvoServer  # noqa: E402

PORT = 19798  # 테스트 전용 포트(메인 테스트 19799 와 분리)
URL = f"ws://127.0.0.1:{PORT}/ws"
HTTP = f"http://127.0.0.1:{PORT}"
BOARD = "_test_skin_board"

# 1x1 투명 PNG (유효한 PNG 매직으로 시작)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
NOT_PNG = b"GIF89a not really a png" * 4


async def recv(ws, skip=("presence",)):
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout=5)
        d = json.loads(msg.data)
        if d.get("type") in skip:
            continue
        return d


async def recv_presence(ws):
    """presence 메시지만 골라 받는다."""
    return await recv(ws, skip=("user_join", "user_leave", "server_msg"))


async def auth_only(ws, user, pw):
    """auth_required→auth→auth_ok. http_token 반환."""
    m = await recv(ws)
    assert m["type"] == "auth_required", m
    await ws.send_str(json.dumps({"type": "auth", "user": user, "pass": pw}))
    m = await recv(ws)
    assert m["type"] == "auth_ok", m
    return m["http_token"]


async def join(ws, board, last_seq=0):
    await ws.send_str(json.dumps(
        {"type": "join_board", "board_id": board, "last_seq": last_seq}))
    resp = await recv(ws)            # sync/delta
    await recv(ws)                   # motd(server_msg)
    return resp


def _unit_tests(ok):
    # 깨끗한 상태
    skin_store.delete_skin("ualice")
    skin_store.delete_skin("ubob")

    assert skin_store.is_png(PNG) and not skin_store.is_png(NOT_PNG)
    ok("skin_store.is_png magic check")

    assert skin_store.skin_hash("ualice") == ""
    ok("skin_hash empty when no skin")

    h = skin_store.save_skin("ualice", PNG)
    assert h and len(h) == 16, h
    ok("save_skin returns 16-hex hash")

    assert skin_store.skin_hash("ualice") == h
    ok("skin_hash matches saved (mtime cache)")

    # 비-PNG / 과대 크기는 거부
    assert skin_store.save_skin("ualice", NOT_PNG) is None
    assert skin_store.save_skin("ualice", b"\x89PNG\r\n\x1a\n" + b"x" * (600 * 1024)) is None
    ok("save_skin rejects non-PNG and oversize")

    # 경로 정규화(traversal 방지) — username 의 / 등은 _ 로
    p = skin_store.skin_path("../../etc/passwd")
    assert p is not None and p.parent == skin_store.get_skins_dir(), p
    ok("skin_path sanitizes username (no traversal)")

    assert skin_store.delete_skin("ualice") is True
    assert skin_store.skin_hash("ualice") == ""
    ok("delete_skin removes skin")


async def run():
    auth_mod.add_user("alice", "pw123", auth_mod.MEMBER)
    auth_mod.add_user("bob", "pw456", auth_mod.MEMBER)
    auth_mod.add_user("guestv", "pwv", auth_mod.VISITOR)
    for u in ("alice", "bob", "guestv"):
        skin_store.delete_skin(u)

    from server import board_store
    bdir = board_store.get_boards_dir() / board_store.safe_board_id(BOARD)
    if bdir.exists():
        import shutil
        shutil.rmtree(bdir)

    config = {
        "server": {"host": "127.0.0.1", "port": PORT, "name": "Test", "motd": "hi",
                   "default_level": 1, "allow_guests": False},
        "ai": {"gemini_keys": [], "default_model": "gemini-2.5-flash"},
        "users": {}, "oauth": {"mattermost": {"enabled": False}},
        "network": {"upnp": False},
    }
    server = QonvoServer(config)
    await server.start()

    passed = []

    def ok(label):
        passed.append(label)

    try:
        _unit_tests(ok)

        async with aiohttp.ClientSession() as cs:
            # alice/bob 접속 + join
            wa = await cs.ws_connect(URL)
            wb = await cs.ws_connect(URL)
            ta = await auth_only(wa, "alice", "pw123")
            tb = await auth_only(wb, "bob", "pw456")
            await join(wa, BOARD)
            await join(wb, BOARD)
            await recv(wa)  # bob user_join 소비

            # 1) 토큰 없이 GET/PUT → 401
            async with cs.get(f"{HTTP}/skin/alice") as r:
                assert r.status == 401, r.status
            ok("skin GET without token → 401")

            # 2) 스킨 없으면 GET → 404
            async with cs.get(f"{HTTP}/skin/alice?t={tb}") as r:
                assert r.status == 404, r.status
            ok("skin GET when none → 404")

            # 3) alice 가 자기 스킨 PUT → 200 + hash
            async with cs.put(f"{HTTP}/skin?t={ta}", data=PNG) as r:
                assert r.status == 200, r.status
                j = await r.json()
                hsh = j["hash"]
                assert len(hsh) == 16, j
            ok("skin PUT (own) → 200 + hash")

            # 4) bob 이 alice 스킨 GET → 바이트 일치
            async with cs.get(f"{HTTP}/skin/alice?t={tb}") as r:
                assert r.status == 200, r.status
                got = await r.read()
                assert got == PNG, (len(got), len(PNG))
            ok("skin GET (other user) → exact bytes")

            # 5) 업로드 직후 presence 가 bob 에게 alice.skin 해시를 전파
            #    (join 시점에 큐된 옛 presence 들을 지나 새 해시가 박힌 것까지 드레인)
            au = None
            for _ in range(8):
                pm = await recv_presence(wb)
                assert pm["type"] == "presence", pm
                cand = next((u for u in pm["users"] if u["user"] == "alice"), None)
                if cand and cand.get("skin") == hsh:
                    au = cand
                    break
            assert au, f"presence 에 업로드된 skin 미반영 (last={pm})"
            ok("presence carries skin hash after upload")

            # 6) 비-PNG PUT → 400
            async with cs.put(f"{HTTP}/skin?t={ta}", data=NOT_PNG) as r:
                assert r.status == 400, r.status
            ok("skin PUT non-PNG → 400")

            # 7) Visitor 는 PUT 거부(read-only)
            wv = await cs.ws_connect(URL)
            tv = await auth_only(wv, "guestv", "pwv")
            async with cs.put(f"{HTTP}/skin?t={tv}", data=PNG) as r:
                assert r.status == 403, r.status
            ok("visitor skin PUT → 403 (read-only)")
            await wv.close()

            # 8) 재접속 시 auth 단계에서 저장된 스킨 해시가 로드돼 presence 로 광고
            await wa.close()
            await asyncio.sleep(0.1)
            wa2 = await cs.ws_connect(URL)
            ta2 = await auth_only(wa2, "alice", "pw123")
            await join(wa2, BOARD)
            # bob 이 보는 presence(alice 재접속)에 skin 유지
            found = None
            for _ in range(6):
                pm = await recv_presence(wb)
                au = next((u for u in pm["users"] if u["user"] == "alice"), None)
                if au and au.get("skin") == hsh:
                    found = au
                    break
            assert found, "재접속 후 presence 에 저장된 skin 미반영"
            ok("persisted skin loaded at auth → advertised in presence")

            # 9) DELETE → 기본 복귀(GET 404)
            async with cs.delete(f"{HTTP}/skin?t={ta2}") as r:
                assert r.status == 200, r.status
            async with cs.get(f"{HTTP}/skin/alice?t={tb}") as r:
                assert r.status == 404, r.status
            ok("skin DELETE → reset (GET 404)")

            await wa2.close()
            await wb.close()
            await asyncio.sleep(0.1)
    finally:
        await server.stop()

    print(f"\n  PASSED {len(passed)}/{len(passed)} checks:")
    for p in passed:
        print(f"    ✓ {p}")
    return True


if __name__ == "__main__":
    try:
        asyncio.run(run())
        print("\n  ALL SKIN TESTS PASSED ✅")
    except AssertionError as e:
        print(f"\n  ❌ ASSERTION FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n  ❌ ERROR: {e}")
        sys.exit(1)
