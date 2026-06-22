#!/usr/bin/env bash
# Linux 빌드 — qonvo 단일 실행파일(ELF) 생성. 저장소 루트에서 실행.
#   1) python3 -m venv .venv && source .venv/bin/activate   (권장)
#   2) pip install -r requirements.txt pyinstaller
#   3) ./crack.sh
# 결과물: ./qonvo  (실행: ./qonvo)
#
# crack.bat(Windows)의 리눅스판:
#  - --add-data 구분자 ';' → ':'  (리눅스)
#  - --icon 생략(리눅스 GUI 아이콘은 .ico 미사용; 런타임 번들은 --add-data로 유지)
#  - 새 소셜/서버 모듈은 지연 import 라서 --collect-submodules 로 통째 수집
set -e

# 빌드 스탬프(누적 커밋 수)를 buildno.txt 에 구워서 번들 — frozen exe 타이틀바에 beta-X.Y.Z+N 표시
printf '%s' "$(git rev-list --count HEAD)" > buildno.txt
trap 'rm -f buildno.txt' EXIT

python3 -m PyInstaller --noconfirm --onefile --noconsole --name qonvo --distpath . \
    --add-data "icon.ico:." \
    --add-data "icon.png:." \
    --add-data "lang:lang" \
    --add-data "build.toml:." \
    --add-data "buildno.txt:." \
    --add-data "plugins:plugins" \
    --add-data "icons:icons" \
    --collect-all openai \
    --collect-all anthropic \
    --collect-all websocket \
    --collect-all aiohttp \
    --collect-submodules v.boards.whiteboard \
    --collect-submodules server \
    --collect-submodules v \
    --exclude-module torch \
    --exclude-module torchvision \
    --exclude-module tensorflow \
    --exclude-module numba \
    --exclude-module llvmlite \
    --exclude-module sklearn \
    --exclude-module skimage \
    --exclude-module scipy \
    --exclude-module pygame \
    --exclude-module pytest \
    --exclude-module py \
    --exclude-module lxml \
    --exclude-module jinja2 \
    --exclude-module uvicorn \
    --exclude-module fsspec \
    --exclude-module pandas \
    --exclude-module matplotlib \
    --exclude-module IPython \
    --exclude-module rembg \
    --hidden-import "v.data_viewer" \
    ./src/main.py

echo
echo "빌드 완료 → ./qonvo  (실행: ./qonvo)"
echo "Qt 오류(xcb) 나면:  sudo apt install -y libxcb-cursor0 libxcb-xinerama0"

# --- 독/메뉴 아이콘용 .desktop 설치 (GNOME 등) ---
# 리눅스는 실행파일에 아이콘이 안 박힘 → .desktop 으로 등록해야 독에 아이콘이 뜬다.
BIN="$(pwd)/qonvo"
ICON_DEST="$HOME/.local/share/icons/qonvo.png"
DESK_DEST="$HOME/.local/share/applications/qonvo.desktop"
mkdir -p "$(dirname "$ICON_DEST")" "$(dirname "$DESK_DEST")"
cp -f icon.png "$ICON_DEST" 2>/dev/null || true
cat > "$DESK_DEST" <<DESK
[Desktop Entry]
Type=Application
Name=Qonvo
Exec=$BIN
Icon=$ICON_DEST
Terminal=false
Categories=Graphics;Utility;
StartupWMClass=qonvo
DESK
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "아이콘 등록됨: $DESK_DEST  (독에 안 뜨면 로그아웃/재로그인)"
