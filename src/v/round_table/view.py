"""
라운드 테이블 전체화면 뷰
- 메인 인터페이스
- 테이블 시각화 + 채팅 패널 + 컨트롤
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSplitter, QProgressBar, QLineEdit, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut

from v.round_table.config_dialog import RoundTableConfig, RoundTableConfigDialog
from v.round_table.table_widget import RoundTableWidget
from v.round_table.chat_panel import ChatPanel, MessageBubble
from v.round_table.worker import DiscussionWorker, TurnInfo


class ProgressPanel(QFrame):
    """진행상황 패널"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            ProgressPanel {
                background-color: #252530;
                border: 1px solid #3a3a4a;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # 현재 스텝
        self.step_label = QLabel("준비 중...")
        self.step_label.setFont(QFont("맑은 고딕", 12, QFont.Weight.Bold))
        self.step_label.setStyleSheet("color: #8888aa;")
        layout.addWidget(self.step_label)

        # 프로그레스 바
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setStyleSheet("""
            QProgressBar {
                background-color: #1a1a2e;
                border: none;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background-color: #0d6efd;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.progress)

        # 상태 텍스트
        self.status_label = QLabel("")
        self.status_label.setFont(QFont("맑은 고딕", 10))
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

    def set_step(self, step_name: str, round_num: int):
        """스텝 설정"""
        self.step_label.setText(f"{step_name} - Round {round_num}")

    def set_progress(self, value: int, total: int):
        """진행률 설정"""
        percent = int(value / total * 100) if total > 0 else 0
        self.progress.setValue(percent)
        self.status_label.setText(f"{value}/{total} 완료")

    def set_status(self, text: str):
        """상태 텍스트"""
        self.status_label.setText(text)


class ControlPanel(QFrame):
    """컨트롤 패널"""

    pause_clicked = pyqtSignal()
    resume_clicked = pyqtSignal()
    next_step_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_paused = False
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            ControlPanel {
                background-color: #252530;
                border: 1px solid #3a3a4a;
                border-radius: 8px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        # 일시정지/재개 버튼
        self.pause_btn = QPushButton("⏸ 일시정지")
        self.pause_btn.setStyleSheet("""
            QPushButton {
                background-color: #f0ad4e;
                color: #000;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #ec971f; }
        """)
        self.pause_btn.clicked.connect(self._toggle_pause)
        layout.addWidget(self.pause_btn)

        # 다음 스텝 버튼
        next_btn = QPushButton("⏭ 다음 스텝")
        next_btn.setStyleSheet("""
            QPushButton {
                background-color: #5bc0de;
                color: #000;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #31b0d5; }
        """)
        next_btn.clicked.connect(self.next_step_clicked)
        layout.addWidget(next_btn)

        layout.addStretch()

        # 중단 버튼
        stop_btn = QPushButton("■ 중단")
        stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #d9534f;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #c9302c; }
        """)
        stop_btn.clicked.connect(self.stop_clicked)
        layout.addWidget(stop_btn)

    def _toggle_pause(self):
        self._is_paused = not self._is_paused
        if self._is_paused:
            self.pause_btn.setText("▶ 재개")
            self.pause_clicked.emit()
        else:
            self.pause_btn.setText("⏸ 일시정지")
            self.resume_clicked.emit()

    def reset(self):
        """상태 초기화"""
        self._is_paused = False
        self.pause_btn.setText("⏸ 일시정지")


class RoundTableView(QWidget):
    """라운드 테이블 전체화면 뷰"""

    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config: RoundTableConfig | None = None
        self.worker: DiscussionWorker | None = None
        self.current_bubble: MessageBubble | None = None
        self.completed_turns = 0
        self.total_turns = 0
        self._init_ui()
        self._setup_shortcuts()

    def _init_ui(self):
        self.setWindowTitle("Round Table")
        self.setStyleSheet("background-color: #1a1a2e;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 헤더
        header = self._create_header()
        layout.addWidget(header)

        # 메인 콘텐츠 (스플리터)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #3a3a4a;
                width: 2px;
            }
        """)

        # 좌측: 테이블 시각화
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 8, 16)

        self.table_widget = RoundTableWidget()
        left_layout.addWidget(self.table_widget, 1)

        # 진행상황
        self.progress_panel = ProgressPanel()
        left_layout.addWidget(self.progress_panel)

        # 컨트롤
        self.control_panel = ControlPanel()
        self.control_panel.pause_clicked.connect(self._on_pause)
        self.control_panel.resume_clicked.connect(self._on_resume)
        self.control_panel.next_step_clicked.connect(self._on_next_step)
        self.control_panel.stop_clicked.connect(self._on_stop)
        left_layout.addWidget(self.control_panel)

        splitter.addWidget(left_panel)

        # 우측: 채팅 패널
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 16, 16, 16)

        chat_header = QLabel("토론 내역")
        chat_header.setFont(QFont("맑은 고딕", 14, QFont.Weight.Bold))
        chat_header.setStyleSheet("color: #8888aa; padding: 8px 0;")
        right_layout.addWidget(chat_header)

        self.chat_panel = ChatPanel()
        right_layout.addWidget(self.chat_panel, 1)

        splitter.addWidget(right_panel)

        # 스플리터 비율
        splitter.setSizes([500, 600])

        layout.addWidget(splitter, 1)

        # 하단: 주제 입력 (초기 상태)
        self.input_panel = self._create_input_panel()
        layout.addWidget(self.input_panel)

    def _create_header(self) -> QFrame:
        """헤더 생성"""
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background-color: #252530;
                border-bottom: 1px solid #3a3a4a;
            }
        """)
        header.setFixedHeight(50)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 0, 16, 0)

        # 타이틀
        self.title_label = QLabel("🎯 Round Table")
        self.title_label.setFont(QFont("맑은 고딕", 16, QFont.Weight.Bold))
        self.title_label.setStyleSheet("color: #e0e0e0;")
        layout.addWidget(self.title_label)

        layout.addStretch()

        # 설정 버튼
        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(36, 36)
        settings_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                border: none;
                font-size: 18px;
            }
            QPushButton:hover { color: #fff; }
        """)
        settings_btn.clicked.connect(self._show_config)
        layout.addWidget(settings_btn)

        # 닫기 버튼
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(36, 36)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                border: none;
                font-size: 18px;
            }
            QPushButton:hover { color: #d9534f; }
        """)
        close_btn.clicked.connect(self._close)
        layout.addWidget(close_btn)

        return header

    def _create_input_panel(self) -> QFrame:
        """입력 패널 생성"""
        panel = QFrame()
        panel.setStyleSheet("""
            QFrame {
                background-color: #252530;
                border-top: 1px solid #3a3a4a;
            }
        """)
        panel.setFixedHeight(60)

        layout = QHBoxLayout(panel)
        layout.setContentsMargins(16, 10, 16, 10)

        label = QLabel("주제:")
        label.setStyleSheet("color: #888;")
        layout.addWidget(label)

        self.topic_input = QLineEdit()
        self.topic_input.setPlaceholderText("토론할 주제를 입력하세요...")
        self.topic_input.setStyleSheet("""
            QLineEdit {
                background-color: #1a1a2e;
                color: #e0e0e0;
                border: 1px solid #3a3a4a;
                border-radius: 4px;
                padding: 8px 12px;
            }
            QLineEdit:focus {
                border-color: #0d6efd;
            }
        """)
        self.topic_input.returnPressed.connect(self._start_from_input)
        layout.addWidget(self.topic_input, 1)

        start_btn = QPushButton("시작")
        start_btn.setStyleSheet("""
            QPushButton {
                background-color: #0d6efd;
                color: white;
                border: none;
                padding: 8px 24px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #0b5ed7; }
        """)
        start_btn.clicked.connect(self._start_from_input)
        layout.addWidget(start_btn)

        return panel

    def _setup_shortcuts(self):
        """단축키 설정"""
        # ESC: 닫기
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._close)

    def _show_config(self):
        """설정 다이얼로그 표시"""
        dialog = RoundTableConfigDialog(self, self.config)
        if dialog.exec():
            self.config = dialog.get_config()
            self._apply_config()

    def _apply_config(self):
        """설정 적용"""
        if not self.config:
            return

        # 타이틀 업데이트
        self.title_label.setText(f"🎯 Round Table - {self.config.topic}")

        # 테이블 위젯 업데이트
        self.table_widget.set_participants(
            self.config.participants,
            self.config.moderator if self.config.moderator_enabled else None
        )

        # 총 턴 수 계산
        self.total_turns = 0
        for step in self.config.steps:
            self.total_turns += step.max_rounds * len(self.config.participants)
            if self.config.moderator_enabled and self.config.moderator_after_each_round:
                self.total_turns += step.max_rounds

        self.progress_panel.set_progress(0, self.total_turns)

    def _start_from_input(self):
        """입력 패널에서 시작"""
        topic = self.topic_input.text().strip()
        if not topic:
            return

        # 설정 다이얼로그 표시
        self.config = RoundTableConfig(topic=topic)
        dialog = RoundTableConfigDialog(self, self.config)
        if dialog.exec():
            self.config = dialog.get_config()
            self._apply_config()
            self._start_discussion()

    def start_with_config(self, config: RoundTableConfig):
        """설정으로 바로 시작"""
        self.config = config
        self._apply_config()
        self._start_discussion()

    def _start_discussion(self):
        """토론 시작"""
        if not self.config or not self.config.participants:
            return

        # 입력 패널 숨기기
        self.input_panel.hide()

        # 채팅 초기화
        self.chat_panel.clear()
        self.completed_turns = 0
        self.control_panel.reset()

        # 워커 시작
        self.worker = DiscussionWorker(self.config)
        self.worker.turn_started.connect(self._on_turn_started)
        self.worker.token_received.connect(self._on_token_received)
        self.worker.turn_finished.connect(self._on_turn_finished)
        self.worker.step_changed.connect(self._on_step_changed)
        self.worker.discussion_finished.connect(self._on_discussion_finished)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.start()

    def _on_turn_started(self, turn_info: TurnInfo):
        """턴 시작"""
        # 테이블 업데이트
        if not turn_info.is_moderator:
            self.table_widget.set_current_speaker(turn_info.participant_index)

        # 채팅에 메시지 추가
        self.current_bubble = self.chat_panel.add_message(
            turn_info.persona,
            is_moderator=turn_info.is_moderator
        )

        # 상태 업데이트
        self.progress_panel.set_status(f"{turn_info.persona.icon} {turn_info.persona.name} 발언 중...")

    def _on_token_received(self, token: str):
        """토큰 수신"""
        if self.current_bubble:
            current = self.current_bubble.message.content
            self.chat_panel.update_message(self.current_bubble, current + token)

    def _on_turn_finished(self, response: str):
        """턴 완료"""
        if self.current_bubble:
            self.chat_panel.finish_message(self.current_bubble, response)
            self.current_bubble = None

        # 진행률 업데이트
        self.completed_turns += 1
        self.progress_panel.set_progress(self.completed_turns, self.total_turns)

    def _on_step_changed(self, step_name: str, round_num: int):
        """스텝 변경"""
        self.progress_panel.set_step(step_name, round_num)
        self.chat_panel.set_step(step_name, round_num)
        self.table_widget.reset_all()

    def _on_discussion_finished(self):
        """토론 완료"""
        self.progress_panel.set_status("✓ 토론 완료")
        self.table_widget.set_current_speaker(-1)
        self.chat_panel.add_system_message("토론이 완료되었습니다.")

        # 입력 패널 다시 표시
        self.input_panel.show()
        self.topic_input.clear()

    def _on_error(self, error: str):
        """에러 발생"""
        QMessageBox.critical(self, "오류", error)
        self.progress_panel.set_status(f"오류: {error}")

    def _on_pause(self):
        """일시정지"""
        if self.worker:
            self.worker.pause()
            self.progress_panel.set_status("일시정지됨")

    def _on_resume(self):
        """재개"""
        if self.worker:
            self.worker.resume()

    def _on_next_step(self):
        """다음 스텝"""
        if self.worker:
            self.worker.skip_to_next_step()

    def _on_stop(self):
        """중단"""
        if self.worker:
            self.worker.stop()
            self.worker.wait()
            self.progress_panel.set_status("중단됨")
            self.input_panel.show()

    def _close(self):
        """닫기"""
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "확인",
                "토론이 진행 중입니다. 정말 닫으시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.worker.stop()
            self.worker.wait()

        self.closed.emit()
        self.close()

    def closeEvent(self, event):
        """닫기 이벤트"""
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
        event.accept()
