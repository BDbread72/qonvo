"""
라운드 테이블 노드
- 여러 AI 모델이 순차적으로 토론하는 실험적 기능
"""
import copy
import math

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QScrollArea, QFrame, QDialog, QLineEdit,
    QApplication, QListWidget, QListWidgetItem, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPainterPath

from v.theme import Theme
from q import t
from v.provider import GeminiProvider, ChatMessage
from v.model_plugin import get_all_models, get_all_model_ids
from v.settings import is_experimental_mode
from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle, InputDialog


# 참가자별 색상
PARTICIPANT_COLORS = [
    "#3498db",  # 파랑
    "#e74c3c",  # 빨강
    "#9b59b6",  # 보라
    "#f1c40f",  # 노랑
    "#1abc9c",  # 청록
    "#e67e22",  # 주황
    "#2ecc71",  # 초록
    "#34495e",  # 회색
]


class RoundTableConfigDialog(QDialog):
    """라운드 테이블 설정 다이얼로그"""

    def __init__(self, participants=None, on_save=None):
        super().__init__(None)
        self.on_save = on_save
        self.setWindowTitle(t("round_table.edit_settings"))
        self.setMinimumSize(500, 400)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setStyleSheet("QDialog { background-color: #1e1e1e; }")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 설명
        desc = QLabel(t("round_table.config_desc"))
        desc.setStyleSheet("color: #888; font-size: 11px; margin-bottom: 8px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 참가자 리스트
        self._add_label(layout, t("round_table.participants"))

        self.participant_list = QListWidget()
        self.participant_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.participant_list.setStyleSheet("""
            QListWidget {
                background: #252525;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 4px;
            }
            QListWidget::item {
                background: #333;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 8px;
                margin: 2px;
                color: #ddd;
            }
            QListWidget::item:selected {
                background: #0d6efd;
                border-color: #3d8bfd;
            }
        """)
        layout.addWidget(self.participant_list)

        # 참가자 추가 버튼
        add_row = QHBoxLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(t("round_table.participant_name"))
        self.name_input.setStyleSheet("""
            QLineEdit {
                background: #333; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 8px; font-size: 12px;
            }
        """)
        add_row.addWidget(self.name_input)

        self.model_combo = QComboBox()
        _all_models = get_all_models()
        for mid in get_all_model_ids():
            self.model_combo.addItem(_all_models[mid], mid)
        self.model_combo.setStyleSheet("""
            QComboBox {
                background: #333; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 8px; min-width: 150px;
            }
            QComboBox QAbstractItemView {
                background: #2d2d2d; color: #ddd;
                border: 1px solid #444;
                selection-background-color: #0d6efd;
            }
        """)
        self.model_combo.setMaxVisibleItems(15)
        add_row.addWidget(self.model_combo)

        btn_add = QPushButton("+")
        btn_add.setFixedSize(36, 36)
        btn_add.setStyleSheet("""
            QPushButton {
                background: #27ae60; color: white;
                border: none; border-radius: 4px;
                font-size: 18px; font-weight: bold;
            }
            QPushButton:hover { background: #2ecc71; }
        """)
        btn_add.clicked.connect(self._add_participant)
        add_row.addWidget(btn_add)

        layout.addLayout(add_row)

        # 삭제 버튼
        btn_remove = QPushButton(t("round_table.remove_selected"))
        btn_remove.setStyleSheet("""
            QPushButton {
                background: #c0392b; color: white;
                border: none; border-radius: 4px;
                padding: 8px;
            }
            QPushButton:hover { background: #e74c3c; }
        """)
        btn_remove.clicked.connect(self._remove_selected)
        layout.addWidget(btn_remove)

        layout.addStretch()

        # 저장/취소
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton(t("button.cancel"))
        btn_cancel.setStyleSheet("padding: 8px 20px;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addStretch()
        btn_save = QPushButton(t("button.save"))
        btn_save.setStyleSheet("padding: 8px 20px; background-color: #0d6efd; font-weight: bold;")
        btn_save.clicked.connect(self._save)
        btn_layout.addWidget(btn_save)
        layout.addLayout(btn_layout)

        # 기존 참가자 복원
        for p in (participants or []):
            self._add_participant_item(p.get("name", ""), p.get("model", ""))

    def _add_label(self, layout, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #ccc; font-size: 12px; font-weight: bold; margin-top: 8px;")
        layout.addWidget(lbl)

    def _add_participant(self):
        name = self.name_input.text().strip()
        if not name:
            name = f"AI {self.participant_list.count() + 1}"
        model = self.model_combo.currentData()
        self._add_participant_item(name, model)
        self.name_input.clear()

    def _add_participant_item(self, name, model):
        model_name = get_all_models().get(model, model)
        item = QListWidgetItem(f"{name}  [{model_name}]")
        item.setData(Qt.ItemDataRole.UserRole, {"name": name, "model": model})

        # 색상 표시
        color_idx = self.participant_list.count() % len(PARTICIPANT_COLORS)
        item.setForeground(QColor(PARTICIPANT_COLORS[color_idx]))

        self.participant_list.addItem(item)

    def _remove_selected(self):
        for item in self.participant_list.selectedItems():
            self.participant_list.takeItem(self.participant_list.row(item))

    def _collect_participants(self):
        participants = []
        for i in range(self.participant_list.count()):
            item = self.participant_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                participants.append(data)
        return participants

    def _save(self):
        participants = self._collect_participants()
        if self.on_save:
            self.on_save(participants)
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() == Qt.Key.Key_Return and self.name_input.hasFocus():
            self._add_participant()
        else:
            super().keyPressEvent(event)


class RoundTableWorker(QThread):
    """라운드 테이블 순차 실행 워커"""
    participant_started = pyqtSignal(int, str)      # (참가자 인덱스, 이름)
    chunk_received = pyqtSignal(int, str)           # (참가자 인덱스, 텍스트 청크)
    participant_finished = pyqtSignal(int, str)     # (참가자 인덱스, 최종 응답)
    all_finished = pyqtSignal(str)                  # 최종 결과 (마지막 참가자 응답)
    error_signal = pyqtSignal(str)
    tokens_received = pyqtSignal(int, int)

    def __init__(self, provider, participants, initial_input, context_messages=None):
        super().__init__()
        self.provider = provider
        self.participants = participants
        self.initial_input = initial_input
        self.context_messages = context_messages or []
        self._cancel = False

    def run(self):
        total_in = 0
        total_out = 0
        accumulated_discussion = []  # 누적 대화

        for i, participant in enumerate(self.participants):
            if self._cancel:
                return

            name = participant["name"]
            model = participant["model"]

            self.participant_started.emit(i, name)

            # 프롬프트 구성: 이전 대화 컨텍스트 포함
            if accumulated_discussion:
                discussion_text = "\n\n".join([
                    f"[{d['name']}]: {d['content']}"
                    for d in accumulated_discussion
                ])
                prompt = f"""주제: {self.initial_input}

지금까지의 토론:
{discussion_text}

당신은 '{name}'입니다. 위 토론을 읽고 자신의 의견을 제시하세요. 이전 참가자들의 의견에 동의하거나 반박할 수 있습니다."""
            else:
                prompt = f"""주제: {self.initial_input}

당신은 '{name}'입니다. 이 주제에 대해 자신의 의견을 제시하세요."""

            messages = list(self.context_messages) + [
                ChatMessage(role="user", content=prompt)
            ]

            try:
                # 스트리밍 호출
                result = self.provider.chat(model, messages, stream=True)

                full_response = ""
                for chunk in result:
                    if self._cancel:
                        return

                    if isinstance(chunk, dict):
                        if chunk.get("__usage__"):
                            total_in += chunk.get("prompt_tokens", 0)
                            total_out += chunk.get("candidates_tokens", 0)
                    elif isinstance(chunk, str):
                        full_response += chunk
                        self.chunk_received.emit(i, chunk)

                # 누적 대화에 추가
                accumulated_discussion.append({
                    "name": name,
                    "content": full_response
                })

                self.participant_finished.emit(i, full_response)
                self.tokens_received.emit(total_in, total_out)

            except Exception as e:
                self.error_signal.emit(f"{name}: {e}")
                return

        # 최종 결과: 마지막 참가자의 응답
        if accumulated_discussion:
            self.all_finished.emit(accumulated_discussion[-1]["content"])
        else:
            self.all_finished.emit("")

    def cancel(self):
        self._cancel = True
        if self.provider:
            self.provider.cancel()


class RoundTableWidget(QWidget, BaseNode):
    """라운드 테이블 노드 위젯"""

    def __init__(self, node_id, on_send=None, on_modified=None):
        super().__init__()
        self.init_base_node(node_id=node_id, on_modified=on_modified)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.on_send = on_send

        # 참가자 설정
        self.participants = []  # [{"name": str, "model": str}, ...]
        self.conversation_log = []  # [{"name": str, "content": str}, ...]

        self.user_message = None
        self.user_files = []
        self.ai_response = None
        self.model = None
        self.pinned = False
        self.tokens_in = 0
        self.tokens_out = 0

        self._current_participant = -1
        self._participant_labels = []  # 참가자 라벨 리스트 (하이라이트용)

        self.setMinimumSize(320, 280)
        self.resize(360, 320)
        self.setStyleSheet("""
            RoundTableWidget {
                background-color: #1e1e1e;
                border: 3px solid #27ae60;
                border-radius: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(0)

        # 헤더
        self.header = DraggableHeader(self)
        self.header.setFixedHeight(36)
        self.header.setStyleSheet("""
            DraggableHeader {
                background-color: #1e2d1e;
                border-top-left-radius: 9px;
                border-top-right-radius: 9px;
                border-bottom: 1px solid #2a4a2a;
            }
        """)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        self.title_label = QLabel(f"#{node_id} Round Table")
        self.title_label.setStyleSheet("color: #2ecc71; font-weight: bold; font-size: 12px;")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        # 설정 버튼
        btn_edit = QPushButton("⚙")
        btn_edit.setFixedSize(28, 28)
        btn_edit.setStyleSheet("""
            QPushButton {
                background: transparent; border: none;
                font-size: 16px; color: #888;
            }
            QPushButton:hover { color: #ddd; }
        """)
        btn_edit.setToolTip(t("round_table.edit_settings"))
        btn_edit.clicked.connect(self._open_config)
        header_layout.addWidget(btn_edit)

        layout.addWidget(self.header)

        # 참가자 바
        self.participants_bar = QFrame()
        self.participants_bar.setStyleSheet("background: #252525; border: none;")
        self.participants_layout = QHBoxLayout(self.participants_bar)
        self.participants_layout.setContentsMargins(8, 6, 8, 6)
        self.participants_layout.setSpacing(8)

        self.no_participants_label = QLabel(t("round_table.no_participants"))
        self.no_participants_label.setStyleSheet("color: #666; font-size: 11px;")
        self.participants_layout.addWidget(self.no_participants_label)
        self.participants_layout.addStretch()

        layout.addWidget(self.participants_bar)

        # 진행 상태
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("""
            color: #27ae60; font-size: 11px; padding: 4px 12px;
            background: #1a2a1a; border: none; font-weight: bold;
        """)
        self.progress_label.hide()
        layout.addWidget(self.progress_label)

        # 대화 로그 영역
        self.content_area = QScrollArea()
        self.content_area.setWidgetResizable(True)
        self.content_area.setStyleSheet("""
            QScrollArea { background-color: #1e1e1e; border: none; }
            QScrollBar:vertical { width: 6px; background: transparent; }
            QScrollBar::handle:vertical { background: #444; border-radius: 3px; }
        """)

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("background: transparent;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(10, 10, 10, 10)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.content_layout.setSpacing(8)
        self.content_area.setWidget(self.content_widget)
        layout.addWidget(self.content_area)

        # 입력 버튼
        self.btn_input = QPushButton(t("button.compose"))
        self.btn_input.setStyleSheet("""
            QPushButton {
                background-color: #27ae60; color: white;
                border: none; padding: 10px; font-weight: bold;
                border-bottom-left-radius: 9px;
                border-bottom-right-radius: 9px;
            }
            QPushButton:hover { background-color: #2ecc71; }
        """)
        self.btn_input.clicked.connect(self._open_input)
        layout.addWidget(self.btn_input)

        # 토큰
        self.tokens_label = QLabel("")
        self.tokens_label.setStyleSheet("color: #555; font-size: 10px; padding: 2px 10px;")
        self.tokens_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.tokens_label.hide()
        layout.addWidget(self.tokens_label)

        # 리사이즈 핸들
        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)
        self.resize_handle.raise_()

        # 펄스 애니메이션
        self._pulse_timer = QTimer()
        self._pulse_timer.setInterval(30)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_phase = 0.0
        self._pulse_active = False
        self._pulse_bg_color = None
        self._pulse_border_color = None

        # 현재 응답 라벨 (스트리밍용)
        self._current_response_label = None

    def _open_config(self):
        """설정 다이얼로그 열기"""
        if self.sent:
            return

        def on_save(participants):
            self.participants = participants
            self._update_participants_bar()
            if self.on_modified:
                self.on_modified()

        dialog = RoundTableConfigDialog(participants=self.participants, on_save=on_save)
        dialog.exec()

    def _update_participants_bar(self):
        """참가자 바 업데이트"""
        # 기존 라벨 제거
        for lbl in self._participant_labels:
            lbl.setParent(None)
            lbl.deleteLater()
        self._participant_labels = []

        if not self.participants:
            self.no_participants_label.show()
            return

        self.no_participants_label.hide()

        for i, p in enumerate(self.participants):
            color = PARTICIPANT_COLORS[i % len(PARTICIPANT_COLORS)]
            lbl = QLabel(p["name"])
            lbl.setStyleSheet(f"""
                color: {color}; font-size: 11px; font-weight: bold;
                padding: 4px 8px; background: #333; border-radius: 4px;
            """)
            self._participant_labels.append(lbl)
            # stretch 앞에 삽입
            self.participants_layout.insertWidget(self.participants_layout.count() - 1, lbl)

    def _open_input(self):
        """입력 다이얼로그"""
        if self.sent:
            return
        if not self.participants:
            self._open_config()
            return

        def on_submit(result):
            if result:
                text, files = result
                self._send(text, files)

        dialog = InputDialog(
            self.window(),
            t("dialog.node_input_title", node_id=self.node_id),
            on_submit
        )
        dialog.exec()

    def _send(self, msg, files):
        """메시지 전송"""
        if self.sent:
            return

        self.sent = True
        self.user_message = msg
        self.user_files = files
        self.model = self.participants[0]["model"] if self.participants else None

        self.btn_input.hide()
        self.progress_label.show()

        # 사용자 메시지 표시
        if msg:
            msg_label = QLabel(msg)
            msg_label.setWordWrap(True)
            msg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            msg_label.setStyleSheet("""
                background-color: #27ae60;
                border-radius: 12px; border-top-right-radius: 4px;
                padding: 10px 12px; color: white; font-size: 13px;
            """)
            msg_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.content_layout.addWidget(msg_label)

        self._start_pulse()

        # plugin이 실행 처리
        if self.on_send:
            self.on_send(self.node_id, self.model, msg, self.user_files)

    def set_participant_progress(self, idx, name):
        """현재 발언자 표시"""
        self._current_participant = idx
        self.progress_label.setText(f"{name} ({idx + 1}/{len(self.participants)})")

        # 참가자 하이라이트
        for i, lbl in enumerate(self._participant_labels):
            color = PARTICIPANT_COLORS[i % len(PARTICIPANT_COLORS)]
            if i == idx:
                lbl.setStyleSheet(f"""
                    color: white; font-size: 11px; font-weight: bold;
                    padding: 4px 8px; background: {color}; border-radius: 4px;
                """)
            else:
                lbl.setStyleSheet(f"""
                    color: {color}; font-size: 11px; font-weight: bold;
                    padding: 4px 8px; background: #333; border-radius: 4px;
                """)

        # 새 응답 라벨 생성
        color = PARTICIPANT_COLORS[idx % len(PARTICIPANT_COLORS)]

        # 이름 라벨
        name_label = QLabel(name)
        name_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold; padding: 2px 0;")
        self.content_layout.addWidget(name_label)

        # 응답 라벨
        self._current_response_label = QLabel("")
        self._current_response_label.setWordWrap(True)
        self._current_response_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._current_response_label.setStyleSheet(f"""
            background-color: #2d2d2d;
            border-left: 3px solid {color};
            border-radius: 4px;
            padding: 10px 12px; color: #ddd; font-size: 13px;
        """)
        self.content_layout.addWidget(self._current_response_label)

    def add_response_chunk(self, idx, chunk):
        """응답 청크 추가 (스트리밍)"""
        if self._current_response_label:
            current = self._current_response_label.text()
            self._current_response_label.setText(current + chunk)
            # 스크롤 하단으로
            self.content_area.verticalScrollBar().setValue(
                self.content_area.verticalScrollBar().maximum()
            )

    def finalize_participant(self, idx, response):
        """참가자 응답 완료"""
        self.conversation_log.append({
            "name": self.participants[idx]["name"],
            "content": response
        })
        self._current_response_label = None

    def set_response(self, response, done=False):
        """최종 응답"""
        self.ai_response = response

        if done:
            self._stop_pulse()
            self.progress_label.setText(t("round_table.finished"))

            # 하이라이트 해제
            for i, lbl in enumerate(self._participant_labels):
                color = PARTICIPANT_COLORS[i % len(PARTICIPANT_COLORS)]
                lbl.setStyleSheet(f"""
                    color: {color}; font-size: 11px; font-weight: bold;
                    padding: 4px 8px; background: #333; border-radius: 4px;
                """)

            if self.tokens_in or self.tokens_out:
                self._show_tokens()

    def set_tokens(self, tokens_in, tokens_out):
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out

    def _show_tokens(self):
        self.tokens_label.setText(f"↑{self.tokens_in:,}  ↓{self.tokens_out:,}")
        self.tokens_label.show()

    # ---- 펄스 애니메이션 ----

    def _start_pulse(self):
        self._pulse_phase = 0.0
        self._pulse_active = True
        self.setStyleSheet("")
        self._pulse_timer.start()

    def _stop_pulse(self):
        self._pulse_timer.stop()
        self._pulse_active = False
        self._pulse_bg_color = None
        self._pulse_border_color = None
        self.setStyleSheet("""
            RoundTableWidget {
                background-color: #1e1e1e;
                border: 3px solid #27ae60;
                border-radius: 12px;
            }
        """)

    def _pulse_tick(self):
        self._pulse_phase += 0.05
        v = (math.sin(self._pulse_phase) + 1) / 2

        br = int(39 + v * (46 - 39))
        bg = int(174 + v * (204 - 174))
        bb = int(96 + v * (113 - 96))
        bgb = int(30 + v * (45 - 30))
        self._pulse_bg_color = QColor(30, bgb, 30)
        self._pulse_border_color = QColor(br, bg, bb)
        self.update()

    def paintEvent(self, event):
        if self._pulse_active and self._pulse_bg_color:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(1.5, 1.5, self.width() - 3, self.height() - 3, 12, 12)
            p.fillPath(path, self._pulse_bg_color)
            p.setPen(QPen(self._pulse_border_color, 3))
            p.drawPath(path)
            p.end()
        else:
            super().paintEvent(event)

    def get_data(self):
        """저장용 데이터"""
        return {
            "type": "round_table_node",
            "id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "participants": copy.deepcopy(self.participants),
            "conversation_log": copy.deepcopy(self.conversation_log),
            "user_message": self.user_message,
            "ai_response": self.ai_response,
            "sent": self.sent,
            "pinned": self.pinned,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }
