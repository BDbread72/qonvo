# 2026-02-25 작업 사항

## 1. Prompt Node 기능 개선

### 변경 파일
- `src/v/boards/whiteboard/prompt_node.py`
- `src/v/boards/whiteboard/chat_node.py`
- `src/v/boards/whiteboard/plugin.py`

### 추가된 기능

#### Role Selector (역할 선택)
- 헤더에 QComboBox: S(System) / U(User) / A(Assistant)
- system → 기존 동작 (effective_system_prompt에 병합)
- user/assistant → ChatMessage로 messages 앞에 prefix 삽입
- 배지 텍스트 역할에 따라 변경 (SP/UP/AP)

#### Enable/Disable 토글
- 헤더에 checkable QPushButton (ON/OFF)
- 비활성 시 body_edit 텍스트 색상 dim (#666)
- `_collect_all_inputs()`에서 `prompt_enabled=False`면 skip

#### Priority 순서
- 헤더에 QSpinBox (0~99, 낮을수록 먼저)
- `_handle_chat_send()`에서 priority 기준 정렬 후 처리

#### 글자수 카운터
- body_edit 하단에 `N자` 라벨, textChanged마다 실시간 업데이트

### 데이터 흐름 변경
- **Before**: `prompt_texts: List[str]` → `"\n\n".join()` → system_prompt 병합
- **After**: `prompt_entries: List[Dict]` → priority 정렬 → role별 분리 처리
  - system entries → effective_system_prompt에 병합
  - user/assistant entries → `ChatMessage(role=..., content=...)` 리스트로 messages 앞에 삽입

### 하위 호환성
- 기존 저장 파일: `.get("role", "system")`, `.get("enabled", True)`, `.get("priority", 0)` 기본값 사용
- `_materialize_prompt_node()`에서 role, enabled, priority 복원 추가

---

## 2. 이미지 카드 저장 유실 버그 수정

### 변경 파일
- `src/v/board.py`
- `src/v/boards/whiteboard/plugin.py`

### 근본 원인
`collect_data()`에서 lazy_mgr의 pending 데이터를 `data["image_cards"].extend(rows)`로 추가할 때, dict 객체가 **같은 참조**로 전달됨. 이후 `board.py`의 save()가 `card['image_path']`를 archive 내부 이름으로 덮어쓰면, **lazy_mgr 내부의 pending dict까지 변형**됨.

```
Save #1: pending.image_path = "C:\...\94eae290.png" (절대경로)
         → board.py가 "attachments/ee75d8d5.png"으로 덮어씀
         → lazy_mgr의 pending dict도 같이 변형!

Save #2: pending.image_path = "attachments/ee75d8d5.png" (archive 내부 이름)
         → temp에 없음 → UNRESOLVABLE → 영구 유실
```

### 수정 내용

| 위치 | 수정 | 효과 |
|------|------|------|
| `plugin.py:2137` | `copy.deepcopy(rows)` | save 시 원본 pending 변형 방지 (근본 원인 해결) |
| `board.py:398` | `fpath.replace('\\', '/')` 정규화 | Windows 백슬래시 경로 resolve 실패 방지 |
| `board.py:737` | 동일 정규화 (load 쪽) | load 시에도 경로 정규화 |
| `board.py:467` | `card['image_path'] = ''` | 유령 참조 제거 (캐스케이딩 실패 차단) |
| `board.py:482` | 동일 처리 (차원 내부) | 차원 image_card도 동일 보호 |

### 로그 증거
- node 66, 67, 68, 69, 75: 2/24~2/25 동안 매 로드/저장마다 반복 실패
- archive TOC 확인 → 해당 5개 파일 존재하지 않음 (이미 유실, 복구 불가)

---

## 3. StreamWorker `__error__` 미처리 버그 수정

### 변경 파일
- `src/v/boards/whiteboard/widgets.py`

### 증상
Preferred Options 모드에서 "생성 중... (0/4)"가 몇 분간 지속되며 멈춤.

### 원인
`provider.py`의 `_stream_with_signatures()`가 스트리밍 오류 시 `{"__error__": str(error)}`를 yield하지만, `StreamWorker.run()`의 for 루프에서 이 케이스를 처리하는 코드가 없었음.

결과: 에러가 조용히 무시 → `error_signal` 미emit → `_on_chat_pref_error()` 미호출 → `_preferred_results` 카운트 증가 안 됨 → 영구 멈춤.

### 수정
```python
elif isinstance(chunk, dict) and "__error__" in chunk:  # 스트리밍 오류 감지
    self.error_signal.emit(chunk["__error__"])
    return
```

---

## 4. Chat Node 요청 큐 기능 추가

### 변경 파일
- `src/v/boards/whiteboard/chat_node.py`

### 변경 전 동작
`_send()` 진입 시 `_running=True`이면 `return` → 요청 무시됨.
Signal input도 `_running=False`로 강제 리셋 후 전송.

### 변경 후 동작
`_running=True`일 때 새 요청이 오면 현재 상태(모델, 옵션, 프롬프트 등)를 스냅샷하여 `_send_queue`에 적재.
응답 완료 시 `QTimer.singleShot(0, _process_queue)`로 다음 요청을 FIFO 처리.

### 구현 내용

| 위치 | 변경 | 효과 |
|------|------|------|
| `__init__` | `self._send_queue = []` | 대기 큐 초기화 |
| `_send()` | `_running`일 때 스냅샷→큐 적재→return | 요청 유실 방지 |
| `_process_queue()` | 큐에서 pop(0)→상태 복원→on_send 호출 | FIFO 순차 처리 |
| `set_response(done=True)` | 큐 체크→singleShot 디큐 | 텍스트 응답 완료 후 다음 처리 |
| `show_preferred_results()` | 동일 | preferred 완료 후 다음 처리 |
| `set_image_response()` | 동일 | 이미지 완료 후 다음 처리 |
| `on_signal_input()` | `_running=False` 제거 | signal도 큐를 통해 순차 처리 |

---

## 5. 저장 시 참조 변형 방지 (릴리스 전 감사)

### 문제
`board.py`의 save 로직이 노드 데이터의 파일 경로를 archive 내부 이름으로 덮어쓰는데, `get_data()`가 내부 상태를 직접 참조로 반환하면 **in-memory 상태가 오염**됨.

### 감사 결과

| 파일 | 위험도 | 문제 | 조치 |
|------|--------|------|------|
| `chat_node.py` | CRITICAL | `history` shallow copy → 내부 images 변형 | `copy.deepcopy(self._history)` |
| `chat_node.py` | MEDIUM | `extra_input_defs` dict 리스트 shallow copy | `copy.deepcopy()` |
| `round_table.py` | CRITICAL | `participants`, `conversation_log` 직접 참조 반환 | `copy.deepcopy()` |
| `dimension_item.py` | - | 이미 `copy.deepcopy` 적용됨 | 변경 없음 |
| 나머지 노드 | - | 원시값/문자열만 반환 | 변경 없음 |
