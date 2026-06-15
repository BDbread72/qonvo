# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
# Install deps
pip install -r requirements.txt

# Run GUI
python src/main.py

# Run CLI (headless)
python src/cli.py run --model gemini-2.0-flash "prompt"
python src/cli.py repl           # interactive REPL
python src/cli.py batch file.txt # batch from file

# Run the SERVER (qonvo-server — headless, no GUI/PyQt6 needed)
cd src && python -m server run          # 또는 루트에서: ./qonvo-server.sh  /  qonvo-server.bat
python -m server adduser <id> <pw> [lvl]   # lvl: 0 Visitor / 1 Member / 2 Operator
python -m server seed_board <file.qonvo> <board_id>   # 로컬 .qonvo → 서버 보드로 시딩

# Build executables — use bundled scripts
./crack.bat       # or crack.ps1 — builds qonvo.exe (GUI). REQUIRES websocket-client installed (--collect-all websocket)
# qonvo-cli.spec → pyinstaller qonvo-cli.spec for CLI binary

# Run tests (standalone scripts, not pytest)
python tests/test_improvements.py
python tests/test_server.py        # 서버 E2E: auth/join/op브로드캐스트/delta/권한/영속화 (9 checks)

# Syntax check a single file
python -m py_compile src/v/boards/whiteboard/plugin.py
```

Key dependencies (`requirements.txt`): **PyQt6**, **google-genai**, **cryptography**, **prompt-toolkit**, **rich**, **tomli**, **websocket-client**, **aiohttp**. Server-only 의존성은 `requirements-server.txt` (PyQt6 제외 — aiohttp/google-genai/cryptography). Python 3.11+ (3.14 호환 — `main.py`의 WMI 우회 참조).

On Windows Korean locale, run `chcp 65001` first for UTF-8 console output.

이 저장소는 두 개의 배포물을 만든다: **qonvo**(GUI 실행기, `qonvo.exe`)와 **qonvo-server**(헤드리스 협업 서버, `src/server/` + `qonvo-server-dist.zip`). 서버는 GUI 클라이언트(qonvo)와 동일한 provider/board 코드를 헤드리스로 재사용한다.

## Architecture

### Application Lifecycle
```
main.py → App() → ui.run_app(app)
  → MainWindow → _show_welcome() or _load_plugin()
  → WhiteBoardPlugin.create_view() → WhiteboardView (QGraphicsView)
```

### Plugin System
**Board plugins**: `BoardPlugin` (ABC in `boards/base.py`) defines `create_view()`, `collect_data()`, `restore_data()`. The primary implementation is `WhiteBoardPlugin` in `boards/whiteboard/plugin.py` (~980 lines after split). `dimension_board.py`는 sub-board 변형. Plugin discovery scans `src/v/boards/` for modules exporting `PLUGIN_CLASS`.

Whiteboard 모듈은 다음으로 분리됨:
```
plugin.py            ← WhiteBoardPlugin + NodeProxyWidget
_chat_send.py        ← 채팅 전송 헬퍼
_chat_workers.py     ← StreamWorker 관리
_materialization.py  ← lazy → live node 변환
_node_factory.py     ← 노드 생성 분기
_serialization.py    ← collect_data / restore_data
_server_mixin.py     ← ServerMixin: 서버모드 sync/op적용/첨부다운로드/이미지리프레시
server_client.py     ← ServerClient(WebSocket) + AttachmentDownloadThread (클라 측)
connect_dialog.py    ← ConnectDialog(host/port/wss) + BoardSelectDialog
```

**Model plugins** (`model_plugin.py`): `ModelPlugin` ABC → `configure()`, `chat()`. `PluginRegistry` auto-discovers plugins from `plugins/` directory. `ProviderRouter` transparently routes chat calls to the correct plugin.
- **Gemini** — built-in (`provider.py`), 9 models (text + image generation)
- **Anthropic** — `plugins/anthropic_plugin.py` (Claude models)
- **OpenAI** — `plugins/openai_plugin.py` (GPT models)

### Board Save/Load (.qonvo binary format)
`BoardManager` in `board.py` handles serialization:
- **Header** (24B): magic `QONVO`, version, entry count, data offset
- **TOC**: per-entry metadata (name, offset, size, compression)
- **Data body**: zlib-compressed JSON + raw attachments
- Streaming extraction (256KB chunks), seek-based random access
- Legacy ZIP format auto-detected and migrated

### Node System (23 categories)
All node widgets inherit `QWidget` + `BaseNode` mixin, wrapped in `NodeProxyWidget` (QGraphicsProxyWidget).

| ID Key | Categories |
|--------|------------|
| `id` | nodes, function_nodes, round_tables, repository_nodes, texts, group_frames |
| `node_id` | sticky_notes, buttons, checklists, image_cards, dimensions, prompt_nodes, markdown_nodes, nixi_nodes, ups_nodes, rmv_nodes, switch_nodes, latch_nodes, and_gates, or_gates, not_gates, xor_gates, bulb_nodes |

ID key mapping is definitive in `lazy_loader.py:ID_KEY_MAP` — 직접 나열하지 말고 그 상수를 참조할 것 (자주 추가됨).

`logic_nodes.py`: switch/latch + and/or/not/xor/bulb 게이트 (시그널 회로용).

### Port & Edge System
- `PortItem` — typed connection points (TYPE_BOOLEAN, TYPE_STRING, TYPE_FILE)
- `EdgeItem` — bezier curves connecting output→input ports
- INPUT ports allow single connection by default; `multi_connect=True` overrides (used by repository node)
- **Port caching**: `_cached_scene_pos` + `_cache_valid` for 90% scenePos() reduction
- **Event-driven**: `itemChange(ItemPositionHasChanged)` → `reposition_ports()` (no timer)

### Port Pattern Differences
```python
# Chat Node: single ports
node.output_port          # PortItem
node.signal_output_port   # PortItem

