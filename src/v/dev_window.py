"""
개발자 창
- 프로젝트 JSON 뷰어
- 디버그 로그 콘솔
"""
import sys
import json
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QTextEdit, QVBoxLayout,
    QHBoxLayout, QWidget, QPushButton
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QTextCharFormat

from q import t


class LogStream(QObject):
    """sys.stdout/stderr를 가로채서 시그널로 전달"""

    written = pyqtSignal(str, str)  # (text, level)

    def __init__(self, original, level="info"):
        super().__init__()
        self.original = original
        self.level = level

    def write(self, text):
        if self.original:
            self.original.write(text)
        if text.strip():
            self.written.emit(text, self.level)

    def flush(self):
        if self.original:
            self.original.flush()


class DevWindow(QMainWindow):
    """개발자 도구 창"""

    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window

        self.setWindowTitle(t("dev.title"))
        self.setMinimumSize(700, 500)
        self.resize(800, 600)
        self.setStyleSheet("""
            QMainWindow { background-color: #1a1a1a; }
            QTabWidget::pane {
                border: 1px solid #333;
                background-color: #1a1a1a;
            }
            QTabBar::tab {
                background-color: #2d2d2d;
                color: #aaa;
                padding: 8px 20px;
                border: 1px solid #333;
                border-bottom: none;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #1a1a1a;
                color: #fff;
                border-bottom: 2px solid #0d6efd;
            }
        """)

        # 탭 위젯
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        # === 프로젝트 JSON 탭 ===
        json_widget = QWidget()
        json_layout = QVBoxLayout(json_widget)
        json_layout.setContentsMargins(8, 8, 8, 8)

        json_toolbar = QHBoxLayout()
        btn_refresh = QPushButton(t("button.refresh"))
        btn_refresh.setStyleSheet(
            "padding: 6px 16px; background-color: #2d2d2d; color: #ddd; border-radius: 4px;"
        )
        btn_refresh.clicked.connect(self._refresh_json)
        json_toolbar.addWidget(btn_refresh)

        btn_copy_json = QPushButton(t("button.copy"))
        btn_copy_json.setStyleSheet(
            "padding: 6px 16px; background-color: #2d2d2d; color: #ddd; border-radius: 4px;"
        )
        btn_copy_json.clicked.connect(self._copy_json)
        json_toolbar.addWidget(btn_copy_json)
        json_toolbar.addStretch()
        json_layout.addLayout(json_toolbar)

        self.json_view = QTextEdit()
        self.json_view.setReadOnly(True)
        self.json_view.setFont(QFont("Consolas", 11))
        self.json_view.setStyleSheet("""
            QTextEdit {
                background-color: #0d0d0d;
                color: #8bc34a;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 8px;
            }
        """)
        json_layout.addWidget(self.json_view)
        tabs.addTab(json_widget, t("dev.tab_json"))

        # === 로그 탭 ===
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(8, 8, 8, 8)

        log_toolbar = QHBoxLayout()
        btn_clear = QPushButton(t("button.clear"))
        btn_clear.setStyleSheet(
            "padding: 6px 16px; background-color: #2d2d2d; color: #ddd; border-radius: 4px;"
        )
        btn_clear.clicked.connect(self._clear_log)
        log_toolbar.addWidget(btn_clear)
        log_toolbar.addStretch()
        log_layout.addLayout(log_toolbar)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setStyleSheet("""
            QTextEdit {
                background-color: #0d0d0d;
                color: #ddd;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 8px;
            }
        """)
        log_layout.addWidget(self.log_view)
        tabs.addTab(log_widget, t("dev.tab_log"))

        # stdout/stderr 가로채기
        self._stdout_stream = LogStream(sys.stdout, "info")
        self._stderr_stream = LogStream(sys.stderr, "error")
        self._stdout_stream.written.connect(self._append_log)
        self._stderr_stream.written.connect(self._append_log)
        sys.stdout = self._stdout_stream
        sys.stderr = self._stderr_stream

    def _refresh_json(self):
        """현재 보드 데이터를 JSON으로 표시"""
        if not self.main_window or not self.main_window.current_plugin:
            self.json_view.setPlainText(t("dev.board_not_loaded"))
            return

        try:
            data = self.main_window.current_plugin.collect_data()
            text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
            self.json_view.setPlainText(text)
        except Exception as e:
            self.json_view.setPlainText(t("error.generic", error=str(e)))

    def _copy_json(self):
        """JSON 클립보드에 복사"""
        from PyQt6.QtWidgets import QApplication
        text = self.json_view.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def _clear_log(self):
        self.log_view.clear()

    def _append_log(self, text: str, level: str):
        """로그 메시지 추가"""
        timestamp = datetime.now().strftime("%H:%M:%S")

        cursor = self.log_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)

        # 타임스탬프 (회색)
        fmt_time = QTextCharFormat()
        fmt_time.setForeground(QColor("#666"))
        cursor.insertText(f"[{timestamp}] ", fmt_time)

        # 메시지 (레벨에 따라 색상)
        fmt_msg = QTextCharFormat()
        if level == "error":
            fmt_msg.setForeground(QColor("#ff6b6b"))
        else:
            fmt_msg.setForeground(QColor("#ddd"))
        cursor.insertText(text + "\n", fmt_msg)

        # 스크롤 아래로
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

    def closeEvent(self, event):
        """창 닫기 (stdout 복원하지 않음 - 개발자 모드 유지)"""
        # 숨기기만 하고 삭제하지 않음
        self.hide()
        event.ignore()

    def destroy_streams(self):
        """앱 종료 시 stdout/stderr 복원"""
        if sys.stdout is self._stdout_stream:
            sys.stdout = self._stdout_stream.original
        if sys.stderr is self._stderr_stream:
            sys.stderr = self._stderr_stream.original
