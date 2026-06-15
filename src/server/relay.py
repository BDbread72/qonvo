"""경량 릴레이 — 잠긴 망의 호스트도 외부 접속을 받게 한다(스팀 SDR 식).

호스트는 ``/relay/host`` 로 **나가는** WS 연결을 유지하고, 손님은 ``/relay/c`` 로
접속한다. 릴레이는 둘 사이 메시지를 conn_id 로 멀티플렉싱해 **전달만** 한다.
보드 데이터/권위는 호스트에 그대로 — 릴레이는 우체부일 뿐(중앙 업로드 아님).

프레임(JSON, 호스트 채널):
  relay→host : {"t":"open","c":cid}            손님 입장
               {"t":"data","c":cid,"d":text}   손님이 보낸 메시지
               {"t":"close","c":cid}           손님 퇴장
  host→relay : {"t":"data","c":cid,"d":text}   손님에게 전달할 메시지
               {"t":"close","c":cid}           손님 연결 종료

손님 WS(/relay/c)는 투명 파이프 — 손님은 평소 qonvo 프로토콜 그대로 말하고,
호스트의 로컬 임베드 서버(터널 클라이언트 경유)가 처리한다.
"""
from __future__ import annotations

import json
from typing import Dict, Tuple

from aiohttp import web, WSMsgType


class RelayHub:
    def __init__(self):
        self._hosts: Dict[str, web.WebSocketResponse] = {}        # session -> host ws
        self._conns: Dict[Tuple[str, int], web.WebSocketResponse] = {}  # (session,cid) -> joiner ws
        self._cid = 0

    def sessions(self) -> list:
        return list(self._hosts.keys())

    async def host_handler(self, request: web.Request) -> web.WebSocketResponse:
        session = request.query.get("session", "").strip()
        ws = web.WebSocketResponse(max_msg_size=64 * 1024 * 1024, heartbeat=30)
        await ws.prepare(request)
        if not session:
            await ws.close(); return ws
        if session in self._hosts:
            # 이미 그 세션을 호스팅 중 — 신규 호스트 거부
            await ws.send_str(json.dumps({"t": "error", "m": "session taken"}))
            await ws.close(); return ws
        self._hosts[session] = ws
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    f = json.loads(msg.data)
                except Exception:
                    continue
                cid = f.get("c")
                t = f.get("t")
                jw = self._conns.get((session, cid))
                if jw is None:
                    continue
                if t == "data":
                    try:
                        await jw.send_str(f.get("d", ""))
                    except Exception:
                        pass
                elif t == "close":
                    try:
                        await jw.close()
                    except Exception:
                        pass
        finally:
            self._hosts.pop(session, None)
            for (s, c) in [k for k in self._conns if k[0] == session]:
                jw = self._conns.pop((s, c), None)
                if jw is not None:
                    try:
                        await jw.close()
                    except Exception:
                        pass
        return ws

    async def join_handler(self, request: web.Request) -> web.WebSocketResponse:
        session = request.query.get("session", "").strip()
        host = self._hosts.get(session)
        ws = web.WebSocketResponse(max_msg_size=64 * 1024 * 1024, heartbeat=30)
        await ws.prepare(request)
        if host is None:
            await ws.close(); return ws        # 호스트 오프라인
        self._cid += 1
        cid = self._cid
        self._conns[(session, cid)] = ws
        try:
            await host.send_str(json.dumps({"t": "open", "c": cid}))
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    break
                h = self._hosts.get(session)
                if h is None:
                    break
                await h.send_str(json.dumps({"t": "data", "c": cid, "d": msg.data}))
        except Exception:
            pass
        finally:
            self._conns.pop((session, cid), None)
            h = self._hosts.get(session)
            if h is not None:
                try:
                    await h.send_str(json.dumps({"t": "close", "c": cid}))
                except Exception:
                    pass
        return ws
