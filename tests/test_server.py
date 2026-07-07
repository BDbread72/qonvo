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

            # 10) edge_add 중복 → dedup. 무효 op(중복)은 이제 브로드캐스트되지 않는다
            #     (no-op 억제 — pan 재실체화의 node_add 재방송 폭주를 막는 것과 동일 로직).
            #     후속 유효 op(move)을 보내 순서 동기화 후 doc 이 그대로임을 확인한다.
            await send_op(wa, {"op_id": "e1b", "op_type": "edge_add",
                               "data": {"source_node_id": 100, "target_node_id": 200}})
            await send_op(wa, {"op_id": "m0", "op_type": "node_move", "target": "100",
                               "data": {"x": 999, "y": 1}})   # 기존 좌표 유지(test 20 보존)
            await recv(wb)   # move 는 브로드캐스트됨 → 그때까진 중복 edge_add 도 처리 완료
            assert len(board.doc.get("edges", [])) == 1, board.doc.get("edges")
            ok("edge_add dedup (no duplicate edge, no-op not broadcast)")

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

            # 12b) node_signal — 휘발성: 다른 멤버에 브로드캐스트되지만 seq/doc/oplog 불변
            seq_before = board.seq
            oplog_before = len(board._oplog)
            await send_op(wa, {"op_id": "sig1", "op_type": "node_signal", "target": "100",
                               "data": {"data": True}})
            sigb = await recv(wb)
            assert sigb["type"] == "op" and sigb["ops"][0]["op_type"] == "node_signal", sigb
            assert board.seq == seq_before, (board.seq, seq_before)
            assert len(board._oplog) == oplog_before, (len(board._oplog), oplog_before)
            ok("node_signal broadcast-only (no seq/doc/oplog change)")

            # 12c) 크래시/오류 보고 — POST /report → reports.jsonl 적재
            from server.config import get_server_dir
            rpath = get_server_dir() / "reports" / "reports.jsonl"
            if rpath.exists():
                rpath.unlink()
            async with cs.post(f"http://127.0.0.1:{PORT}/report", json={
                "kind": "crash", "summary": "ZeroDivisionError: division by zero",
                "detail": "Traceback...\n  x = 1/0", "version": "beta-test",
                "user": "alice", "server": BOARD,
            }) as rr:
                assert rr.status == 200, rr.status
                assert (await rr.json()).get("ok") is True
            assert rpath.exists(), "reports.jsonl not written"
            rline = json.loads(rpath.read_text(encoding="utf-8").splitlines()[-1])
            assert rline["kind"] == "crash" and rline["user"] == "alice", rline
            assert "recv_ts" in rline and rline["server"] == BOARD, rline
            ok("crash report POST /report stored")

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

            # ── 섹션 E: 하드닝(재-auth 거부 / 보드전환 leave / id키 / 첨부 스코프) ──
            sbid = board.board_id
            BOARD2 = BOARD + "2"

            # 21) 재-auth 거부(http_token 누수 방지, S2)
            wg = await cs.ws_connect(URL)
            assert (await recv(wg))["type"] == "auth_required"
            await wg.send_str(json.dumps({"type": "auth", "user": "alice", "pass": "pw123"}))
            assert (await recv(wg))["type"] == "auth_ok"
            await wg.send_str(json.dumps({"type": "auth", "user": "alice", "pass": "pw123"}))
            r = await recv(wg)
            assert r["type"] == "error" and r["code"] == "already_authed", r
            ok("re-auth rejected (no http_token leak)")
            await wg.close()

            # 22) 보드 전환 → 이전 보드 멤버에게 user_leave(유령 presence 방지, M5)
            w1 = await cs.ws_connect(URL)
            w2 = await cs.ws_connect(URL)
            await auth_join(w1, "alice", "pw123", BOARD, last_seq=board.seq)
            await auth_join(w2, "bob", "pw456", BOARD, last_seq=board.seq)
            await recv(w1)  # bob user_join 소비
            await w1.send_str(json.dumps({"type": "join_board", "board_id": BOARD2, "last_seq": 0}))
            ul = await recv(w2)
            assert ul["type"] == "user_leave" and ul["user"] == "alice", ul
            ok("user_leave on board switch (no ghost presence)")
            await w1.close(); await w2.close()
            await asyncio.sleep(0.05)

            # 23) node_add 가 id + node_id 두 키 모두 저장(18개 노드타입 드롭 방지, H1)
            w3 = await cs.ws_connect(URL)
            await auth_join(w3, "alice", "pw123", BOARD, last_seq=board.seq)
            await send_op(w3, {"op_id": "na1", "op_type": "node_add", "target": "777",
                               "data": {"_category": "sticky_notes", "x": 1, "y": 2}})
            await asyncio.sleep(0.1)  # 단일 멤버 → 브로드캐스트 없음, doc 반영만 대기
            n777 = board._find_node("777")
            assert n777 is not None and n777.get("node_id") == 777 and n777.get("id") == 777, n777
            ok("node_add stores both id + node_id (sticky/image/etc. not dropped)")
            await w3.close()

            # 24) 첨부 접근이 보드 멤버십으로 스코프됨(크로스보드 차단, S1)
            w4 = await cs.ws_connect(URL)
            assert (await recv(w4))["type"] == "auth_required"
            await w4.send_str(json.dumps({"type": "auth", "user": "alice", "pass": "pw123"}))
            tok = (await recv(w4))["http_token"]
            await w4.send_str(json.dumps({"type": "join_board", "board_id": BOARD, "last_seq": 0}))
            await recv(w4); await recv(w4)  # sync + motd
            async with cs.get(f"http://127.0.0.1:{PORT}/board/{sbid}/manifest?t={tok}") as rr:
                assert rr.status == 200, ("member manifest", rr.status)
            other = board_store.safe_board_id(BOARD2)
            async with cs.get(f"http://127.0.0.1:{PORT}/board/{other}/manifest?t={tok}") as rr:
                assert rr.status == 401, ("cross-board must be denied", rr.status)
            ok("attachment access scoped to board membership")
            await w4.close()

            # 25) AI 응답 history 가 재접속 full-sync 에 복원됨(캐시 무효화 + 클라 포맷)
            #     — '1회 실행 후 껏다 키면 로그가 사라지고 대기중' 버그의 회귀 방지.
            _ = board.snapshot_for_join(0)          # full-sync 캐시를 현재 seq 로 워밍
            board.append_assistant_message("300", "resp-text", [],
                                           user="prompt-text", model="m1")
            snap = board.snapshot_for_join(0)       # 재접속(항상 full sync)
            doc = snap.get("snapshot")
            if doc is None and snap.get("snapshot_gz"):
                import base64 as _b64, gzip as _gz
                doc = json.loads(_gz.decompress(_b64.b64decode(snap["snapshot_gz"])).decode("utf-8"))
            n300 = next((n for n in doc.get("nodes", [])
                         if n.get("id") == 300 or n.get("node_id") == 300), None)
            assert n300 is not None, "chat node 300 missing from full sync"
            h = n300.get("history", [])
            assert h and h[-1].get("user") == "prompt-text" \
                and h[-1].get("response") == "resp-text", h
            ok("AI history restored on rejoin (snap cache invalidated, client format)")

            # 26) preferred(N후보) 실행도 재접속 로그에 남는다(예전엔 문서에 아무것도 안 남김)
            _ = board.snapshot_for_join(0)
            board.append_preferred_message(
                "300", user="pick one", model="m2",
                candidates=[{"text": "cand-A", "images": []},
                            {"text": "cand-B", "images": []}])
            snap2 = board.snapshot_for_join(0)
            doc2 = snap2.get("snapshot")
            if doc2 is None and snap2.get("snapshot_gz"):
                import base64 as _b64b, gzip as _gzb
                doc2 = json.loads(_gzb.decompress(_b64b.b64decode(snap2["snapshot_gz"])).decode("utf-8"))
            n300b = next((n for n in doc2.get("nodes", [])
                          if n.get("id") == 300 or n.get("node_id") == 300), None)
            h2 = (n300b or {}).get("history", [])
            assert h2 and h2[-1].get("preferred_texts") == ["cand-A", "cand-B"], h2
            ok("preferred run persists to rejoin log (candidates survive)")

            # BOARD2 정리(테스트가 만든 보드 디렉토리)
            b2dir = board_store.get_boards_dir() / board_store.safe_board_id(BOARD2)
            if b2dir.exists():
                import shutil
                shutil.rmtree(b2dir, ignore_errors=True)

    finally:
        await server.stop()

    print("\n".join(f"  [OK] {p}" for p in passed))
    print(f"\n{len(passed)} checks passed")
    return passed


EXPECTED = 31

if __name__ == "__main__":
    result = asyncio.run(run())
    if len(result) != EXPECTED:
        print(f"\n[FAIL] expected {EXPECTED} checks, got {len(result)}")
        sys.exit(1)
    print("[PASS] all server E2E checks green")
    sys.exit(0)