# Function Node: dict ports
node.output_ports["_default"]  # PortItem

# Always check both in _emit_complete_signal()
```

### Data Flow
```
Chat: user message → StreamWorker(QThread) → GeminiProvider.chat()
  → chunk signals → UI update → finished → _emit_complete_signal() → next node

Function: signal trigger → FunctionExecutionWorker(QThread)
  → walks internal graph (start→llm_call→condition→end) → result → next node

Signal: ButtonNode click → emit_signal(signal_output_port)
  → traverses edges → target.on_signal_input()
```

### Provider System (`provider.py`, `model_plugin.py`)
- `GeminiProvider` wraps Google Gemini API (built-in, 9 models including image generation)
- **Multi-key round robin**: `_get_client()` cycles through API keys with `threading.Lock()`
- **Streaming**: `_stream_with_signatures()` yields text chunks + thought_signatures
- `StreamWorker` (QThread) runs off main thread, emits Qt signals
- `ProviderRouter` → `PluginRegistry.get_plugin_for_model()` → routes to correct plugin or fallback to Gemini

### Lazy Loading
```python
# restore_data() flow:
_lazy_mgr.ingest_data(data)          # JSON → pending store with spatial index
_lazy_mgr.query_visible(viewport)    # Returns items in viewport
_materialize_items(visible)          # Creates only visible nodes
# Pan/zoom → schedule_check() → _materialize_visible_items()
# collect_data() merges materialized + pending (unseen) data
```

### Worker Queue
Max 4 concurrent API workers. Excess queued in `_pending_workers`, started as slots free.

### CLI (Headless 모드)
- `cli.py` — argparse 진입점, 서브커맨드 라우팅 (`run`, `repl`, `batch`)
- `cli_repl.py` — prompt_toolkit Application 기반 split 레이아웃 (상태 테이블 + 입력). rich.Table → StringIO → FormattedTextControl(ANSI)
- `cli_job_manager.py` — ThreadPoolExecutor + Lock으로 동시 실행 관리
- `cli_runner.py` — 배치/단건 실행 헬퍼
- REPL 명령어: `/model`, `/models`, `/workers`, `/show`, `/status`, `/system`, `/attach`, `/repeat N <prompt>`, `/bulk file.txt`, `/clear`, `/help`, `/quit`
- 이미지 첨부: `@path` 인라인 + `/attach` 세션 모드

### Qonvo Server (서버, `src/server/`)
헤드리스 협업 서버. 클라이언트(qonvo GUI)가 `ws://host:port/ws`로 접속해 실시간으로
같은 보드를 공유하고, AI 실행을 서버가 대행한다. **PyQt6 의존 없음** — 앱의 provider 스택
(`v.provider`/`v.model_plugin`/`v.settings`/`v.logger`, 전부 Qt-free)을 그대로 재사용한다.
문서: `docs/qonvo-server.md`. 진입점: `src/server/__main__.py` (CLI처럼 argparse, `run`/`adduser`/`listusers`/`config`/`seed_board`).

