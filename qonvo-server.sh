#!/usr/bin/env bash
# Qonvo 데디케이티드 서버 (마인크래프트 .jar 방식)
#
#   ./qonvo-server.sh           # = start. 첫 실행 시 자동 설치 후 기동
#   ./qonvo-server.sh start|stop|restart|status|logs
#   ./qonvo-server.sh adduser <id> <pw> [level]
#
# 필요 조건: Python 3.11+ 설치 (마크의 Java 처럼). 그 외엔 알아서 깔린다.
# 외부 접속은 서버가 UPnP로 라우터 포트를 스스로 열어 처리(포트포워딩/터널 불필요).
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/src"
VENV="$HERE/.venv"
PYBIN="$VENV/bin/python"
REQ="$HERE/requirements-server.txt"
SERVER_LOG="$HERE/server.log"

# ---- 첫 실행 자동 설치 (마크 첫 구동처럼) --------------------------------
bootstrap() {
    [ -x "$PYBIN" ] && return 0

    local PY3
    PY3="$(command -v python3 || command -v python || true)"
    if [ -z "$PY3" ]; then
        echo "!! Python 3.11+ 가 필요합니다 (마인크래프트의 Java 처럼)."
        echo "   Ubuntu:  sudo apt install python3 python3-venv"
        echo "   설치 후 다시 ./qonvo-server.sh 를 실행하세요."
        exit 1
    fi

    echo "[설치] 최초 1회 구성 중... (라이브러리 내려받기, 30초~1분)"
    # venv 생성. python3-venv(ensurepip) 없는 배포판도 자동 처리:
    # 일반 생성 실패 시 --without-pip 로 만들고 get-pip 로 pip 부트스트랩.
    "$PY3" -m venv "$VENV" >/dev/null 2>&1 \
        || "$PY3" -m venv --without-pip "$VENV" >/dev/null 2>&1
    if [ ! -x "$VENV/bin/pip" ]; then
        curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/qonvo-get-pip.py \
            && "$PYBIN" /tmp/qonvo-get-pip.py -q >/dev/null 2>&1
    fi
    if ! "$PYBIN" -m pip install -q --upgrade pip >/dev/null 2>&1; then
        echo "!! pip 설치 실패. 인터넷 연결 또는 'sudo apt install python3-venv' 후 재시도."
        exit 1
    fi
    "$PYBIN" -m pip install -q -r "$REQ"
    echo "[설치] 완료"
}

start() {
    bootstrap
    if pgrep -f "python -m server run" >/dev/null; then
        echo "[server] 이미 실행 중"
    else
        ( cd "$SRC" && setsid "$PYBIN" -m server run >"$SERVER_LOG" 2>&1 </dev/null & )
        echo "[server] 기동 중..."
    fi
    for _ in $(seq 1 20); do
        grep -q "가동 중" "$SERVER_LOG" 2>/dev/null && break
        sleep 1
    done
    echo
    sed -n '/=====/,/=====/p' "$SERVER_LOG" 2>/dev/null | tail -12
}

stop() {
    pkill -f "python -m server run" 2>/dev/null && echo "[server] 종료" || echo "[server] 실행 중 아님"
}

status() {
    if pgrep -f "python -m server run" >/dev/null; then
        echo "[server] UP"
        sed -n '/=====/,/=====/p' "$SERVER_LOG" 2>/dev/null | tail -12
    else
        echo "[server] down"
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    logs)    tail -f "$SERVER_LOG" ;;
    adduser) bootstrap; ( cd "$SRC" && "$PYBIN" -m server adduser "${2:-}" "${3:-}" "${4:-1}" ) ;;
    whitelist|ban|pardon|banlist|listusers|config)
        bootstrap; ( cd "$SRC" && "$PYBIN" -m server "$@" ) ;;
    *) echo "usage: $0 {start|stop|restart|status|logs|adduser|whitelist|ban|pardon|banlist}"; exit 2 ;;
esac
