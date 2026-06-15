"""Qonvo 서버 엔드투엔드 스모크 테스트 (pytest 아님, 단독 실행).

서버를 인프로세스로 띄우고 aiohttp ws 클라이언트 2개로
인증 → join → op 브로드캐스트 → delta sync 영속화를 검증한다.

  python tests/test_server.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import aiohttp  # noqa: E402

from server import auth as auth_mod  # noqa: E402
from server import board_store  # noqa: E402
from server.app import QonvoServer  # noqa: E402

PORT = 19799  # 테스트 전용 포트
URL = f"ws://127.0.0.1:{PORT}/ws"
BOARD = "_test_board_smoke"


async def recv(ws):
    msg = await asyncio.wait_for(ws.receive(), timeout=5)
    return json.loads(msg.data)


async def auth_join(sess, user, pw, board, last_seq=0):
    """auth_required→auth→auth_ok, 그리고 join→sync/delta 를 수행."""
    m = await recv(sess)
    assert m["type"] == "auth_required", m
    await sess.send_str(json.dumps({"type": "auth", "user": user, "pass": pw}))
    m = await recv(sess)
    assert m["type"] == "auth_ok", m
    await sess.send_str(json.dumps({"type": "join_board", "board_id": board, "last_seq": last_seq}))
    resp = await recv(sess)  # sync 또는 delta
    motd = await recv(sess)  # 서버가 join 후 보내는 server_msg(motd)
    assert motd["type"] == "server_msg", motd
    return resp


async def run():
    # 깨끗한 상태 보장
    auth_mod.add_user("alice", "pw123", auth_mod.MEMBER)
    auth_mod.add_user("bob", "pw456", auth_mod.MEMBER)
    auth_mod.add_user("guestv", "pwv", auth_mod.VISITOR)

    bdir = board_store.get_boards_dir() / board_store.safe_board_id(BOARD)
    if bdir.exists():
        import shutil
        shutil.rmtree(bdir)

    config = {
        "server": {"host": "127.0.0.1", "port": PORT, "name": "Test", "motd": "hi",
                   "default_level": 1, "allow_guests": False},
        "ai": {"gemini_keys": [], "default_model": "gemini-2.5-flash"},
        "users": {}, "oauth": {"mattermost": {"enabled": False}},
    }
    server = QonvoServer(config)
    await server.start()

    passed = []

    try:
        async with aiohttp.ClientSession() as cs:
            # 1) 잘못된 비밀번호 → auth_fail
            async with cs.ws_connect(URL) as w:
                assert (await recv(w))["type"] == "auth_required"
                await w.send_str(json.dumps({"type": "auth", "user": "alice", "pass": "WRONG"}))
                assert (await recv(w))["type"] == "auth_fail"
                passed.append("auth_fail on bad password")

            # 2) alice/bob 접속 + join → 첫 join 은 full sync
            wa = await cs.ws_connect(URL)
            wb = await cs.ws_connect(URL)
            sa = await auth_join(wa, "alice", "pw123", BOARD)
            assert sa["type"] == "sync", sa
            passed.append("full sync on first join")

            sb = await auth_join(wb, "bob", "pw456", BOARD)
            assert sb["type"] == "sync", sb
            # bob join 시 alice 에게 user_join 브로드캐스트
            uj = await recv(wa)
            assert uj["type"] == "user_join" and uj["user"] == "bob", uj
            passed.append("user_join broadcast")

            # 3) alice 가 op 전송 → bob 이 수신
            op = {"op_id": "x1", "op_type": "node_add", "target": "100",
                  "data": {"_category": "nodes", "x": 50, "y": 60}}
            await wa.send_str(json.dumps({"type": "op", "ops": [op]}))
            mb = await recv(wb)
            assert mb["type"] == "op" and mb["author"] == "alice", mb
            assert mb["ops"][0]["target"] == "100"
            assert mb["seq"] == 1
            passed.append("op broadcast to other member with seq")

            # 4) 권위 문서에 반영됐는지
            board = server.boards.get(BOARD)
            node = board._find_node("100")
            assert node and node["x"] == 50, node
            passed.append("authoritative doc updated by op")

            # 5) move op
            await wa.send_str(json.dumps({"type": "op", "ops": [
                {"op_id": "x2", "op_type": "node_move", "target": "100", "data": {"x": 999, "y": 1}}]}))
            await recv(wb)
            assert board._find_node("100")["x"] == 999
            passed.append("node_move applied")

            await wa.close(); await wb.close()
            await asyncio.sleep(0.1)

            # 6) 새 클라가 last_seq=1 로 재접속 → delta(나머지 op만)
            wc = await cs.ws_connect(URL)
            sc = await auth_join(wc, "alice", "pw123", BOARD, last_seq=1)
            assert sc["type"] == "delta", sc
            assert sc["seq"] == 2
            assert len(sc["ops"]) == 1 and sc["ops"][0]["op_type"] == "node_move", sc
            passed.append("delta sync since last_seq")
            await wc.close()

            # 7) Visitor 는 op 거부
            wv = await cs.ws_connect(URL)
            sv = await auth_join(wv, "guestv", "pwv", BOARD)
            assert sv["type"] in ("sync", "delta")
            await wv.send_str(json.dumps({"type": "op", "ops": [
                {"op_id": "x9", "op_type": "node_remove", "target": "100", "data": {}}]}))
            mv = await recv(wv)
            assert mv["type"] == "error" and mv["code"] == "perm", mv
            passed.append("visitor op rejected (read-only)")
            await wv.close()

            # 8) 영속화: 스냅샷 저장 후 디스크에서 재로딩
            board.save_snapshot()
            reloaded = board_store.Board(BOARD)
            assert reloaded._find_node("100")["x"] == 999, "persisted doc mismatch"
            passed.append("snapshot persisted + reloaded from disk")

    finally:
        await server.stop()

    print("\n".join(f"  [OK] {p}" for p in passed))
    print(f"\n{len(passed)}/9 checks passed")
    return len(passed) == 9


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
