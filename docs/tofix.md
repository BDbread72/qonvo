# Qonvo 프로젝트 전체 버그/오류 분석 리포트

## I. PyInstaller 빌드 (crack.bat) 문제

### CRITICAL - 빌드 실패/런타임 크래시

| # | 문제 | 상세 |
|---|------|------|
| B1 | **hidden-import 9개 누락** | `dimension_item`, `function_node`, `sticky_note`, `button_node`, `round_table`, `checklist`, `repository_node`, `function_library`, `function_editor` — exe 실행 시 해당 노드 타입 전부 로드 불가 |
| B2 | **build.toml 경로 오류** | `ui.py:24` — `sys.executable.parent` 사용. `sys._MEIPASS` 로 변경 필요. exe 위치 옮기면 버전 표시 실패 |
| B3 | **plugins/ 미번들** | `--add-data "plugins;plugins"` 누락 → OpenAI/Anthropic 플러그인 사용 불가 |
| B4 | **icons/ 미번들** | `--add-data "icons;icons"` 누락 → icons.toml 커스텀 아이콘 무시 (하드코딩 기본값으로 폴백) |

---

## II. 데이터 저장/로드 (board.py, settings.py)

### CRITICAL

| # | 파일 | 문제 |
|---|------|------|
| D1 | `board.py:354-365` | **백업 로테이션 부분 실패** — `.backup2→.backup3` rename 실패 시 `.backup3` 이미 삭제된 상태로 세대 유실 |
| D2 | `board.py:624-634` | **Windows 원자적 swap 실패** — `temp_dir.rename()` 실패 + `rmtree` 실패 시 `staging_dir.rename()` 불가 → 로드 실패 |
| D3 | `settings.py:69-70` | **비원자적 저장** — 쓰기 중 크래시 시 JSON 파손 → 모든 설정 유실 (`batch_queue.py`는 원자적인데 settings는 아님) |
| D4 | `settings.py:370-400` | **API 키 마이그레이션 실패 위험** — `del data["api_key"]` 후 `_save_all()` 실패 시 평문/암호화 키 모두 유실 |
| D5 | `board.py:379-408` | **보드 전환 시 attachment 경로 해석 실패** — 보드 A 로드 → 보드 B 전환 → 보드 A 저장 시 temp_dir 불일치로 첨부파일 유실 |

### HIGH

| # | 파일 | 문제 |
|---|------|------|
| D6 | `settings.py:30-75` | **캐시 스레드 안전성 없음** — `_settings_cache`/`_cache_mtime` 글로벌 변수에 락 없음 |
| D7 | `settings.py:42-46` | **mtime 정밀도 한계** — NTFS에서 같은 밀리초 내 2회 쓰기 시 캐시 무효화 불가 |
| D8 | `settings.py:176-182` | **복호화 실패 무시** — 모든 키 복호화 실패 시 빈 배열 반환, 사용자에게 키 손상 알림 없음 |
| D9 | `board.py:723-741` | **`fetch_default()` 예외 미처리** — URL/네트워크/디스크 에러 시 앱 크래시 |

---

## III. Worker 관리 & 동시성

### HIGH

| # | 파일:라인 | 문제 |
|---|-----------|------|
| W1 | `plugin.py:1935-1957` | **`_finish_worker` 중복 호출** — `error_signal`과 `all_finished` 둘 다 fire되면 `_active_workers` 이중 감소 → 대기열 워커 실행 불가 |
| W2 | `batch_queue.py:36-59` | **load/save 사이 TOCTOU 레이스** — 락이 전체 save를 커버하지 않음, 동시 쓰기 시 작업 유실 |
| W3 | `batch_queue.py:67-88` | **stale 정리 후 save 실패 무시** — cleanup_stale() 성공 반환하지만 디스크에는 stale job 잔존 |

---

## IV. Provider/API 관련

### HIGH

| # | 파일:라인 | 문제 |
|---|-----------|------|
| P1 | `provider.py:186-197` | **잘못된 API 키 클라이언트 캐시** — 키 무효 시 `genai.Client()` 실패 but 캐시에 잔류, 이후 요청 계속 실패 |
| P2 | `provider.py:748-779` | **스트리밍 중 네트워크 오류 시 usage/signatures 유실** — generator 종료 시 메타데이터 yield 안됨 |
| P3 | `provider.py:994-997` | **배치 폴링 실패 무기록** — `RuntimeError` 시 None 반환만, 로그 없음 → 배치 결과 영구 유실 |
| P4 | `model_plugin.py:162-168` | **플러그인 레지스트리 불일치** — `configure()` 예외 시 `_model_to_plugin`에 등록되지만 `_plugins`에는 없음 → Gemini 폴백 (잘못된 프로바이더) |

---

## V. Blueprint 함수 시스템

### CRITICAL

| # | 파일:라인 | 문제 |
|---|-----------|------|
| F1 | `function_engine.py:392-402` | **Sequence 노드가 항상 None 반환** — 모든 분기 실행 후 메인 체인 종료됨. 후속 노드로 이어지지 않음 |
| F2 | `function_engine.py:264-282` | **데이터 핀 순환 참조 감지 없음** — Pure 노드 사이 순환 의존 시 무한 재귀 → 스택 오버플로우 |

