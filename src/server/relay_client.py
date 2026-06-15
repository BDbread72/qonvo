"""릴레이 터널 클라이언트 — 호스트 측.

임베드 서버(로컬 127.0.0.1:port/ws)를 릴레이에 노출한다. 호스트가 릴레이로
**나가는** 연결을 유지하고, 릴레이가 보내는 각 손님 스트림(conn_id)을 로컬
임베드 서버의 /ws 연결로 파이프한다. 잠긴 망(카페·이중NAT)에서도 외부 접속 가능.

run_tunnel() 은 임베드 호스트의 asyncio 루프에서 task 로 돈다(끊기면 재연결).
"""
from __future__ import annotations

import asyncio
import json

import aiohttp


async def run_tunnel(relay_ws_base: str, session: str, local_port: int,
                     stop_event: "asyncio.Event | None" = None) -> None:
    host_url = relay_ws_base.rstrip("/") + "/relay/host?session=" + session
    local_url = f"ws://127.0.0.1:{local_port}/ws"
    backoff = 1.0

    while stop_event is None or not stop_event.is_set():
        try:
            async with aiohttp.ClientSession() as cs:
                async with cs.ws_connect(host_url, heartbeat=30, max_msg_size=0) as ws:
                    backoff = 1.0
                    conns: dict = {}   # cid -> aiohttp ClientWebSocketResponse(local)

                    async def pump_local(cid: int, lws):
                        try:
                            async for m in lws:
                                if m.type == aiohttp.WSMsgType.TEXT:
                                    await ws.send_str(json.dumps({"t": "data", "c": cid, "d": m.data}))
                                else:
                                    break
                        except Exception:
                            pass
                        finally:
                            conns.pop(cid, None)
                            try:
                                await ws.send_str(json.dumps({"t": "close", "c": cid}))
                            except Exception:
                                pass

                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        try:
                            f = json.loads(msg.data)
                        except Exception:
                            continue
                        t = f.get("t"); cid = f.get("c")
                        if t == "open":
                            try:
                                lws = await cs.ws_connect(local_url, max_msg_size=0)
                            except Exception:
                                try:
                                    await ws.send_str(json.dumps({"t": "close", "c": cid}))
                                except Exception:
                                    pass
                                continue
                            conns[cid] = lws
                            asyncio.ensure_future(pump_local(cid, lws))
                        elif t == "data":
                            lws = conns.get(cid)
                            if lws is not None:
                                try:
                                    await lws.send_str(f.get("d", ""))
                                except Exception:
                                    pass
                        elif t == "close":
                            lws = conns.pop(cid, None)
                            if lws is not None:
                                try:
                                    await lws.close()
                                except Exception:
                                    pass
        except Exception:
            pass
        if stop_event is not None and stop_event.is_set():
            break
        await asyncio.sleep(min(backoff, 10.0))
        backoff *= 2
