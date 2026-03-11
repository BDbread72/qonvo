import base64

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QLabel, QCheckBox, QPushButton, QFrame,
    QSlider, QComboBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QPixmap, QImage, QPainter

from v.theme import Theme


class _ImageLoader(QThread):
    loaded = pyqtSignal(int, QImage, int, int)

    def __init__(self, index, img_data, max_px, dpr):
        """인덱스와 원본 이미지 데이터, 최대 픽셀 크기, DPI 스케일을 저장한다."""
        super().__init__()
        self._index = index
        self._img_data = img_data
        self._max_px = max_px
        self._dpr = dpr

    def run(self):
        """백그라운드에서 이미지를 디코딩하고 스케일링한 뒤 로드 완료 시그널을 방출한다."""
        import os
        img = None
        if isinstance(self._img_data, str) and os.path.isfile(self._img_data):
            img = QImage(self._img_data)
        else:
            raw = None
            try:
                if isinstance(self._img_data, bytes):
                    raw = self._img_data
                elif isinstance(self._img_data, str):
                    if self._img_data.startswith("data:image"):
                        _, encoded = self._img_data.split(",", 1)
                        raw = base64.b64decode(encoded)
                    else:
                        raw = base64.b64decode(self._img_data)
            except Exception:
                return
            if not raw:
                return
            img = QImage.fromData(raw)
        if img is None or img.isNull():
            return
        orig_w, orig_h = img.width(), img.height()
        max_px = int(self._max_px * self._dpr)
        scaled = img.scaled(
            max_px, max_px,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.loaded.emit(self._index, scaled, orig_w, orig_h)


class PreferredResultsWindow(QWidget):
    selection_confirmed = pyqtSignal(list)
    selection_cancelled = pyqtSignal()
    rework_requested = pyqtSignal()

    def __init__(self, results, parent=None, input_images=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.results = results
        self._checkboxes: list[QCheckBox] = []
        self._confirmed = False
        self._loaders: list[_ImageLoader] = []
        self._img_placeholders: dict[int, QLabel] = {}
        self._size_placeholders: dict[int, QLabel] = {}
        self._loaded_images: dict[int, QImage] = {}
        self._compare_active = False
        self._compare_opacity = 0.5
        self._input_images: list[str] = input_images or []
        self._input_originals: dict[int, QImage] = {}
        self._selected_input_idx: int = 0
        self._rework_in_flight: set[int] = set()
        self._cards: dict[int, QFrame] = {}
        self._text_labels: dict[int, QLabel] = {}
        self._setup_ui()
        self._start_image_loaders()
        if self._input_images:
            self._load_input_image(0)

    def _setup_ui(self):
        """스크롤 그리드와 하단 버튼 바 레이아웃을 구성한다."""
        self.setWindowTitle("Preferred Options - 결과 선택")
        self.setMinimumSize(600, 400)
        self.resize(800, 550)
        self.setStyleSheet(f"background-color: {Theme.BG_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

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

        cols = min(3, len(self.results)) or 1

        for i, (text, images) in enumerate(self.results):
            card = self._create_card(i, text, images)
            self._grid.addWidget(card, i // cols, i % cols)

        scroll.setWidget(grid_widget)
        layout.addWidget(scroll, 1)

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

        self._btn_rework = QPushButton("Rework")
        self._btn_rework.setFixedHeight(32)
        self._btn_rework.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_SECONDARY};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 6px;
                padding: 4px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
            QPushButton:disabled {{ color: {Theme.TEXT_DISABLED}; }}
        """)
        self._btn_rework.clicked.connect(self._request_rework_all)
        btn_bar.addWidget(self._btn_rework)

        if self._input_images:
            self._btn_compare = QPushButton("Show with Original")
            self._btn_compare.setCheckable(True)
            self._btn_compare.setFixedHeight(32)
            self._btn_compare.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Theme.BG_SECONDARY};
                    color: {Theme.TEXT_PRIMARY};
                    border: 1px solid {Theme.GRID_LINE};
                    border-radius: 6px;
                    padding: 4px 12px;
                    font-size: 12px;
                }}
                QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
                QPushButton:checked {{
                    background-color: {Theme.ACCENT_PRIMARY};
                    color: white;
                    border-color: {Theme.ACCENT_PRIMARY};
                }}
            """)
            self._btn_compare.toggled.connect(self._on_compare_toggled)
            btn_bar.addWidget(self._btn_compare)

            if len(self._input_images) > 1:
                import os
                self._combo_input = QComboBox()
                self._combo_input.setFixedHeight(28)
                self._combo_input.setStyleSheet(f"""
                    QComboBox {{
                        background-color: {Theme.BG_INPUT};
                        color: {Theme.TEXT_PRIMARY};
                        border: 1px solid {Theme.GRID_LINE};
                        border-radius: 4px;
                        padding: 2px 8px;
                        font-size: 11px;
                    }}
                    QComboBox::drop-down {{
                        border: none;
                        width: 20px;
                    }}
                    QComboBox QAbstractItemView {{
                        background-color: {Theme.BG_SECONDARY};
                        color: {Theme.TEXT_PRIMARY};
                        selection-background-color: {Theme.ACCENT_PRIMARY};
                    }}
                """)
                for i, path in enumerate(self._input_images):
                    name = os.path.basename(path)
                    self._combo_input.addItem(f"{i+1}. {name}", i)
                self._combo_input.currentIndexChanged.connect(self._on_input_selection_changed)
                self._combo_input.hide()
                btn_bar.addWidget(self._combo_input)

            self._slider_opacity = QSlider(Qt.Orientation.Horizontal)
            self._slider_opacity.setRange(10, 90)
            self._slider_opacity.setValue(50)
            self._slider_opacity.setFixedWidth(80)
            self._slider_opacity.setFixedHeight(28)
            self._slider_opacity.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    height: 4px;
                    background: {Theme.GRID_LINE};
                    border-radius: 2px;
                }}
                QSlider::handle:horizontal {{
                    width: 14px; height: 14px;
                    margin: -5px 0;
                    background: {Theme.ACCENT_PRIMARY};
                    border-radius: 7px;
                }}
            """)
            self._slider_opacity.valueChanged.connect(self._on_opacity_changed)
            self._slider_opacity.hide()
            btn_bar.addWidget(self._slider_opacity)

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
        """후보 카드 위젯 하나(체크박스, 플레이스홀더, 텍스트)를 생성한다."""
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

        if images:
            placeholder = QLabel("이미지 로딩 중...")
            placeholder.setFixedSize(320, 240)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(
                f"color: {Theme.TEXT_DISABLED}; font-size: 11px; border: none;"
            )
            card_layout.addWidget(placeholder, alignment=Qt.AlignmentFlag.AlignCenter)

            size_lbl = QLabel("")
            size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            size_lbl.setStyleSheet(
                f"color: {Theme.TEXT_DISABLED}; font-size: 10px; border: none;"
            )
            card_layout.addWidget(size_lbl)

            self._img_placeholders[index] = placeholder
            self._size_placeholders[index] = size_lbl

        text_label = QLabel("")
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        text_label.setStyleSheet(
            f"color: {Theme.TEXT_SECONDARY}; font-size: 11px; "
            f"padding: 6px; border: none;"
        )
        if text:
            preview = text[:300] + "..." if len(text) > 300 else text
            text_label.setText(preview)
        else:
            text_label.hide()
        card_layout.addWidget(text_label)
        self._text_labels[index] = text_label

        card_layout.addStretch()
        self._cards[index] = card
        return card

    def _start_image_loaders(self):
        """카드마다 로더 스레드를 생성하고 시작한다."""
        dpr = self.devicePixelRatio() or 1.0
        max_logical = 320
        for i, (_, images) in enumerate(self.results):
            if not images or i not in self._img_placeholders:
                continue
            loader = _ImageLoader(i, images[0], max_logical, dpr)
            loader.loaded.connect(self._on_image_loaded)
            self._loaders.append(loader)
            loader.start()

    def _on_image_loaded(self, index, qimage, orig_w, orig_h):
        """로드 완료 시 플레이스홀더를 디코딩된 이미지로 교체한다."""
        placeholder = self._img_placeholders.get(index)
        size_lbl = self._size_placeholders.get(index)
        if placeholder is None:
            return
        dpr = self.devicePixelRatio() or 1.0
        pixmap = QPixmap.fromImage(qimage)
        pixmap.setDevicePixelRatio(dpr)
        placeholder.setPixmap(pixmap)
        placeholder.setFixedSize(
            int(qimage.width() / dpr),
            int(qimage.height() / dpr),
        )
        placeholder.setStyleSheet("border: none; padding: 4px;")
        self._loaded_images[index] = qimage
        if size_lbl is not None:
            size_lbl.setText(f"{orig_w} x {orig_h}")
        if self._compare_active:
            self._refresh_compare()

    def _on_check_changed(self):
        """체크박스 변경 시 확인 버튼 텍스트를 업데이트한다."""
        count = sum(1 for cb in self._checkboxes if cb.isChecked())
        self._btn_confirm.setText(f"선택 ({count}개)")

    def _toggle_all(self):
        """전체 선택 또는 전체 해제를 토글한다."""
        all_checked = all(cb.isChecked() for cb in self._checkboxes)
        for cb in self._checkboxes:
            cb.setChecked(not all_checked)

    def _confirm(self):
        """선택을 확정해 시그널을 방출하고 창을 닫는다."""
        self._confirmed = True
        selected = [i for i, cb in enumerate(self._checkboxes) if cb.isChecked()]
        self.selection_confirmed.emit(selected)
        self.close()

    def _load_input_image(self, idx):
        if idx in self._input_originals:
            return
        if idx < 0 or idx >= len(self._input_images):
            return
        import os
        path = self._input_images[idx]
        if not os.path.exists(path):
            return
        img = QImage(path)
        if not img.isNull():
            dpr = self.devicePixelRatio() or 1.0
            max_px = int(320 * dpr)
            self._input_originals[idx] = img.scaled(
                max_px, max_px,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

    def _on_compare_toggled(self, checked):
        self._compare_active = checked
        if hasattr(self, '_slider_opacity'):
            self._slider_opacity.setVisible(checked)
        if hasattr(self, '_combo_input'):
            self._combo_input.setVisible(checked)
        if checked:
            self._refresh_compare()
        else:
            self._restore_original_images()

    def _on_input_selection_changed(self, combo_idx):
        if combo_idx < 0:
            return
        self._selected_input_idx = combo_idx
        self._load_input_image(combo_idx)
        if self._compare_active:
            self._refresh_compare()

    def _on_opacity_changed(self, value):
        self._compare_opacity = value / 100.0
        if self._compare_active:
            self._refresh_compare()

    def _refresh_compare(self):
        orig_img = self._input_originals.get(self._selected_input_idx)
        if orig_img is None:
            return
        dpr = self.devicePixelRatio() or 1.0
        opacity = self._compare_opacity

        for idx, placeholder in self._img_placeholders.items():
            cand_img = self._loaded_images.get(idx)
            if cand_img is None:
                continue
            tw = max(orig_img.width(), cand_img.width())
            th = max(orig_img.height(), cand_img.height())
            composed = QImage(tw, th, QImage.Format.Format_ARGB32)
            composed.fill(Qt.GlobalColor.transparent)
            p = QPainter(composed)
            p.setOpacity(opacity)
            p.drawImage(
                0, 0,
                orig_img.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation),
            )
            p.setOpacity(1.0 - opacity)
            p.drawImage(
                0, 0,
                cand_img.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation),
            )
            p.end()
            pm = QPixmap.fromImage(composed)
            pm.setDevicePixelRatio(dpr)
            placeholder.setPixmap(pm)
            placeholder.setFixedSize(int(tw / dpr), int(th / dpr))

    def _restore_original_images(self):
        dpr = self.devicePixelRatio() or 1.0
        for idx, placeholder in self._img_placeholders.items():
            img = self._loaded_images.get(idx)
            if img is None:
                continue
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(dpr)
            placeholder.setPixmap(pm)
            placeholder.setFixedSize(int(img.width() / dpr), int(img.height() / dpr))

    def _request_rework_all(self):
        if self._rework_in_flight:
            return
        self._rework_in_flight.update(range(len(self.results)))
        self._btn_rework.setEnabled(False)
        self._btn_rework.setText("Reworking...")
        self.rework_requested.emit()

    def update_card(self, index, text, images):
        self._rework_in_flight.discard(index)
        if not self._rework_in_flight:
            self._btn_rework.setEnabled(True)
            self._btn_rework.setText("Rework")

        if index < len(self.results):
            self.results[index] = (text, images)

        text_lbl = self._text_labels.get(index)
        if text_lbl:
            if text:
                preview = text[:300] + "..." if len(text) > 300 else text
                text_lbl.setText(preview)
                text_lbl.show()
            else:
                text_lbl.setText("")
                text_lbl.hide()

        if images and index in self._img_placeholders:
            dpr = self.devicePixelRatio() or 1.0
            loader = _ImageLoader(index, images[0], 320, dpr)
            loader.loaded.connect(self._on_image_loaded)
            self._loaders.append(loader)
            loader.start()

    def closeEvent(self, event):
        """로더 스레드를 기다리고, 확정되지 않았으면 취소 시그널을 방출한다."""
        for loader in self._loaders:
            loader.quit()
            loader.wait(500)
        if not self._confirmed:
            self.selection_cancelled.emit()
        super().closeEvent(event)
