"""라이브 커서 테스트용 봇 — 'bot' 사용자로 접속해 커서를 원형으로 움직인다.

PC 한 대로 멀티플레이(라이브 커서)를 확인하기 위함. 이 봇을 켜두고 qonvo 에서
같은 보드(lacs)에 접속하면, 캔버스 원점(0,0) 근처에 'bot' 커서가 도는 게 보인다.

  python tests/cursor_bot.py [board] [seconds]
"""
import asyncio
import json
import math
import sys

import aiohttp

try:  # 한국어 콘솔(cp949)에서도 출력 안 깨지게
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOST = "qonvo.4myway.uk"
PORT = 9700
USER, PW = "bot", "botpw"


async def main():
    board = sys.argv[1] if len(sys.argv) > 1 else "lacs"
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0
    url = f"ws://{HOST}:{PORT}/ws"
    async with aiohttp.ClientSession() as cs:
        async with cs.ws_connect(url, timeout=20, max_msg_size=64 * 1024 * 1024) as ws:
            await ws.receive()  # auth_required
            await ws.send_str(json.dumps({"type": "auth", "user": USER, "pass": PW}))
            ok = json.loads((await ws.receive()).data)
            if ok.get("type") != "auth_ok":
                print("auth failed:", ok)
                return
            await ws.send_str(json.dumps({"type": "join_board", "board_id": board, "last_seq": 0}))
            print(f"bot joined '{board}' — 원점(0,0) 근처에서 커서를 돌립니다 ({duration:.0f}s)")
            print("qonvo 에서 같은 보드 접속 후 '원점으로' (보기 리셋) 하면 보입니다.")

            t = 0.0
            R = 350.0
            try:
                while t < duration:
                    x = R * math.cos(t)
                    y = R * math.sin(t)
                    await ws.send_str(json.dumps({
                        "type": "presence", "ping": 30,
                        "cursor": {"x": round(x, 1), "y": round(y, 1)},
                    }))
                    await asyncio.sleep(0.1)
                    t += 0.15
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
    print("bot done")


if __name__ == "__main__":
    asyncio.run(main())
