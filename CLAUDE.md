# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
# Run the application
python src/main.py

# Build executable (PyInstaller) — see crack.bat for full flags
pyinstaller --onefile --noconsole --name qonvo --icon=icon.ico --distpath . \
  --add-data "lang;lang" --add-data "build.toml;." \
  --hidden-import "v.boards.whiteboard" ... ./src/main.py

# Run tests (standalone scripts, not pytest)
python tests/test_improvements.py
python tests/test_phase3_theme.py
python tests/test_phase4_caching.py

# Syntax check a single file
python -m py_compile src/v/boards/whiteboard/plugin.py
```

No `requirements.txt` or `pyproject.toml`. Key dependencies: **PyQt6**, **google-genai**, **cryptography**, Python 3.11+.

On Windows Korean locale, run `chcp 65001` first for UTF-8 console output.

## Architecture

### Application Lifecycle
```
main.py → App() → ui.run_app(app)
  → MainWindow → _show_welcome() or _load_plugin()
  → WhiteBoardPlugin.create_view() → WhiteboardView (QGraphicsView)
```

### Plugin System
**Board plugins**: `BoardPlugin` (ABC in `boards/base.py`) defines `create_view()`, `collect_data()`, `restore_data()`. The sole implementation is `WhiteBoardPlugin` in `boards/whiteboard/plugin.py` (~3000 lines). Plugin discovery scans `src/v/boards/` for modules exporting `PLUGIN_CLASS`.

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

### Node System (12 types)
All node widgets inherit `QWidget` + `BaseNode` mixin, wrapped in `NodeProxyWidget` (QGraphicsProxyWidget).

| Category | ID Key | Notes |
|----------|--------|-------|
| nodes, function_nodes, round_tables, repository_nodes, texts, group_frames | `id` | |
| sticky_notes, buttons, checklists, image_cards, dimensions | `node_id` | |

ID key mapping is definitive in `lazy_loader.py:ID_KEY_MAP`.

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
- **Boards**: `%APPDATA%/Qonvo/boards/*.qonvo`
- **Backups**: `%APPDATA%/Qonvo/backups/{name}_{timestamp}.qonvo.bak` (settings: `backup_enabled`, `backup_path`, `backup_count`)
- **Logs (Primary)**: `%APPDATA%/Qonvo/logs/qonvo.db` (SQLite, WAL mode)
- **Logs (Backup)**: `%APPDATA%/Qonvo/logs/qonvo.log` (rotating 5MB x 3)
- **Temp**: `%APPDATA%/Qonvo/boards/.temp/<board>/attachments/` (board-isolated)

## Critical Rules
- **Never clamp coordinates** — negative coords are normal on the infinite canvas
- **Always search before implementing** — duplicate code is a recurring issue
- **Always grep for actual method names** — don't guess (`_set_color` ≠ `_apply_color`)
- **Check both port patterns** — `output_port` (single) vs `output_ports` (dict)
- **ID key varies by node type** — some use `id`, others `node_id`

## Storage & Save Logic Rules (MANDATORY)

데이터 저장 관련 코드를 작성할 때 반드시 아래 규칙을 따를 것. 위반 시 데이터 유실 발생.

### 파일 저장 시
- **파일명은 항상 UUID 사용** — `uuid.uuid4().hex` 등. timestamp나 순번 사용 금지 (이름 충돌 → 덮어쓰기 → 데이터 유실)
- **보드별 temp 디렉토리 격리** — 모든 임시 파일은 `boards/.temp/{board_name}/attachments/`에 저장. 공유 폴더 (`dimension_images/`, `temp/` 등) 절대 사용 금지
- **상대 경로 해석 필수** — `attachments/xxx.png` 같은 상대 경로는 반드시 `_resolve_attachment()` 헬퍼로 temp 디렉토리에서 실제 파일 찾기. `Path(relative).exists()`는 항상 False 반환

### 백업
- **타임스탬프 기반 백업** — `%APPDATA%/Qonvo/backups/{name}_{YYYYMMDD_HHMMSS}.qonvo.bak`
- 최근 N개만 유지 (기본 5, settings.json `backup_count`로 설정)
- `backup_enabled: false`로 비활성화 가능, `backup_path`로 경로 변경 가능

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
