@echo off
rem Bake build stamp (cumulative commit count) into buildno.txt for the bundle
rem so the frozen exe titlebar shows beta-X.Y.Z+N.
rem NOTE: keep these comments ASCII-only. cmd parses this file as cp949 while it is saved
rem as UTF-8, and pipe/redirect chars inside rem lines get EXECUTED by cmd. The old
rem redirect-first echo idiom burned "ECHO is on." into buildno.txt (version showed
rem beta-X.Y.Z+ECHO is on.). Current form below is safe.
set BUILDNO=
for /f %%i in ('git rev-list --count HEAD') do set BUILDNO=%%i
if "%BUILDNO%"=="" set BUILDNO=0
(echo %BUILDNO%)> buildno.txt
python -m PyInstaller --noconfirm --onefile --noconsole --name qonvo --icon=icon.ico --distpath . ^
    --add-data "icon.ico;." ^
    --add-data "lang;lang" ^
    --add-data "build.toml;." ^
    --add-data "buildno.txt;." ^
    --add-data "plugins;plugins" ^
    --add-data "icons;icons" ^
    --collect-all openai ^
    --collect-all anthropic ^
    --collect-all websocket ^
    --exclude-module torch ^
    --exclude-module torchvision ^
    --exclude-module tensorflow ^
    --exclude-module numba ^
    --exclude-module llvmlite ^
    --exclude-module sklearn ^
    --exclude-module skimage ^
    --exclude-module scipy ^
    --exclude-module pygame ^
    --exclude-module pytest ^
    --exclude-module py ^
    --exclude-module lxml ^
    --exclude-module jinja2 ^
    --exclude-module uvicorn ^
    --exclude-module fsspec ^
    --exclude-module pandas ^
    --exclude-module matplotlib ^
    --exclude-module IPython ^
    --exclude-module rembg ^
    --hidden-import "v.boards.whiteboard" ^
    --hidden-import "v.boards.whiteboard.plugin" ^
    --hidden-import "v.boards.whiteboard.view" ^
    --hidden-import "v.boards.whiteboard.chat_node" ^
    --hidden-import "v.boards.whiteboard.items" ^
    --hidden-import "v.boards.whiteboard.widgets" ^
    --hidden-import "v.boards.whiteboard.minimap" ^
    --hidden-import "v.boards.whiteboard.radial_menu" ^
    --hidden-import "v.boards.whiteboard.search_bar" ^
    --hidden-import "v.boards.whiteboard.dimension_item" ^
    --hidden-import "v.boards.whiteboard.function_node" ^
    --hidden-import "v.boards.whiteboard.sticky_note" ^
    --hidden-import "v.boards.whiteboard.button_node" ^
    --hidden-import "v.boards.whiteboard.round_table" ^
    --hidden-import "v.boards.whiteboard.checklist" ^
    --hidden-import "v.boards.whiteboard.repository_node" ^
    --hidden-import "v.boards.whiteboard.function_library" ^
    --hidden-import "v.boards.whiteboard.function_editor" ^
    --hidden-import "v.boards.whiteboard.function_editor_web" ^
    --hidden-import "PyQt6.QtWebEngineWidgets" ^
    --hidden-import "PyQt6.QtWebEngineCore" ^
    --hidden-import "PyQt6.QtWebChannel" ^
    --hidden-import "v.data_viewer" ^
    ./src/main.py
del buildno.txt 2>nul
