# beta-1.4.14 — 채팅 명령어 시스템 대확장 + 견고성

마인크래프트식 채팅 `/명령어`를 대폭 확장하고, 독립 감사·하드닝으로 다졌습니다.
협업 채팅창(또는 솔로에서 Enter)에서 `/`로 시작하면 명령입니다.

## ✨ 새 명령어

- **`/create <노드>{데이터태그}`** — 마크 `give item{nbt}`식. `/create chat{model:"...", name:"분석"}`.
  노드에 붙임·공백 뒤·위치 뒤 어디든. SNBT(숫자·문자열·bool·리스트·중첩 컴파운드).
- **`/delete` `/connect` `/run` `/grep`** — 노드 삭제(작업중 보호)·엣지 연결(포트 지정 + 타입
  인식)·실행 트리거(챗·버튼·all)·내용 검색.
- **`/function`** (마크 Java식) — 명령 시퀀스 매크로. `$` 매크로 줄 + `$(변수)` 치환 +
  `{인자}` 전달, `/return`(자기 함수만 종료), 재귀 가드. `/function edit` 로 **CodeMirror 에디터**.
- **`/execute if|unless <조건> run <명령>`** — 조건부 실행. 조건: `data`/`value`(노드값)/`node`/
  `online`·`cursor`(서버). `matches 10..`/비교. `store result data <키> run …`(계산→저장→분기).
- **`/data set/get/merge/remove/list`** — 자체 키-값 저장소(마크 `/data storage`풍). 점(.) 경로 탐색.
- **`/say` `/return`** 보조.

## 🛠 견고성 (감사 11건 + 하드닝 4배치)

- 한글 데이터키·함수명 지원, 노드 중복 이름은 `#id`로 지정.
- 콤마 누락·트레일링 입력 무음 손상 차단, `007`·`1e3`·`inf`/`nan` 방어, 중첩 깊이 가드(크래시 방지).
- 어떤 명령 오류도 앱을 죽이지 않음(함수 실행도 안 끊김), 영문 오류를 한글로 안내.
- 대량 AI 호출(`/run all`)·출력 도배·대형 보드 grep 폭주 캡.
- 권한: Visitor는 쓰기 명령 전부 차단(우회 불가), 읽기 허용.

## 📦 참고

- 함수 에디터(`/function edit`)는 **PyQt6-WebEngine**(CodeMirror)을 씁니다 → exe 크기 증가.
- 협업 서버(qonvo-server)는 변경 없음(명령/에디터는 클라이언트 전용).
