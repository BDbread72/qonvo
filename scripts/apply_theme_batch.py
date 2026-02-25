"""
Phase 3: Theme 자동 적용 도우미 스크립트
남은 파일들에 Theme import 추가
"""
import re
from pathlib import Path

# 처리할 파일 목록
files_to_process = [
    "src/v/boards/whiteboard/sticky_note.py",
    "src/v/boards/whiteboard/function_node.py",

    "src/v/boards/whiteboard/widgets.py",
    "src/v/boards/whiteboard/dimension_item.py",
    "src/v/boards/whiteboard/minimap.py",
    "src/v/boards/whiteboard/button_node.py",
    "src/v/boards/whiteboard/checklist.py",
    "src/v/boards/whiteboard/search_bar.py",
    "src/v/boards/whiteboard/function_editor.py",
    "src/v/boards/whiteboard/function_types.py",
    "src/v/boards/whiteboard/function_nodes.py",
    "src/v/boards/whiteboard/dimension_board.py",
    "src/v/boards/whiteboard/round_table.py",
    "src/v/settings_dialog.py",
]

base_path = Path(__file__).parent

print("=" * 60)
print("Phase 3: Theme 임포트 추가 검사")
print("=" * 60)

for file_path_str in files_to_process:
    file_path = base_path / file_path_str

    if not file_path.exists():
        print(f"[SKIP] {file_path_str} - 파일 없음")
        continue

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Theme import 확인
    has_theme_import = 'from v.theme import Theme' in content
    has_hex_colors = bool(re.search(r'#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3}', content))

    if has_hex_colors:
        if has_theme_import:
            print(f"[OK] {file_path_str} - Theme import O, 색상 많음")
        else:
            print(f"[ADD] {file_path_str} - Theme import 필요")
    else:
        print(f"[SKIP] {file_path_str} - 하드코딩 색상 없음")

print("\n" + "=" * 60)
print("정리: 각 파일에 Theme import 추가 및 색상 교체 필요")
print("=" * 60)
