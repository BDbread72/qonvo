"""
Preferred Options 결과 선택 창
- 독립 윈도우 (비모달, 메인 캔버스와 동시 조작 가능)
- 풀사이즈 이미지 미리보기
- 0~N개 다중 선택
- 선택 결과를 콜백으로 전달
"""
import base64

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QLabel, QCheckBox, QPushButton, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap

from v.theme import Theme


class PreferredResultsWindow(QWidget):
    """Preferred Options 결과를 풀사이즈로 보여주고 다중 선택하는 독립 창"""

    # 선택 완료 시그널: list[int] (선택된 인덱스)
    selection_confirmed = pyqtSignal(list)
    # 창 닫힘 (선택 없이)
    selection_cancelled = pyqtSignal()

    def __init__(self, results, parent=None):
        """
        Args:
            results: list of (text, images) tuples
                text: str - 결과 텍스트
                images: list - 이미지 데이터 (base64/bytes)
        """
        super().__init__(parent, Qt.WindowType.Window)
        self.results = results
        self._checkboxes: list[QCheckBox] = []
        self._confirmed = False  # U1: closeEvent 전 초기화
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Preferred Options - 결과 선택")
        self.setMinimumSize(600, 400)
        self.resize(800, 550)
        self.setStyleSheet(f"background-color: {Theme.BG_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 스크롤 영역
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {Theme.BG_PRIMARY};
                border: none;
            }}
        """)

        grid_widget = QWidget()
        grid_widget.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(grid_widget)
        self._grid.setSpacing(16)

        cols = min(3, len(self.results))
        if cols == 0:
            cols = 1

        for i, (text, images) in enumerate(self.results):
            card = self._create_card(i, text, images)
            row = i // cols
            col = i % cols
            self._grid.addWidget(card, row, col)

        scroll.setWidget(grid_widget)
        layout.addWidget(scroll, 1)

        # 하단 버튼바
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        btn_select_all = QPushButton("전체 선택")
        btn_select_all.setFixedHeight(32)
        btn_select_all.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_SECONDARY};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 6px;
                padding: 4px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
        """)
        btn_select_all.clicked.connect(self._toggle_all)
        btn_bar.addWidget(btn_select_all)

        btn_bar.addStretch()

        self._btn_confirm = QPushButton("선택 (0개)")
        self._btn_confirm.setFixedHeight(32)
        self._btn_confirm.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.ACCENT_PRIMARY};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 4px 20px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {Theme.ACCENT_HOVER}; }}
        """)
        self._btn_confirm.clicked.connect(self._confirm)
        btn_bar.addWidget(self._btn_confirm)

        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedHeight(32)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_SECONDARY};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 6px;
                padding: 4px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
        """)
        btn_cancel.clicked.connect(self.close)
        btn_bar.addWidget(btn_cancel)

        layout.addLayout(btn_bar)

    def _create_card(self, index, text, images):
        """결과 카드 위젯 생성"""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SECONDARY};
                border: 2px solid {Theme.GRID_LINE};
                border-radius: 10px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(8)

        # 체크박스 + 라벨
        cb = QCheckBox(f"Option {index + 1}")
        cb.setStyleSheet(f"""
            QCheckBox {{
                color: {Theme.TEXT_PRIMARY};
                font-weight: bold;
                font-size: 12px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {Theme.TEXT_TERTIARY};
                border-radius: 4px;
                background-color: {Theme.BG_INPUT};
            }}
            QCheckBox::indicator:checked {{
                background-color: {Theme.ACCENT_PRIMARY};
                border-color: {Theme.ACCENT_PRIMARY};
            }}
        """)
        cb.toggled.connect(self._on_check_changed)
        self._checkboxes.append(cb)
        card_layout.addWidget(cb)

        # 이미지 (있으면)
        if images:
            pixmap = self._decode_pixmap(images[0])
            if pixmap and not pixmap.isNull():
                img_label = QLabel()
                dpr = self.devicePixelRatio() or 1.0
                max_logical = 320
                max_px = int(max_logical * dpr)
                scaled = pixmap.scaled(
                    max_px, max_px,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                scaled.setDevicePixelRatio(dpr)
                img_label.setPixmap(scaled)
                img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img_label.setStyleSheet("border: none; padding: 4px;")
                card_layout.addWidget(img_label)

                size_label = QLabel(f"{pixmap.width()} x {pixmap.height()}")
                size_label.setStyleSheet(
                    f"color: {Theme.TEXT_DISABLED}; font-size: 10px; border: none;"
                )
                size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                card_layout.addWidget(size_label)

        # 텍스트 (있으면)
        if text:
            preview = text[:300] + "..." if len(text) > 300 else text
            text_label = QLabel(preview)
            text_label.setWordWrap(True)
            text_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            text_label.setStyleSheet(
                f"color: {Theme.TEXT_SECONDARY}; font-size: 11px; "
                f"padding: 6px; border: none;"
            )
            card_layout.addWidget(text_label)

        card_layout.addStretch()
        return card

    def _decode_pixmap(self, img_data) -> QPixmap | None:
        """이미지 데이터 -> QPixmap"""
        raw_bytes = None
        if isinstance(img_data, bytes):
            raw_bytes = img_data
        elif isinstance(img_data, str):
            if img_data.startswith("data:image"):
                _, encoded = img_data.split(",", 1)
                raw_bytes = base64.b64decode(encoded)
            else:
                raw_bytes = base64.b64decode(img_data)
        if not raw_bytes:
            return None
        pm = QPixmap()
        pm.loadFromData(raw_bytes)
        return pm

    def _on_check_changed(self):
        """체크 상태 변경 시 버튼 텍스트 업데이트"""
        count = sum(1 for cb in self._checkboxes if cb.isChecked())
        self._btn_confirm.setText(f"선택 ({count}개)")

    def _toggle_all(self):
        """전체 선택 / 해제 토글"""
        all_checked = all(cb.isChecked() for cb in self._checkboxes)
        for cb in self._checkboxes:
            cb.setChecked(not all_checked)

    def _confirm(self):
        """선택 확인 → 시그널 발송 후 창 닫기"""
        self._confirmed = True
        selected = [i for i, cb in enumerate(self._checkboxes) if cb.isChecked()]
        self.selection_confirmed.emit(selected)
        self.close()

    def closeEvent(self, event):
        """창 닫힐 때 취소 시그널 (confirm 안 한 경우)"""
        if not self._confirmed:
            self.selection_cancelled.emit()
        super().closeEvent(event)
