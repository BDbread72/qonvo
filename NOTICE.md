# Third-Party Notices

## Live cursors (`src/v/boards/whiteboard/cursor_layer.py`)
라이브 커서 글리프(화살표 포인터 + 이름표)는 **자작**으로 Qt(QPainter)에서 직접 그립니다.
외부 에셋을 포함하지 않으므로 별도 저작권 표시가 필요 없습니다.

참고: Ubuntu **Yaru** 커서 테마(https://github.com/ubuntu/yaru) 사용을 검토했으나,
OS용 xcursor 포맷이라 캔버스 렌더링에 부적합하여 채택하지 않았습니다. 만약 향후
Yaru 아트워크를 사용할 경우 다음 라이선스가 적용됩니다:
- 코드: **GPL-3.0**
- 아트워크: **CC-BY-SA 4.0** (저작자 표시 + 동일조건 변경허락)

## 주요 의존성 라이선스 (요약)
- PyQt6 — GPL-3.0 / 상용
- aiohttp — Apache-2.0
- google-genai, openai, anthropic SDK — 각 Apache-2.0 / MIT
- websocket-client — Apache-2.0
- cloudflared (선택, 번들 안 함) — Apache-2.0
