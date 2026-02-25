"""
남은 파일들에 Theme import 자동 추가
"""
import re
from pathlib import Path

files_and_positions = [
    ("src/v/boards/whiteboard/widgets.py", "from PyQt6"),
    ("src/v/boards/whiteboard/dimension_item.py", "from PyQt6"),
    ("src/v/boards/whiteboard/minimap.py", "from PyQt6"),
    ("src/v/boards/whiteboard/button_node.py", "from PyQt6"),
    ("src/v/boards/whiteboard/checklist.py", "from PyQt6"),
    ("src/v/boards/whiteboard/search_bar.py", "from PyQt6"),
    ("src/v/boards/whiteboard/function_editor.py", "from PyQt6"),
    ("src/v/boards/whiteboard/function_types.py", "from PyQt6"),
    ("src/v/boards/whiteboard/function_nodes.py", "from PyQt6"),
    ("src/v/boards/whiteboard/dimension_board.py", "from PyQt6"),
    ("src/v/boards/whiteboard/round_table.py", "from PyQt6"),
    ("src/v/settings_dialog.py", "from PyQt6"),
]

base_path = Path(__file__).parent

for file_path_str, marker in files_and_positions:
    file_path = base_path / file_path_str

    if not file_path.exists():
        print(f"[SKIP] {file_path_str} - 파일 없음")
        continue

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 이미 있으면 스킵
    if 'from v.theme import Theme' in content:
        print(f"[OK] {file_path_str} - 이미 Theme import 있음")
        continue

    # PyQt6 import 다음에 Theme import 추가
    if marker in content:
        # 마지막 PyQt6 import 찾기
        lines = content.split('\n')
        insert_pos = None
        last_pyqt_line = -1

        for i, line in enumerate(lines):
            if line.startswith('from PyQt6') or line.startswith('import PyQt6'):
                last_pyqt_line = i

        if last_pyqt_line >= 0:
            # PyQt import 섹션 끝 찾기 (빈 줄까지)
            insert_pos = last_pyqt_line + 1
            while insert_pos < len(lines) and lines[insert_pos].startswith(('from PyQt6', 'import PyQt6')):
                insert_pos += 1

            # 빈 줄 건너뛰고 다음 내용 찾기
            while insert_pos < len(lines) and lines[insert_pos].strip() == '':
                insert_pos += 1

            # Theme import 추가
            if insert_pos > 0 and insert_pos < len(lines):
                # 앞에 빈 줄이 있으면 그 다음에 추가
                lines.insert(insert_pos, 'from v.theme import Theme')
                new_content = '\n'.join(lines)

                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)

                print(f"[ADD] {file_path_str} - Theme import 추가")
            else:
                print(f"[WARN] {file_path_str} - 삽입 위치 결정 실패")
        else:
            print(f"[WARN] {file_path_str} - PyQt6 import 없음")
    else:
        print(f"[SKIP] {file_path_str} - 마커 없음")

print("\n완료! 모든 파일에 Theme import 추가됨")