```
src/server/
  __main__.py    진입점 — main.py 와 동일한 WMI 우회 + crash 로깅 필수(aiohttp hang 방지)
  config.py      config.toml 로드/기본생성. 데이터루트 get_server_dir()
  auth.py        로컬계정(pbkdf2, users.json) + 레벨 + OAuth/HTTP 토큰. Authenticator
  oauth.py       Mattermost(merri) OAuth2 → 1회용 토큰 발급
  board_store.py Board(권위 doc + oplog + attachments) / BoardManager(lazy 캐시)
  ai_runner.py   build_router() + run_ai() — ProviderRouter 헤드리스 재사용(CLI와 동일)
  session.py     Session(연결) + Registry(보드별 멤버십·브로드캐스트)
  app.py         QonvoServer — aiohttp WS 라우팅 + 첨부 HTTP + UPnP 수명주기
  console.py     운영 콘솔(list/say/kick/op/save/stop), tty일 때만 기동
  upnp.py        UPnP IGD 자동 포트개방(stdlib only)
  seed_board.py  .qonvo → 서버 보드 변환(board.py 추출은 Qt-free라 서버에서 동작)
```

**권위 모델 (NOT CRDT)**: `Board`가 restore_data 포맷의 JSON `doc`을 들고, 들어온 op를
적용해 갱신한다. seq 번호가 붙은 append-only `oplog.jsonl`로 delta sync. join 시 `last_seq`
기준으로 full `sync`(전체 doc) 또는 `delta`(이후 op만) 전송. op는 sender 제외 보드 멤버에게 브로드캐스트.

**프로토콜 (server_client.py 와 1:1)**:
```
S→C auth_required → C→S auth{user,pass} → S→C auth_ok{level,boards,http_token} | auth_fail{reason}
C→S join_board{board_id,last_seq} → S→C sync{snapshot,seq} | delta{ops,seq}
C→S op{ops:[{op_id,op_type,target,data,ts}]} → S→C op{ops,author,seq} (타 멤버에게)
C→S ai_request{node_id,params} → S→C ai_progress{node_id,chunk}* → ai_complete{node_id,result}
S→C user_join/user_leave/server_msg/error
```
op_type: node_add/remove/move/prop, edge_add/remove, chat_append.

**권한 (Minecraft BE 방식)**: Visitor(0) 읽기전용(op/AI 거부) / Member(1) 편집+AI / Operator(2) 관리.
`config.toml [server] default_level/allow_guests`, `[users] <name>=<level>` 오버라이드.

**외부 접속 — 포트포워딩 없이 (UPnP)**: 서버 시작 시 `upnp.py`가 라우터(IGD)에 포트개방을
요청하고 주기 갱신, 종료 시 해제(`config.toml [network] upnp/public_host/upnp_lease`). 라우터가
UPnP 미지원이면 자동 건너뜀(수동 포워딩/cloudflared 폴백). `public_host`는 클라에 안내할 주소.

**첨부(이미지) 전송**: 서버 보드도 `attachments/<uuid>.ext` 상대경로를 쓴다. 서버는 첨부를
`boards/<id>/attachments/`에 저장하고 HTTP로 제공 — `GET/PUT /board/{id}/attach/{name}`,
`GET /board/{id}/manifest`. 보호는 auth_ok에서 발급되는 `http_token`(WS 세션 수명, 끊으면 폐기,
쿼리 `?t=`). 클라(`_server_mixin`+`AttachmentDownloadThread`)는 join 후 **즉시 restore →
백그라운드로 첨부 다운로드 → 완료 시 이미지 노드 리프레시**. AI 생성 이미지는 라이브는 base64로
즉시 표시 + 서버가 파일로 영속(`_persist_ai_images`).

**클라이언트 측(GUI 앱 내)**: `server_client.py`(ServerClient/_WebSocketThread/AttachmentDownloadThread),
`_server_mixin.py`(ServerMixin), `connect_dialog.py`, `ui.py`의 `_connect_to_server`/`_enter_server_mode` glue.
`compose_ws_url()`가 host/port/secure로 ws/wss URL을 만든다(터널·도메인은 wss).

