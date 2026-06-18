"""Qonvo 서버 엔드투엔드 테스트 (pytest 아님, 단독 실행).

서버를 인프로세스로 띄우고 aiohttp ws 클라이언트로 인증 → join → op 브로드캐스트
→ delta sync → presence/chat → 권한 → 영속화를 검증한다.

UPnP/라우터는 건드리지 않는다(config 에서 upnp=False → hermetic).
presence 는 서버가 join/op 와 무관하게 비동기로 쏘는 노이즈라 recv() 가 기본 skip 한다.

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


async def recv(ws, skip=("presence",)):
    """메시지 하나를 받되, skip 타입(기본: presence 비동기 노이즈)은 건너뛴다.

    presence 는 join/op 와 무관하게 서버가 쏘므로, 명시적으로 검증할 때만
    skip=() 로 받는다. 그 외 모든 단언은 presence 에 흔들리지 않는다.
    """
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout=5)
        d = json.loads(msg.data)
        if d.get("type") in skip:
            continue
        return d


async def send_op(ws, ops):
    """op(들)을 전송. dict 하나면 [op] 로 감싼다."""
    if isinstance(ops, dict):
        ops = [ops]
    await ws.send_str(json.dumps({"type": "op", "ops": ops}))


async def auth_join(sess, user, pw, board, last_seq=0):
    """auth_required→auth→auth_ok, 그리고 join→sync/delta 를 수행."""
    m = await recv(sess)
    assert m["type"] == "auth_required", m
    await sess.send_str(json.dumps({"type": "auth", "user": user, "pass": pw}))
    m = await recv(sess)
    assert m["type"] == "auth_ok", m
    assert m.get("http_token"), "auth_ok 에 http_token 미발급"  # 첨부 HTTP 토큰
    await sess.send_str(json.dumps(
        {"type": "join_board", "board_id": board, "last_seq": last_seq}))
    resp = await recv(sess)   # sync 또는 delta (presence 는 skip)
    motd = await recv(sess)   # 서버가 join 후 보내는 server_msg(motd)
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
        "network": {"upnp": False},  # ★ 라우터/SSDP 건드리지 않음 (hermetic)
    }
    server = QonvoServer(config)
    await server.start()

    passed = []

    def ok(label):
        passed.append(label)

    try:
        async with aiohttp.ClientSession() as cs:
            # ── 섹션 A: 인증 / 기본 op / delta / visitor ─────────────────
            # 1) 잘못된 비밀번호 → auth_fail
            async with cs.ws_connect(URL) as w:
                assert (await recv(w))["type"] == "auth_required"
                await w.send_str(json.dumps({"type": "auth", "user": "alice", "pass": "WRONG"}))
                assert (await recv(w))["type"] == "auth_fail"
            ok("auth_fail on bad password")

            # 2) alice/bob 접속 + join → 첫 join 은 full sync
            wa = await cs.ws_connect(URL)
            wb = await cs.ws_connect(URL)
            sa = await auth_join(wa, "alice", "pw123", BOARD)
            assert sa["type"] == "sync", sa
            ok("full sync on first join (+http_token issued)")

            sb = await auth_join(wb, "bob", "pw456", BOARD)
            assert sb["type"] == "sync", sb
            uj = await recv(wa)  # bob join 시 alice 에게 user_join (presence 는 skip)
            assert uj["type"] == "user_join" and uj["user"] == "bob", uj
            ok("user_join broadcast")

            # 3) alice 가 op 전송 → bob 이 수신 (sender 제외)
            await send_op(wa, {"op_id": "x1", "op_type": "node_add", "target": "100",
                               "data": {"_category": "nodes", "x": 50, "y": 60}})
            mb = await recv(wb)
            assert mb["type"] == "op" and mb["author"] == "alice", mb
            assert mb["ops"][0]["target"] == "100" and mb["seq"] == 1
            ok("op broadcast to other member with seq")

            board = server.boards.get(BOARD)
            assert board._find_node("100")["x"] == 50
            ok("authoritative doc updated by op")

            # 4) move op
            await send_op(wa, {"op_id": "x2", "op_type": "node_move", "target": "100",
                               "data": {"x": 999, "y": 1}})
            await recv(wb)
            assert board._find_node("100")["x"] == 999
            ok("node_move applied")

            await wa.close()
            await wb.close()
            await asyncio.sleep(0.1)

            # 5) 새 클라가 last_seq=1 로 재접속 → delta(나머지 op만)
            wc = await cs.ws_connect(URL)
            sc = await auth_join(wc, "alice", "pw123", BOARD, last_seq=1)
            assert sc["type"] == "delta" and sc["seq"] == 2, sc
            assert len(sc["ops"]) == 1 and sc["ops"][0]["op_type"] == "node_move", sc
            ok("delta sync since last_seq")
            await wc.close()
            await asyncio.sleep(0.05)

            # 6) Visitor 는 op / ai_request 둘 다 거부
            wv = await cs.ws_connect(URL)
            sv = await auth_join(wv, "guestv", "pwv", BOARD)
            assert sv["type"] in ("sync", "delta")
            await send_op(wv, {"op_id": "x9", "op_type": "node_remove", "target": "100", "data": {}})
            mv = await recv(wv)
            assert mv["type"] == "error" and mv["code"] == "perm", mv
            ok("visitor op rejected (read-only)")

            await wv.send_str(json.dumps({"type": "ai_request", "node_id": "100", "params": {}}))
            mv = await recv(wv)
            assert mv["type"] == "error" and mv["code"] == "perm", mv
            ok("visitor ai_request rejected")
            await wv.close()
            await asyncio.sleep(0.05)

            # ── 섹션 B: 협업 op 전 타입 + presence/chat/ping ──────────────
            wa = await cs.ws_connect(URL)
            wb = await cs.ws_connect(URL)
            await auth_join(wa, "alice", "pw123", BOARD, last_seq=board.seq)
            await auth_join(wb, "bob", "pw456", BOARD, last_seq=board.seq)
            await recv(wa)  # bob 의 user_join 소비

            # 7) node_prop — 전체 dict 머지 (텍스트/색상/제목 등 영속용)
            await send_op(wa, {"op_id": "p1", "op_type": "node_prop", "target": "100",
                               "data": {"data": {"title": "hello", "color": "#abc", "text": "body"}}})
            await recv(wb)
            n100 = board._find_node("100")
            assert n100["title"] == "hello" and n100["color"] == "#abc" and n100["text"] == "body", n100
            ok("node_prop (full dict merge) applied")

            # 8) node_prop — 단일 key/value
            await send_op(wa, {"op_id": "p2", "op_type": "node_prop", "target": "100",
                               "data": {"key": "w", "value": 321}})
            await recv(wb)
            assert board._find_node("100")["w"] == 321
            ok("node_prop (key/value) applied")

            # 9) edge_add — 노드 200 추가 후 100→200 엣지
            await send_op(wa, {"op_id": "n2", "op_type": "node_add", "target": "200",
                               "data": {"_category": "nodes", "x": 300, "y": 300}})
            await recv(wb)
            await send_op(wa, {"op_id": "e1", "op_type": "edge_add",
                               "data": {"source_node_id": 100, "target_node_id": 200}})
            await recv(wb)
            edges = board.doc.get("edges", [])
            assert len(edges) == 1 and edges[0]["source_node_id"] == 100 \
                and edges[0]["target_node_id"] == 200, edges
            ok("edge_add applied + normalized")

            # 10) edge_add 중복 → dedup (브로드캐스트는 되지만 doc 은 그대로)
            await send_op(wa, {"op_id": "e1b", "op_type": "edge_add",
                               "data": {"source_node_id": 100, "target_node_id": 200}})
            await recv(wb)
            assert len(board.doc.get("edges", [])) == 1, board.doc.get("edges")
            ok("edge_add dedup (no duplicate edge)")

            # 11) edge_remove
            await send_op(wa, {"op_id": "e2", "op_type": "edge_remove",
                               "data": {"source_node_id": 100, "target_node_id": 200}})
            await recv(wb)
            assert len(board.doc.get("edges", [])) == 0
            ok("edge_remove applied")

            # 12) chat_append — 채팅 노드 history 누적
            await send_op(wa, {"op_id": "n3", "op_type": "node_add", "target": "300",
                               "data": {"_category": "nodes", "x": 0, "y": 0}})
            await recv(wb)
            await send_op(wa, {"op_id": "c1", "op_type": "chat_append", "target": "300",
                               "data": {"message": {"role": "user", "content": "hi there"}}})
            await recv(wb)
            hist = board._find_node("300").get("history", [])
            assert hist == [{"role": "user", "content": "hi there"}], hist
            ok("chat_append appends to node history")

            # 13) ping → pong (t 그대로 echo)
            await wa.send_str(json.dumps({"type": "ping", "t": 12345}))
            pong = await recv(wa)
            assert pong["type"] == "pong" and pong["t"] == 12345, pong
            ok("ping → pong echo")

            # 14) chat 브로드캐스트 (sender 포함 전원)
            await wa.send_str(json.dumps({"type": "chat", "text": "hello team"}))
            cb = await recv(wb)
            assert cb["type"] == "chat" and cb["user"] == "alice" and cb["text"] == "hello team", cb
            ca = await recv(wa)  # sender 에게도 echo
            assert ca["type"] == "chat" and ca["text"] == "hello team", ca
            ok("chat broadcast (incl. sender)")

            # 15) presence 브로드캐스트 — 커서 보고 → users 목록에 반영
            await wa.send_str(json.dumps({"type": "presence", "cursor": {"x": 5, "y": 7}}))
            pb = await recv(wb, skip=())  # 이번엔 presence 를 받아야 함
            assert pb["type"] == "presence", pb
            alice_e = next((u for u in pb["users"] if u["user"] == "alice"), None)
            assert alice_e and alice_e["cursor"] == {"x": 5, "y": 7}, pb
            await recv(wa, skip=())  # alice 자신에게 온 presence 소비
            ok("presence broadcast reflects cursor")

            # 16) user_leave — alice 종료 시 bob 에게 통지
            await wa.close()
            ul = await recv(wb)
            assert ul["type"] == "user_leave" and ul["user"] == "alice", ul
            ok("user_leave broadcast on disconnect")
            await wb.close()
            await asyncio.sleep(0.1)

            # ── 섹션 C: sync/delta 경계 ──────────────────────────────────
            cur = board.seq
            # 17) last_seq == 현재 seq → 빈 delta
            wd = await cs.ws_connect(URL)
            sd = await auth_join(wd, "alice", "pw123", BOARD, last_seq=cur)
            assert sd["type"] == "delta" and sd["ops"] == [], sd
            ok("empty delta when already up-to-date")
            await wd.close()

            # 18) last_seq 가 비연속(미래) → full sync 폴백
            we = await cs.ws_connect(URL)
            se = await auth_join(we, "alice", "pw123", BOARD, last_seq=cur + 9999)
            assert se["type"] == "sync", se
            ok("full sync fallback on non-contiguous last_seq")
            await we.close()
            await asyncio.sleep(0.05)

            # ── 섹션 D: 접근 제어(ban 게이트) ────────────────────────────
            # 19) 밴된 사용자는 자격증명이 맞아도 auth_fail
            auth_mod.add_user("banme", "pwban", auth_mod.MEMBER)
            auth_mod.ban_user("banme")
            try:
                wf = await cs.ws_connect(URL)
                assert (await recv(wf))["type"] == "auth_required"
                await wf.send_str(json.dumps({"type": "auth", "user": "banme", "pass": "pwban"}))
                af = await recv(wf)
                assert af["type"] == "auth_fail", af
                ok("banned user rejected at auth")
                await wf.close()
            finally:
                auth_mod.pardon_user("banme")  # 실제 banned.json 오염 정리

            # 20) 영속화: 스냅샷 저장 후 디스크에서 재로딩
            board.save_snapshot()
            reloaded = board_store.Board(BOARD)
            assert reloaded._find_node("100")["x"] == 999, "persisted doc mismatch"
            assert reloaded._find_node("100")["title"] == "hello", "node_prop not persisted"
            ok("snapshot persisted + reloaded from disk (incl. node_prop)")

    finally:
        await server.stop()

    print("\n".join(f"  [OK] {p}" for p in passed))
    print(f"\n{len(passed)} checks passed")
    return passed


EXPECTED = 23

if __name__ == "__main__":
    result = asyncio.run(run())
    if len(result) != EXPECTED:
        print(f"\n[FAIL] expected {EXPECTED} checks, got {len(result)}")
        sys.exit(1)
    print("[PASS] all server E2E checks green")
    sys.exit(0)
