"""
프롬프트 노드 (Prompt Node)
- 연결된 Chat Node에 system_prompt로 주입
- StickyNote 패턴: QWidget + BaseNode, DraggableHeader, QTextEdit, ResizeHandle
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit, QLabel,
    QComboBox, QPushButton, QSpinBox,
)
from PyQt6.QtCore import Qt

from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle

_ROLE_BADGE = {"system": "SP", "user": "UP", "assistant": "AP"}  # 역할별 배지 텍스트


class PromptNodeWidget(QWidget, BaseNode):

    is_prompt_node = True

    def __init__(self, title="", body="", on_modified=None):
        super().__init__()

        self.init_base_node(node_id=None, on_modified=on_modified)

        self._prompt_enabled = True  # 프롬프트 활성 상태

        self.setMinimumSize(200, 140)
        self.resize(260, 200)
        self._apply_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = DraggableHeader(self)
        self.header.setFixedHeight(30)
        self.header.setStyleSheet("""
            QFrame {
                background-color: #9b59b622;
                border: none; border-bottom: 1px solid #9b59b644;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }
        """)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 2, 4, 2)
        header_layout.setSpacing(4)

        self.badge = QLabel("SP")
        self.badge.setFixedSize(24, 20)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setStyleSheet("""
            QLabel {
                background: #9b59b6; color: #fff;
                font-size: 10px; font-weight: bold;
                border-radius: 4px; border: none;
            }
        """)
        header_layout.addWidget(self.badge)

        self.role_combo = QComboBox()  # 역할 선택 드롭다운
        self.role_combo.setFixedWidth(38)
        self.role_combo.setFixedHeight(20)
        self.role_combo.addItem("S", "system")
        self.role_combo.addItem("U", "user")
        self.role_combo.addItem("A", "assistant")
        self.role_combo.setStyleSheet("""
            QComboBox {
                background: #3a2f4d; color: #e0d0f0;
                border: 1px solid #9b59b644; border-radius: 3px;
                font-size: 10px; font-weight: bold; padding: 0 2px;
            }
            QComboBox::drop-down { border: none; width: 12px; }
            QComboBox::down-arrow { image: none; border: none; }
            QComboBox QAbstractItemView {
                background: #2a1f3d; color: #e0d0f0;
                selection-background-color: #9b59b644;
                border: 1px solid #9b59b6;
            }
        """)
        self.role_combo.currentIndexChanged.connect(self._on_role_changed)
        header_layout.addWidget(self.role_combo)

        self.btn_enable = QPushButton("ON")  # 프롬프트 ON/OFF 토글
        self.btn_enable.setCheckable(True)
        self.btn_enable.setChecked(True)
        self.btn_enable.setFixedSize(30, 20)
        self.btn_enable.setStyleSheet("""
            QPushButton {
                background: #27ae60; color: #fff;
                font-size: 9px; font-weight: bold;
                border-radius: 3px; border: none;
            }
            QPushButton:!checked {
                background: #555; color: #999;
            }
        """)
        self.btn_enable.toggled.connect(self._on_enable_toggled)
        header_layout.addWidget(self.btn_enable)

        self.priority_spin = QSpinBox()  # 우선순위 (낮을수록 먼저)
        self.priority_spin.setRange(0, 99)
        self.priority_spin.setValue(0)
        self.priority_spin.setFixedSize(40, 20)
        self.priority_spin.setToolTip("Priority (lower = first)")
        self.priority_spin.setStyleSheet("""
            QSpinBox {
                background: #3a2f4d; color: #e0d0f0;
                border: 1px solid #9b59b644; border-radius: 3px;
                font-size: 10px; padding: 0 2px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 10px; border: none;
            }
        """)
        self.priority_spin.valueChanged.connect(self._on_change)
        header_layout.addWidget(self.priority_spin)

        self.title_edit = QLineEdit(title)
        self.title_edit.setPlaceholderText("Prompt")
        self.title_edit.setStyleSheet("""
            QLineEdit {
                background: transparent; border: none;
                color: #e0d0f0; font-size: 12px; font-weight: bold;
                padding: 0;
            }
        """)
        self.title_edit.textChanged.connect(self._on_change)
        header_layout.addWidget(self.title_edit, 1)

        layout.addWidget(self.header)

        self.body_edit = QTextEdit()
        self.body_edit.setPlaceholderText("System prompt...")
        self.body_edit.setPlainText(body)
        self.body_edit.setStyleSheet("""
            QTextEdit {
                background: transparent; border: none;
                color: #d4c4e8; font-size: 13px; padding: 6px 8px;
            }
        """)
        self.body_edit.textChanged.connect(self._on_change)
        self.body_edit.textChanged.connect(self._update_token_count)  # 실시간 토큰 근사치
        layout.addWidget(self.body_edit)

        self.tokens_label = QLabel("0자")
        self.tokens_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.tokens_label.setFixedHeight(18)
        self.tokens_label.setStyleSheet("""
            QLabel {
                color: #8070a0; font-size: 10px;
                padding-right: 8px; border: none; background: transparent;
            }
        """)
        layout.addWidget(self.tokens_label)

        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)

        self._update_token_count()

    def _apply_style(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #2a1f3d;
                border: 1px solid #9b59b6;
                border-radius: 6px;
            }
        """)

    def _on_change(self):
        if self.on_modified:
            self.on_modified()

    def _on_role_changed(self):
        role = self.role_combo.currentData()
        self.badge.setText(_ROLE_BADGE.get(role, "SP"))  # 배지 업데이트
        placeholder = {
            "system": "System prompt...",
            "user": "User message prefix...",
            "assistant": "Assistant message prefix...",
        }.get(role, "Prompt...")
        self.body_edit.setPlaceholderText(placeholder)
        self._on_change()

    def _on_enable_toggled(self, checked):
        self._prompt_enabled = checked
        self.btn_enable.setText("ON" if checked else "OFF")
        self._apply_enabled_visual()  # 시각 피드백 적용
        self._on_change()

    def _apply_enabled_visual(self):
        if self._prompt_enabled:
            self.body_edit.setStyleSheet("""
                QTextEdit {
                    background: transparent; border: none;
                    color: #d4c4e8; font-size: 13px; padding: 6px 8px;
                }
            """)
        else:
            self.body_edit.setStyleSheet("""
                QTextEdit {
                    background: transparent; border: none;
                    color: #666666; font-size: 13px; padding: 6px 8px;
                }
            """)  # 비활성 시 색상 dim

    def _update_token_count(self):
        text = self.body_edit.toPlainText()
        self.tokens_label.setText(f"{len(text)}자")

    @property
    def prompt_enabled(self):
        return self._prompt_enabled

    @property
    def prompt_role(self):
        return self.role_combo.currentData()

    @property
    def prompt_priority_value(self):
        return self.priority_spin.value()

    def get_data(self):
        return {
            "type": "prompt_node",
            "node_id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "title": self.title_edit.text(),
            "body": self.body_edit.toPlainText(),
            "role": self.role_combo.currentData(),  # 역할
            "enabled": self._prompt_enabled,  # 활성 상태
            "priority": self.priority_spin.value(),  # 우선순위
        }
