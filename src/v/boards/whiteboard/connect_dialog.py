from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QFrame,
)
from PyQt6.QtCore import Qt

from v.theme import Theme
from v.settings import get_setting, set_setting


_INPUT_STYLE = """
    QLineEdit {
        background-color: #2d2d2d;
        color: #ddd;
        border: 1px solid #444;
        border-radius: 6px;
        padding: 8px 10px;
        font-size: 13px;
    }
    QLineEdit:focus {
        border-color: #0d6efd;
    }
"""

_COMBO_STYLE = """
    QComboBox {
        background-color: #2d2d2d;
        color: #ddd;
        border: 1px solid #444;
        border-radius: 6px;
        padding: 8px 10px;
        font-size: 13px;
    }
    QComboBox:focus { border-color: #0d6efd; }
    QComboBox::drop-down { border: none; }
    QComboBox QAbstractItemView {
        background-color: #2d2d2d;
        color: #ddd;
        selection-background-color: #0d6efd;
    }
"""

_LABEL_STYLE = "color: #bbb; font-size: 12px;"


class ConnectDialog(QDialog):
    """서버 접속 정보(Host/Port/Username/Password) 입력 다이얼로그.

    이전 접속 정보는 settings.json에서 복원한다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Server")
        self.setMinimumWidth(400)
        self.setModal(True)
        self.setStyleSheet(f"QDialog {{ background-color: {Theme.BG_PRIMARY}; }}")

        self._result_data: Optional[dict] = None

        saved = get_setting("server_connection", {})

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("Server Connection")
        title.setStyleSheet(f"color: {Theme.TEXT_PRIMARY}; font-size: 16px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
        layout.addWidget(sep)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._host_input = QLineEdit()
        self._host_input.setPlaceholderText("localhost")
        self._host_input.setText(saved.get("host", "localhost"))
        self._host_input.setStyleSheet(_INPUT_STYLE)
        form.addRow(self._make_label("Host"), self._host_input)

        self._port_input = QLineEdit()
        self._port_input.setPlaceholderText("9700")
        self._port_input.setText(str(saved.get("port", 9700)))
        self._port_input.setStyleSheet(_INPUT_STYLE)
        form.addRow(self._make_label("Port"), self._port_input)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #333;")
        form.addRow(sep2)

        self._user_input = QLineEdit()
        self._user_input.setPlaceholderText("username")
        self._user_input.setText(saved.get("username", ""))
        self._user_input.setStyleSheet(_INPUT_STYLE)
        form.addRow(self._make_label("Username"), self._user_input)

        self._pass_input = QLineEdit()
        self._pass_input.setPlaceholderText("password")
        self._pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pass_input.setStyleSheet(_INPUT_STYLE)
        form.addRow(self._make_label("Password"), self._pass_input)

        layout.addLayout(form)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

        btn_layout = QHBoxLayout()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet("padding: 8px 20px;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_layout.addStretch()

        self._btn_connect = QPushButton("Connect")
        self._btn_connect.setStyleSheet(
            "padding: 8px 24px; background-color: #0d6efd; "
            "color: white; font-weight: bold; border-radius: 6px;"
        )
        self._btn_connect.clicked.connect(self._on_connect)
        btn_layout.addWidget(self._btn_connect)

        layout.addLayout(btn_layout)

        self._pass_input.returnPressed.connect(self._on_connect)

    def _make_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_LABEL_STYLE)
        return lbl

    def _on_connect(self):
        host = self._host_input.text().strip() or "localhost"
        port_str = self._port_input.text().strip() or "9700"
        username = self._user_input.text().strip()
        password = self._pass_input.text()

        if not username:
            self._status_label.setText("Username is required")
            self._status_label.setStyleSheet("color: #ff6b6b; font-size: 11px;")
            return

        if not password:
            self._status_label.setText("Password is required")
            self._status_label.setStyleSheet("color: #ff6b6b; font-size: 11px;")
            return

        try:
            port = int(port_str)
        except ValueError:
            self._status_label.setText("Invalid port number")
            self._status_label.setStyleSheet("color: #ff6b6b; font-size: 11px;")
            return

        set_setting("server_connection", {
            "host": host,
            "port": port,
            "username": username,
        })

        self._result_data = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
        }
        self.accept()

    def get_connection_info(self) -> Optional[dict]:
        return self._result_data


class BoardSelectDialog(QDialog):
    """기존 보드 선택 또는 새 보드 생성 다이얼로그."""

    def __init__(self, boards: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Board")
        self.setMinimumWidth(350)
        self.setModal(True)
        self.setStyleSheet(f"QDialog {{ background-color: {Theme.BG_PRIMARY}; }}")

        self._selected_board: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("Select or Create Board")
        title.setStyleSheet(f"color: {Theme.TEXT_PRIMARY}; font-size: 14px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        if boards:
            lbl = QLabel("Existing boards:")
            lbl.setStyleSheet(_LABEL_STYLE)
            layout.addWidget(lbl)

            self._board_combo = QComboBox()
            self._board_combo.setStyleSheet(_COMBO_STYLE)
            for b in boards:
                self._board_combo.addItem(b)
            layout.addWidget(self._board_combo)
        else:
            self._board_combo = None

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
        layout.addWidget(sep)

        lbl2 = QLabel("Or create new:")
        lbl2.setStyleSheet(_LABEL_STYLE)
        layout.addWidget(lbl2)

        self._new_board_input = QLineEdit()
        self._new_board_input.setPlaceholderText("new_board_name")
        self._new_board_input.setStyleSheet(_INPUT_STYLE)
        layout.addWidget(self._new_board_input)

        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet("padding: 8px 20px;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_layout.addStretch()

        btn_join = QPushButton("Join")
        btn_join.setStyleSheet(
            "padding: 8px 24px; background-color: #0d6efd; "
            "color: white; font-weight: bold; border-radius: 6px;"
        )
        btn_join.clicked.connect(self._on_join)
        btn_layout.addWidget(btn_join)

        layout.addLayout(btn_layout)

        self._new_board_input.returnPressed.connect(self._on_join)

    def _on_join(self):
        new_name = self._new_board_input.text().strip()
        if new_name:
            self._selected_board = new_name
        elif self._board_combo and self._board_combo.count() > 0:
            self._selected_board = self._board_combo.currentText()
        else:
            return

        self.accept()

    def get_board_id(self) -> Optional[str]:
        return self._selected_board
