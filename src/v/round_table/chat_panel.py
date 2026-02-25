"""
채팅 내역 패널
- 참가자별 메시지 표시
- 스트리밍 지원
- 중재자 요약 표시
"""

from dataclasses import dataclass
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QScrollArea, QLabel, QFrame,
    QHBoxLayout, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont

from v.round_table.personas import Persona


@dataclass
class ChatMessage:
    """채팅 메시지"""
    persona: Persona
    content: str
    is_moderator: bool = False
    is_streaming: bool = False
    step_name: str = ""
    round_num: int = 0


class MessageBubble(QFrame):
    """메시지 버블"""

    def __init__(self, message: ChatMessage, parent=None):
        super().__init__(parent)
        self.message = message
        self._init_ui()

    def _init_ui(self):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(self._get_style())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # 헤더 (아이콘 + 이름 + 스텝 정보)
        header = QHBoxLayout()

        # 아이콘 + 이름
        name_label = QLabel(f"{self.message.persona.icon} {self.message.persona.name}")
        name_label.setFont(QFont("맑은 고딕", 11, QFont.Weight.Bold))
        name_label.setStyleSheet(f"color: {self.message.persona.color};")
        header.addWidget(name_label)

        header.addStretch()

        # 스텝/라운드 정보
        if self.message.step_name:
            step_label = QLabel(f"{self.message.step_name} R{self.message.round_num}")
            step_label.setFont(QFont("맑은 고딕", 9))
            step_label.setStyleSheet("color: #888;")
            header.addWidget(step_label)

        layout.addLayout(header)

        # 내용
        self.content_label = QLabel(self.message.content)
        self.content_label.setWordWrap(True)
        self.content_label.setTextFormat(Qt.TextFormat.PlainText)
        self.content_label.setFont(QFont("맑은 고딕", 10))
        self.content_label.setStyleSheet("color: #e0e0e0; line-height: 1.5;")
        self.content_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout.addWidget(self.content_label)

        # 스트리밍 중 표시
        if self.message.is_streaming:
            self.streaming_label = QLabel("...")
            self.streaming_label.setStyleSheet("color: #888; font-style: italic;")
            layout.addWidget(self.streaming_label)
        else:
            self.streaming_label = None

    def _get_style(self) -> str:
        if self.message.is_moderator:
            bg = "#2d3a4d"
            border = self.message.persona.color
        else:
            bg = "#252530"
            border = "#3a3a4a"

        return f"""
            MessageBubble {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
                margin: 4px 0;
            }}
        """

    def update_content(self, content: str, is_streaming: bool = False):
        """내용 업데이트 (스트리밍용)"""
        self.message.content = content
        self.message.is_streaming = is_streaming
        self.content_label.setText(content)

        if self.streaming_label:
            if is_streaming:
                self.streaming_label.show()
            else:
                self.streaming_label.hide()


class StepDivider(QFrame):
    """스텝 구분선"""

    def __init__(self, step_name: str, round_num: int, parent=None):
        super().__init__(parent)
        self._init_ui(step_name, round_num)

    def _init_ui(self, step_name: str, round_num: int):
        self.setStyleSheet("""
            StepDivider {
                background-color: #1a1a2e;
                border: none;
                margin: 16px 0;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)

        # 좌측 라인
        left_line = QFrame()
        left_line.setFrameShape(QFrame.Shape.HLine)
        left_line.setStyleSheet("background-color: #4a4a6a;")
        left_line.setFixedHeight(1)
        layout.addWidget(left_line, 1)

        # 스텝 이름
        label = QLabel(f"  {step_name} - Round {round_num}  ")
        label.setFont(QFont("맑은 고딕", 10, QFont.Weight.Bold))
        label.setStyleSheet("color: #8888aa;")
        layout.addWidget(label)

        # 우측 라인
        right_line = QFrame()
        right_line.setFrameShape(QFrame.Shape.HLine)
        right_line.setStyleSheet("background-color: #4a4a6a;")
        right_line.setFixedHeight(1)
        layout.addWidget(right_line, 1)


class ChatPanel(QWidget):
    """채팅 내역 패널"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.messages: list[MessageBubble] = []
        self.current_step = ""
        self.current_round = 0
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 스크롤 영역
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: #1a1a2e;
            }
            QScrollBar:vertical {
                background-color: #1a1a2e;
                width: 10px;
            }
            QScrollBar::handle:vertical {
                background-color: #4a4a6a;
                border-radius: 5px;
                min-height: 30px;
            }
        """)

        # 컨텐츠 위젯
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 12, 12, 12)
        self.content_layout.setSpacing(8)
        self.content_layout.addStretch()

        scroll.setWidget(self.content)
        layout.addWidget(scroll)

        self.scroll_area = scroll

    def set_step(self, step_name: str, round_num: int):
        """현재 스텝 설정 (구분선 추가)"""
        if step_name != self.current_step or round_num != self.current_round:
            self.current_step = step_name
            self.current_round = round_num

            # 구분선 추가
            divider = StepDivider(step_name, round_num)
            self.content_layout.insertWidget(self.content_layout.count() - 1, divider)

    def add_message(self, persona: Persona, is_moderator: bool = False) -> MessageBubble:
        """새 메시지 추가 (스트리밍 시작)"""
        message = ChatMessage(
            persona=persona,
            content="",
            is_moderator=is_moderator,
            is_streaming=True,
            step_name=self.current_step,
            round_num=self.current_round
        )

        bubble = MessageBubble(message)
        self.messages.append(bubble)
        self.content_layout.insertWidget(self.content_layout.count() - 1, bubble)

        # 스크롤 맨 아래로
        QTimer.singleShot(50, self._scroll_to_bottom)

        return bubble

    def update_message(self, bubble: MessageBubble, content: str, is_streaming: bool = True):
        """메시지 업데이트 (스트리밍 중)"""
        bubble.update_content(content, is_streaming)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def finish_message(self, bubble: MessageBubble, content: str):
        """메시지 완료"""
        bubble.update_content(content, is_streaming=False)

    def add_system_message(self, text: str):
        """시스템 메시지 추가"""
        label = QLabel(text)
        label.setFont(QFont("맑은 고딕", 9))
        label.setStyleSheet("color: #888; font-style: italic; padding: 8px;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.insertWidget(self.content_layout.count() - 1, label)

    def clear(self):
        """모든 메시지 삭제"""
        while self.content_layout.count() > 1:  # stretch 제외
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.messages.clear()
        self.current_step = ""
        self.current_round = 0

    def _scroll_to_bottom(self):
        """맨 아래로 스크롤"""
        scrollbar = self.scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
