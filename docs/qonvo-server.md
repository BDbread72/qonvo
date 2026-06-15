# Qonvo Server (1.1.x)

마인크래프트 서버 모델의 헤드리스 협업 서버. 클라이언트(GUI)가
`ws://host:port/ws` 로 접속해 실시간으로 보드를 공유하고, AI 실행을
서버가 대행한다.

## 구성

```
src/server/
  __main__.py     진입점 (WMI 우회 + crash 로깅, 서브커맨드)
  config.py       config.toml 로드/기본생성
  auth.py         로컬 계정(pbkdf2) + 권한 레벨 + OAuth 토큰
  oauth.py        Mattermost(merri) OAuth2
  board_store.py  보드 영속화 (snapshot.json + oplog.jsonl + seq, delta sync)
  ai_runner.py    헤드리스 AI 실행 (ProviderRouter 재사용)
  session.py      세션 + presence
  app.py          aiohttp WS 서버 (라우팅/브로드캐스트/AI)
  console.py      운영 콘솔
```

## 실행

**Ubuntu/Linux (헤드리스 서버 권장):**
```bash
# PyQt6 없이 가벼운 서버 전용 의존성
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-server.txt    # aiohttp + google-genai + cryptography
# (UPnP는 stdlib만 사용 — 추가 설치 없음)

./qonvo-server.sh start                     # 서버 기동 + UPnP 자동개방 + 접속주소 출력
./qonvo-server.sh stop|restart|status|logs
```

**Windows / 일반:**
```bash
# 의존성
pip install -r requirements.txt          # aiohttp 포함

# 서버 실행 (헤드리스 콘솔)
qonvo-server.bat run                       # 또는: cd src && python -m server run

# 계정 관리
qonvo-server.bat adduser <user> <pass> [level]   # level: 0 Visitor / 1 Member / 2 Operator
qonvo-server.bat listusers
qonvo-server.bat config                    # config.toml 경로 출력/생성
```

데이터 경로: `%APPDATA%/Qonvo/server/`
- `config.toml`  — 서버 설정 (host/port/AI키/OAuth)
- `users.json`   — 로컬 계정 (pbkdf2 해시)
- `boards/<id>/` — `snapshot.json`(권위 문서) + `oplog.jsonl`(delta)

## 외부 접속 (포트포워딩 없이 — UPnP 자동개방)

서버는 시작 시 **UPnP로 라우터 포트를 스스로 개방**한다(`config.toml [network] upnp = true`).
사용자가 공유기 설정을 만지거나 cloudflared 같은 터널을 쓸 필요가 없다 —
"스팀 데디서버처럼 그냥 실행". 라우터가 UPnP를 막아둔 경우에만 자동으로 건너뛴다.

```toml
[network]
upnp = true                    # 시작 시 포트 자동개방 + 주기적 갱신 + 종료 시 해제
public_host = "home.4myway.uk" # 클라에게 안내할 주소(도메인/DDNS). 비우면 공인 IP 자동감지
upnp_lease = 3600
```

서버 콘솔/로그에 접속 주소가 출력된다:
```
  접속: Connect 창의 Host 칸에 입력
      home.4myway.uk
  Port: 9700
```

- **이중 NAT(통신사 공유망)**: 라우터가 사설 외부 IP를 보고해도, 실제 공인 IP는
  외부 echo 서비스로 자동 감지한다. 공인 IP가 상위 통신사 장비에 있으면 그 장비도
  열려야 하므로(드묾), 안 되면 cloudflared 폴백 또는 수동 포워딩을 쓴다.
- **UPnP 불가 라우터**: 폴백은 ⑴ 수동 포트포워딩(공유기에 9700 한 줄) 또는
  ⑵ cloudflared 터널(`cloudflared tunnel --url http://localhost:9700` → 공개 wss URL,
  클라 Host에 `wss://...` 붙여넣기). 클라는 `wss://` 스킴/`Secure(wss)` 체크박스를 지원한다.

## 접속 (클라이언트)

GUI 메뉴: **File → Connect to Server** (`Ctrl+Shift+C`)
→ Host(서버가 출력한 주소) / Port 9700 / Username / Password 입력 → 보드 선택/생성.
터널(`wss://`) 사용 시 Host에 전체 URL을 붙여넣으면 'Secure(wss)'가 자동 적용된다.

## 권한 레벨 (마인크래프트 BE 방식)

| 레벨 | 이름 | 권한 |
|---|---|---|
| 0 | Visitor | 읽기 전용 (op/AI 거부) |
| 1 | Member | 편집 + AI 실행 |
| 2 | Operator | 관리 |

`config.toml [server] default_level`, `allow_guests` 로 신규/게스트 정책 제어.
`config.toml [users] <name> = <level>` 로 레벨 오버라이드 가능.

## 계정 연동 (Mattermost / merri OAuth2)

`config.toml [oauth.mattermost]` 에서 `enabled = true` 후 `base_url`,
`client_id`, `client_secret` 설정. 흐름:

```
브라우저 → http://<server>:<port>/oauth/mattermost/login
  → Mattermost 인증 → /oauth/mattermost/callback
  → 1회용 토큰 발급 (10분 유효)
  → 토큰을 Connect 창의 Password 칸에 입력해 접속
```

`allowed_email_domain` 으로 특정 도메인만 허용 가능.
WS `auth` 는 비밀번호 또는 발급 토큰을 모두 받는다(`auth.Authenticator`).

## 프로토콜 (server_client.py 와 1:1)

| 방향 | 메시지 |
|---|---|
| S→C | `auth_required` |
| C→S | `auth {user, pass}` |
| S→C | `auth_ok {level, boards}` / `auth_fail {reason}` |
| C→S | `join_board {board_id, last_seq}` |
| S→C | `sync {snapshot, seq}` (전체) / `delta {ops, seq}` (증분) |
| C→S | `op {ops:[{op_id, op_type, target, data, timestamp}]}` |
| S→C | `op {ops, author, seq}` (타 멤버에게 브로드캐스트) |
| C→S | `ai_request {node_id, params:{model, message, files, system_prompt, options}}` |
| S→C | `ai_progress {node_id, chunk}` → `ai_complete {node_id, result}` |
| S→C | `user_join` / `user_leave` / `server_msg` / `error` |

지원 op_type: `node_add`, `node_remove`, `node_move`, `node_prop`,
`edge_add`, `edge_remove`, `chat_append`.

## 운영 콘솔 명령

`help / list / boards / say <msg> / kick <user> / op <user> / deop <user> /
adduser <u> <p> [lvl] / save / stop`

## 테스트

```bash
python tests/test_server.py     # 인증/join/op브로드캐스트/delta/권한/영속화 9개 체크
```

## 알려진 한계 (v1)

- AI 첨부파일(`files`)은 서버에 존재하는 경로만 처리. 클라 로컬 이미지
  업로드는 미지원(텍스트/이미지생성 모델은 정상).
- 권위 문서(snapshot)는 op 가 실어 나르는 정보만 반영 → 노드 세부
  상태 일부는 재접속 복원 시 기본값일 수 있음(채팅 history/위치/엣지는 OK).
- 인증은 평문 WS(`ws://`). 외부 노출 시 리버스 프록시로 TLS(`wss://`) 권장.