**배포**: `qonvo-server.sh`/`qonvo-server.bat`는 자가설치(첫 실행 시 venv+pip 자동). 마인크래프트
`.jar` 방식 — Python만 있으면 받아서 실행. `qonvo-server-dist.zip`로 묶어 배포.

## Key Patterns

```python
# Theme (35+ color constants)
from v.theme import Theme
widget.setStyleSheet(f"background-color: {Theme.BG_PRIMARY};")

# Localization
from q import t
label.setText(t("menu.file"))  # KR.toml/EN.toml lookup

# Encryption (machine-specific, not portable)
from v.crypto_utils import encrypt_api_key, decrypt_api_key

# Settings (JSON, memory-cached with mtime invalidation)
from v.settings import get_setting, set_setting, get_api_keys
# Stored at %APPDATA%/Qonvo/settings.json

# Snap engine: CapsLock ON = free placement, OFF = PPT-style alignment (8px threshold)
```

## Data Storage (Windows)
- **Settings**: `%APPDATA%/Qonvo/settings.json`
- **Boards**: `%APPDATA%/Qonvo/boards/*.qonvo` (원자적 저장: `.qonvo.tmp` → `replace`)
- **Logs (Primary)**: `%APPDATA%/Qonvo/logs/qonvo.db` (SQLite, WAL mode)
- **Logs (Backup)**: `%APPDATA%/Qonvo/logs/qonvo.log` (rotating 5MB x 3)
- **Crash**: `%APPDATA%/Qonvo/logs/crash.log` (faulthandler + sys.excepthook + threading.excepthook + sys.unraisablehook)
- **Temp**: `%APPDATA%/Qonvo/boards/.temp/<board>/attachments/` (board-isolated)
- **서버 데이터**: `%APPDATA%/Qonvo/server/` (Linux: `~/.config/Qonvo/server/`) — `config.toml`, `users.json`(pbkdf2), `boards/<id>/{snapshot.json, oplog.jsonl, attachments/}`
- **서버 모드 클라 첨부**: 서버 보드 접속 시 첨부는 `boards/.temp/<board_id>/attachments/`로 다운로드됨 (board_name = 서버의 board_id)

## Critical Rules
- **Never clamp coordinates** — negative coords are normal on the infinite canvas
- **Always search before implementing** — duplicate code is a recurring issue
- **Always grep for actual method names** — don't guess (`_set_color` ≠ `_apply_color`)
- **Check both port patterns** — `output_port` (single) vs `output_ports` (dict)
- **ID key varies by node type** — some use `id`, others `node_id`

## Gotchas (지뢰)
- **`main.py`의 WMI 우회**: Windows + Python 3.14에서 `aiohttp` 등이 `platform.win32_ver()` 호출 시 WMI 쿼리로 들어가 hang 발생. `_platform._wmi_query`를 OSError로 강제 실패시키고 `win32_ver`를 lambda로 prewarm함. **이 패치 지우면 일부 환경에서 앱이 통째로 멈춤.**
- **`--noconsole` 빌드**: GUI는 stderr가 안 보이므로 `sys.excepthook` + `threading.excepthook` + `sys.unraisablehook` 모두 등록되어 있음. crash.log 비어있으면 segfault 아닌 Python 예외 → DB 로그 확인
- **PyQt6 시그널 핸들러의 unhandled exception = 앱 즉사**. `_on_image_payload` 같은 슬롯은 반드시 try/except 방어

### 서버(qonvo-server) 지뢰
- **`ui.py`의 서버 glue는 `self.current_plugin`을 쓸 것** (`self.plugin`은 존재하지 않는 속성 — 과거 이 오타로 `set_server_client()`가 안 걸려 서버 sync가 통째로 무시됐음)
- **`_on_server_sync`는 restore 먼저, 첨부 다운로드는 나중**. 다운로드를 먼저 하면 대형 보드(lacs 등)가 다 받을 때까지 빈 화면이 됨
- **클라 첨부 해석은 basename 탐색** (`chat_node._render_page`, `ImageCardItem._start_load`가 `_board_temp_dir` 하위에서 basename 매칭) → 상대경로 `attachments/xxx`를 절대경로로 안 바꿔도 됨. 파일만 `temp/<board>/attachments/`에 있으면 됨
- **qonvo.exe 빌드**: `websocket-client`가 pip 설치돼 있어야 하고 `crack.bat`에 `--collect-all websocket` 필요(없으면 런타임에 "websocket-client not installed"). **실행 중인 qonvo.exe가 파일을 잠그면** 빌드가 PermissionError(단, echo로 exit 0 오인 가능) → 빌드 전 `Get-Process qonvo` 확인
- **`__main__.py`에 main.py와 동일한 WMI 우회 필수** (aiohttp가 Win/Py3.14에서 hang)
- **ssh로 `pkill -f "패턴"`**: 패턴이 ssh 명령줄 자신에 있으면 자기 셸을 죽임(exit 255). `[c]loudflared` 브래킷 트릭 쓰거나 스크립트 내부 stop 사용
- **`http_token`은 WS 세션 수명**: 연결이 끊기면 폐기되므로 첨부 다운로드/업로드는 접속 유지 중에만 동작

