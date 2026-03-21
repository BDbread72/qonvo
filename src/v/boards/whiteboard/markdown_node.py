"""마크다운 노드 위젯
- 입력 포트로 텍스트를 받아 마크다운 렌더링
- 편집/미리보기 모드 전환 지원
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle
from v.theme import Theme


# 다크 테마용 마크다운 렌더링 CSS
_MD_CSS = """
body { color: #e0e0e0; font-family: '맑은 고딕', sans-serif; font-size: 13px; }
h1 { color: #fff; font-size: 20px; border-bottom: 1px solid #444; padding-bottom: 4px; }
h2 { color: #fff; font-size: 17px; border-bottom: 1px solid #333; padding-bottom: 3px; }
h3 { color: #ddd; font-size: 15px; }
code { background: #1a1a1a; color: #e6db74; padding: 2px 4px; border-radius: 3px; }
pre { background: #1a1a1a; padding: 8px; border-radius: 4px; }
a { color: #58a6ff; }
blockquote { border-left: 3px solid #555; padding-left: 8px; color: #999; }
"""


class MarkdownNodeWidget(QWidget, BaseNode):
    """마크다운 편집/미리보기 전환이 가능한 노드 위젯."""

    def __init__(self, on_modified=None):
        """위젯을 초기화하고 UI 구성 요소를 배치한다."""
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.init_base_node(node_id=None, on_modified=on_modified)
        # 원본 마크다운 텍스트와 미리보기 모드 상태
        self._raw_md = ""
        self._preview_mode = True

        self.setMinimumSize(200, 150)
        self.resize(280, 200)
        self._apply_style()

        # 메인 레이아웃
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 드래그 가능한 헤더 영역
        self.header = DraggableHeader(self)
        self.header.setFixedHeight(28)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 2, 4, 2)
        header_layout.setSpacing(3)

        # 헤더 제목
        title = QLabel("Markdown")
        title.setStyleSheet(
            "color: #58a6ff; font-size: 12px; font-weight: bold;"
            " background: transparent; border: none;"
        )
        header_layout.addWidget(title, 1)

        # 미리보기/편집 전환 버튼
        self.toggle_btn = QPushButton("\u270e")
        self.toggle_btn.setFixedSize(22, 22)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: 1px solid #555;
                border-radius: 3px; color: #aaa; font-size: 13px;
            }
            QPushButton:hover { background: #3a3a3a; color: #fff; }
        """)
        self.toggle_btn.clicked.connect(self._toggle_mode)
        header_layout.addWidget(self.toggle_btn)

        layout.addWidget(self.header)

        # 텍스트 영역 (미리보기/편집 겸용)
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.document().setDefaultStyleSheet(_MD_CSS)
        self.text_area.setStyleSheet("""
            QTextEdit {
                background: #1e1e1e; border: none;
                color: #e0e0e0; padding: 8px;
                selection-background-color: #264f78;
            }
        """)
        self.text_area.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.text_area)

        # 리사이즈 핸들
        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)

    def _apply_style(self):
        """위젯 외곽 스타일을 적용한다."""
        self.setStyleSheet("""
            MarkdownNodeWidget {
                background-color: #1e1e1e;
                border: 1px solid #58a6ff;
                border-radius: 6px;
            }
        """)

    def _toggle_mode(self):
        """편집 모드와 미리보기 모드를 전환한다."""
        if self._preview_mode:
            # 편집 모드로 전환
            self._preview_mode = False
            self.text_area.setReadOnly(False)
            self.text_area.setPlainText(self._raw_md)
            self.toggle_btn.setText("\U0001f441")
            self.toggle_btn.setStyleSheet("""
                QPushButton {
                    background: #264f78; border: 1px solid #58a6ff;
                    border-radius: 3px; color: #58a6ff; font-size: 13px;
                }
                QPushButton:hover { background: #2a5a8a; }
            """)
        else:
            # 미리보기 모드로 전환 — 원본 저장 후 렌더링
            self._raw_md = self.text_area.toPlainText()
            self._preview_mode = True
            self.text_area.setReadOnly(True)
            self.text_area.setMarkdown(self._raw_md)
            self.toggle_btn.setText("\u270e")
            self.toggle_btn.setStyleSheet("""
                QPushButton {
                    background: transparent; border: 1px solid #555;
                    border-radius: 3px; color: #aaa; font-size: 13px;
                }
                QPushButton:hover { background: #3a3a3a; color: #fff; }
            """)

    def _on_text_changed(self):
        """편집 중 변경이 발생하면 수정 콜백을 호출한다."""
        if not self._preview_mode:
            if self.on_modified:
                self.on_modified()

    def on_signal_input(self, input_data=None):
        """외부 신호 입력을 받아 마크다운 내용을 갱신한다."""
        data = self._collect_input_data()
        if data:
            self._raw_md = str(data)
            # 미리보기 모드에서만 화면 갱신 (편집 중 덮어쓰기 방지)
            if self._preview_mode:
                self.text_area.setMarkdown(self._raw_md)

    def set_markdown(self, md_text):
        """마크다운 텍스트를 설정하고 현재 모드에 맞게 표시한다."""
        self._raw_md = md_text
        if self._preview_mode:
            self.text_area.setMarkdown(self._raw_md)
        else:
            self.text_area.setPlainText(self._raw_md)

    def get_data(self):
        """현재 노드 상태를 딕셔너리로 반환한다."""
        # 편집 모드에서는 텍스트를 원본으로 반영
        if not self._preview_mode:
            self._raw_md = self.text_area.toPlainText()
        return {
            "type": "markdown",
            "node_id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "markdown": self._raw_md,
            "preview_mode": self._preview_mode,
        }