### HIGH

| # | 파일:라인 | 문제 |
|---|-----------|------|
| F3 | `function_engine.py:350-362` | **루프 내 MAX_TOTAL_STEPS 우회 가능** — 루프 본체 실행 후 step count 재검사 없이 다음 반복 시작 |
| F4 | `function_types.py:44-50` | **데이터 타입 변환 누락** — ARRAY↔STRING, OBJECT↔STRING, BOOLEAN→NUMBER 변환 미지원 |
| F5 | `function_engine.py:474-484` | **LLM 에러 무음 처리** — `error_signal` 미발신, "[LLM Error]"가 정상 응답처럼 다음 노드에 전달 |
| F6 | `function_engine.py:404-426` | **sub-chain 예외 미처리** — `_execute_exec_chain()` 내 예외 발생 시 QThread 워커 크래시 |

---

## VI. UI/UX 버그

### CRITICAL

| # | 파일:라인 | 문제 |
|---|-----------|------|
| U1 | `preferred_dialog.py:248` | **closeEvent 레이스** — `_confirmed` 속성 초기화 전 창 닫힘 시 AttributeError |
| U2 | `chat_node.py:703` | **부모 노드 삭제 후 로그 창 콜백** — `destroyed.connect(lambda: setattr(self, ...))` 에서 삭제된 self 접근 |

### HIGH

| # | 파일:라인 | 문제 |
|---|-----------|------|
| U3 | `plugin.py:878-882` | **`delete_proxy_item()`에서 `proxy.widget()` null 미확인** → 크래시 |
| U4 | `plugin.py:970-1000` | **`_emit_complete_signal`에서 `target_port.parent_proxy` None 미확인** → 삭제된 노드에 시그널 전달 시 크래시 |
| U5 | `plugin.py:2662-2669` | **ButtonNode 크기(width/height) 미복원** — 저장 후 재로드 시 기본 크기로 리셋 |
| U6 | `settings_dialog.py:378` | **API 키 마스킹 불충분** — 16자 이하 키는 전체 노출 |

### MEDIUM

| # | 파일:라인 | 문제 |
|---|-----------|------|
| U7 | `plugin.py:176` | **dimension_windows 메모리 누수** — 닫은 창의 참조가 리스트에 잔류 |
| U8 | `items.py:1131-1134` | **그룹 이동 시 포트 위치 갱신 누락** — `_group_moving=True`로 포트 리포지션 건너뜀 |
| U9 | `view.py:196-212` | **테마 변경 시 포트 색상 미갱신** — 그리드만 갱신, 포트 색상은 이전 테마 유지 |
| U10 | `ui.py:254-267` | **보드 로드 실패 시 메뉴 비활성 고착** — 비활성화된 액션이 다시 활성화되지 않음 |

---

## VII. 의존성/호환성

| # | 문제 | 상세 |
|---|------|------|
| C1 | **`function_engine.py`에 `from __future__ import annotations` 누락** | `str \| None` 33회 사용, Python 3.9에서 SyntaxError (3.11+ 전제이므로 동작하지만 방어적 코딩 미흡) |
| C2 | **tomllib 폴백 불일치** | `icon_manager.py`만 `tomli` 폴백 있음, `q/__init__.py`, `board.py`, `ui.py`는 없음 |
| C3 | **requirements.txt 부재** | PyQt6, google-genai, cryptography 버전 명시 없음 → 설치 재현 불가 |

---

## 심각도별 요약

| 심각도 | 빌드 | 데이터 | Worker | API | 함수엔진 | UI | 의존성 | **합계** |
|--------|-------|--------|--------|-----|----------|-----|--------|---------|
| **CRITICAL** | 4 | 5 | 0 | 0 | 2 | 2 | 0 | **13** |
| **HIGH** | 0 | 4 | 3 | 4 | 4 | 4 | 0 | **19** |
| **MEDIUM** | 0 | 0 | 0 | 0 | 0 | 4 | 3 | **7** |
| **합계** | **4** | **9** | **3** | **4** | **6** | **10** | **3** | **39** |

---

## 최우선 수정 대상 (Top 10)

1. **B1** — crack.bat에 hidden-import 9개 추가 (exe 빌드 필수)
2. **D3** — settings.py 원자적 저장 (tmp→replace) 적용
3. **F1** — Sequence 노드 실행 후 체인 계속되도록 수정
4. **F2** — 데이터 핀 순환 참조 감지 (visited set) 추가
5. **W1** — _finish_worker 중복 호출 방지 (`_finished` 플래그)
6. **D1** — 백업 로테이션을 try/except로 각 단계 보호
7. **U4** — `_emit_complete_signal`에 parent_proxy None 체크 추가
8. **B2** — build.toml 경로를 `sys._MEIPASS`로 수정
9. **P4** — 플러그인 레지스트리 `configure()` 실패 시 `_model_to_plugin` 등록 방지
10. **D4** — API 키 마이그레이션: 암호화 저장 확인 후에만 평문 삭제
