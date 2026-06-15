# Qonvo 서버 — 받아서 실행하기 (마인크래프트 서버 방식)

친구들과 같은 화이트보드를 실시간으로 공유하는 Qonvo 데디케이티드 서버입니다.
**포트포워딩·복잡한 설정 없이**, 받아서 한 번 실행하면 됩니다.

## 필요한 것
- **Python 3.11 이상** (마인크래프트의 Java 같은 존재). 한 번만 깔면 됩니다.
  - Windows: https://python.org 에서 설치 (설치 시 "Add Python to PATH" 체크)
  - Ubuntu/Linux: `sudo apt install python3 python3-venv`

## 실행 (딸깍)

**Windows**
```
qonvo-server.bat
```
더블클릭하거나 위 명령 실행. 처음 한 번은 자동으로 필요한 걸 깔고(30초~1분), 그 뒤엔 바로 켜집니다.

**Ubuntu / Linux / Mac**
```
./qonvo-server.sh
```

켜지면 콘솔에 **접속 주소**가 뜹니다:
```
==================================================
  Qonvo Server 가동 중
  접속: Connect 창의 Host 칸에 입력
      123.45.67.89        (당신 서버의 공인 IP 또는 도메인)
  Port: 9700
==================================================
```
이 주소를 친구에게 알려주면, 친구는 Qonvo에서 **File → Connect to Server**로 접속합니다.

## 계정 만들기
서버에 로그인할 계정을 먼저 만드세요(레벨: 0=관전 1=일반 2=운영자):
```
# Windows
qonvo-server.bat adduser 내아이디 비밀번호 2
# Linux
./qonvo-server.sh adduser 내아이디 비밀번호 2
```

## 명령
```
start     켜기 (기본값 — 인자 없이 실행하면 start)
stop      끄기
restart   재시작
status    상태/접속주소 보기
logs      로그 보기
adduser <id> <pw> [level]   계정 추가
```

## 외부 접속이 안 될 때
서버는 시작할 때 **UPnP로 공유기 포트를 자동으로 엽니다.** 대부분 잘 되지만,
공유기에서 UPnP가 꺼져 있으면 안 될 수 있어요:

1. 공유기 설정에서 **UPnP 켜기** (보통 기본 켜짐) 후 서버 재시작, 또는
2. 공유기에서 **9700 포트 수동 포워딩**, 또는
3. 공인 IP가 없다면(이중 NAT/통신사망) `cloudflared` 터널 사용:
   ```
   cloudflared tunnel --url http://localhost:9700
   ```
   출력된 `https://xxx.trycloudflare.com` 의 `https`를 `wss`로 바꿔
   친구의 Host 칸에 붙여넣게 하세요(Secure 자동 적용).

## AI 기능 (선택)
서버가 AI 응답을 대신 실행하게 하려면 `config.toml`에 Gemini 키를 넣으세요.
파일 위치는 서버 첫 실행 후 `config.toml 경로`가 콘솔에 안내됩니다
(보통 `~/.config/Qonvo/server/config.toml` 또는 `%APPDATA%\Qonvo\server\config.toml`).

## 설정 파일 (config.toml)
```toml
[server]
port = 9700
default_level = 1        # 신규/게스트 기본 권한
allow_guests = false

[network]
upnp = true              # 포트 자동개방
public_host = ""         # 도메인/DDNS 있으면 여기에 (예: home.example.com)

[ai]
gemini_keys = ["..."]    # AI 쓸 때만
```