## Storage & Save Logic Rules (MANDATORY)

데이터 저장 관련 코드를 작성할 때 반드시 아래 규칙을 따를 것. 위반 시 데이터 유실 발생.

### 파일 저장 시
- **파일명은 항상 UUID 사용** — `uuid.uuid4().hex` 등. timestamp나 순번 사용 금지 (이름 충돌 → 덮어쓰기 → 데이터 유실)
- **보드별 temp 디렉토리 격리** — 모든 임시 파일은 `boards/.temp/{board_name}/attachments/`에 저장. 공유 폴더 (`dimension_images/`, `temp/` 등) 절대 사용 금지
- **상대 경로 해석 필수** — `attachments/xxx.png` 같은 상대 경로는 반드시 `_resolve_attachment()` 헬퍼로 temp 디렉토리에서 실제 파일 찾기. `Path(relative).exists()`는 항상 False 반환

### 백업 금지
- **`.qonvo` 보드 파일은 백업을 만들지 않는다.** 저장은 `.qonvo.tmp` → `os.replace`로 원자적임 → 별도 백업 불필요
- `shutil.copy2(filepath, ...)`로 보드 파일 복사본을 만드는 코드를 재도입하지 말 것
- 사용자 실수 복구는 앱 내부 undo로 처리

### 로깅
- **저장/로드 모든 단계에 로그 기록** — 파일 매핑, 경로 해석, 무결성 검증 등
- **로그는 SQLite DB 사용** — 파일 로그는 백업용. DB가 주 저장소 (`qonvo.db`, WAL 모드)
- **저장 후 무결성 검증** — board.json의 모든 attachment 참조가 실제 archive에 존재하는지 확인 로그

### get_data() / _materialize 체크리스트
- 새 필드를 `get_data()`에 추가하면 → `_materialize`에서 복원 코드도 반드시 추가
- UI 위젯 상태 (setChecked, setValue, setEnabled)는 명시적 복원. 시그널 부수효과에 의존하지 말 것

## Versioning (버전 관리)

### 형식: `beta-X.Y.Z`
- **X** (major): 대규모 변경, 호환성 변경. main 브랜치 = X.0.0만 보관
- **Y** (minor): 새 기능 추가. main에서 `Y` 브랜치 생성 (예: `1.0`, `1.1`)
- **Z** (patch): 버그 수정, 소규모 개선. minor 브랜치에서 작업
- `beta-` 접두사 유지

### 브랜치 전략
```
main              ← X.0.0 릴리스만 (beta-1.0.0, beta-2.0.0, ...)
 ├── 1.0          ← 1.0.x 패치 라인
 ├── 1.1          ← 1.1.x 기능 라인 (서버 모드 등)
 └── 2.0          ← (미래) 2.0.x
```

- **main**: X.0.0 태그가 찍힌 안정 버전만. 직접 커밋 금지
- **Y 브랜치** (1.0, 1.1, ...): minor 버전 작업 브랜치. main에서 분기
- 작업은 항상 해당 minor 브랜치에서 수행
- 버전은 `build.toml`의 `[app] version` 필드로 관리
- 태그: `beta-X.Y.Z` (릴리스 시)

### 버전 변경 시
1. `build.toml`의 version 업데이트
2. 커밋 + 태그
3. push (브랜치 + 태그)

## Command Interpretation (사용자 명령 해석)

사용자의 자연어 명령을 아래 규칙에 따라 해석하고 실행할 것.

| 사용자 표현 | 해석 | 실행 |
|------------|------|------|
| "작업 확인", "뭐 했는지 봐", "변경사항 확인" | 현재 작업 내용 리뷰 요청 | `git diff` + `git status`로 변경사항 보여주기 |
