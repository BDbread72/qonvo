import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QCheckBox, QScrollArea,
    QTextEdit, QLineEdit, QMessageBox, QProgressBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from v.theme import Theme
from v.services import vision

_LIKELIHOOD = {
    "VERY_UNLIKELY": (10, "#4CAF50", "거의 아님"),
    "UNLIKELY":      (30, "#8BC34A", "가능성 낮음"),
    "POSSIBLE":      (50, "#FFC107", "가능성 있음"),
    "LIKELY":        (75, "#FF9800", "가능성 높음"),
    "VERY_LIKELY":   (95, "#F44336", "매우 높음"),
    "UNKNOWN":       (0,  "#9E9E9E", "알 수 없음"),
}

_SAFE_LABELS = {
    "adult": "성인",
    "spoof": "변조",
    "medical": "의료",
    "violence": "폭력",
    "racy": "선정적",
}


class _VisionWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, image_path, features):
        super().__init__()
        self._image_path = image_path
        self._features = features

    def run(self):
        try:
            raw = vision.analyze(self._image_path, self._features)
            self.finished.emit(raw)
        except Exception as e:
            self.error.emit(str(e))


class VisionDialog(QWidget):
    results_ready = pyqtSignal(dict)

    def __init__(self, image_path: str, existing_results: dict | None = None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._image_path = image_path
        self._worker: _VisionWorker | None = None
        self._raw_results: dict | None = None
        self._feature_checks: dict[str, QCheckBox] = {}
        self._section_widgets: dict[str, QTextEdit] = {}
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._setup_ui()
        if existing_results:
            self._raw_results = existing_results
            self._show_results(existing_results)

    def _setup_ui(self):
        name = os.path.basename(self._image_path) if self._image_path else "Unknown"
        self.setWindowTitle(f"Vision Analysis - {name}")
        self.setMinimumSize(500, 400)
        self.resize(600, 500)
        self.setStyleSheet(f"background-color: {Theme.BG_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        path_label = QLabel(name)
        path_label.setStyleSheet(
            f"color: {Theme.TEXT_PRIMARY}; font-size: 14px; font-weight: bold;"
        )
        layout.addWidget(path_label)

        api_bar = QHBoxLayout()
        api_bar.setSpacing(6)
        api_lbl = QLabel("API Key:")
        api_lbl.setStyleSheet(f"color: {Theme.TEXT_SECONDARY}; font-size: 11px;")
        api_bar.addWidget(api_lbl)

        self._api_input = QLineEdit()
        self._api_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_input.setPlaceholderText("Vision API key (saved encrypted)")
        self._api_input.setFixedHeight(26)
        self._api_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Theme.BG_INPUT};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
            }}
        """)
        existing_key = vision.get_vision_api_key()
        if existing_key:
            self._api_input.setText(existing_key)
        api_bar.addWidget(self._api_input, 1)

        btn_save_key = QPushButton("Save")
        btn_save_key.setFixedSize(50, 26)
        btn_save_key.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_SECONDARY};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 4px;
                font-size: 11px;
            }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
        """)
        btn_save_key.clicked.connect(self._save_api_key)
        api_bar.addWidget(btn_save_key)
        layout.addLayout(api_bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {Theme.GRID_LINE};")
        layout.addWidget(sep)

        feat_label = QLabel("Features")
        feat_label.setStyleSheet(
            f"color: {Theme.TEXT_SECONDARY}; font-size: 11px; font-weight: bold;"
        )
        layout.addWidget(feat_label)

        saved_features = vision.get_vision_features()

        feat_grid = QHBoxLayout()
        feat_grid.setSpacing(12)
        for feat_key, feat_name in vision.FEATURES.items():
            cb = QCheckBox(feat_name)
            cb.setStyleSheet(f"""
                QCheckBox {{
                    color: {Theme.TEXT_PRIMARY};
                    font-size: 11px;
                    spacing: 4px;
                }}
                QCheckBox::indicator {{
                    width: 14px; height: 14px;
                    border: 1px solid {Theme.TEXT_TERTIARY};
                    border-radius: 3px;
                    background-color: {Theme.BG_INPUT};
                }}
                QCheckBox::indicator:checked {{
                    background-color: {Theme.ACCENT_PRIMARY};
                    border-color: {Theme.ACCENT_PRIMARY};
                }}
            """)
            if feat_key in saved_features:
                cb.setChecked(True)
            self._feature_checks[feat_key] = cb
            feat_grid.addWidget(cb)
        feat_grid.addStretch()
        layout.addLayout(feat_grid)

        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self._btn_analyze = QPushButton("Analyze")
        self._btn_analyze.setFixedHeight(30)
        self._btn_analyze.setStyleSheet(f"""
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
            QPushButton:disabled {{ background-color: {Theme.BG_SECONDARY}; color: {Theme.TEXT_DISABLED}; }}
        """)
        self._btn_analyze.clicked.connect(self._run_analysis)
        btn_bar.addWidget(self._btn_analyze)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            f"color: {Theme.TEXT_DISABLED}; font-size: 11px;"
        )
        btn_bar.addWidget(self._status_label)
        btn_bar.addStretch()
        layout.addLayout(btn_bar)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {Theme.GRID_LINE};")
        layout.addWidget(sep2)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {Theme.BG_PRIMARY};
                border: none;
            }}
        """)

        self._results_container = QWidget()
        self._results_container.setStyleSheet("background: transparent;")
        self._results_layout = QVBoxLayout(self._results_container)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setSpacing(8)
        self._results_layout.addStretch()

        scroll.setWidget(self._results_container)
        layout.addWidget(scroll, 1)

    def _save_api_key(self):
        key = self._api_input.text().strip()
        if key:
            vision.save_vision_api_key(key)
            self._status_label.setText("Key saved")

    def _run_analysis(self):
        key = self._api_input.text().strip()
        if key and key != vision.get_vision_api_key():
            vision.save_vision_api_key(key)

        if not vision.has_vision_api_key():
            QMessageBox.warning(self, "No API Key", "Please enter a Vision API key.")
            return

        features = [k for k, cb in self._feature_checks.items() if cb.isChecked()]
        if not features:
            QMessageBox.warning(self, "No Features", "Select at least one feature.")
            return

        vision.save_vision_features(features)

        self._btn_analyze.setEnabled(False)
        self._status_label.setText("Analyzing...")
        self._clear_results()

        self._worker = _VisionWorker(self._image_path, features)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, raw: dict):
        self._btn_analyze.setEnabled(True)
        self._status_label.setText("Done")
        self._raw_results = raw
        self._show_results(raw)
        self.results_ready.emit(raw)

    def _on_error(self, msg: str):
        self._btn_analyze.setEnabled(True)
        self._status_label.setText("Error")
        self._add_section("Error", msg)

    def _clear_results(self):
        self._section_widgets.clear()
        while self._results_layout.count() > 0:
            item = self._results_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._results_layout.addStretch()

    def _show_results(self, raw: dict):
        self._clear_results()
        sections = vision.parse_results(raw)
        if not sections:
            self._add_section("Result", "No results returned.")
            return
        for title, content in sections.items():
            if title == "Safe Search":
                continue
            self._add_section(title, content)
        safe = raw.get("safeSearchAnnotation")
        if safe:
            self._add_safe_search_section(safe)

    def _add_section(self, title: str, content: str):
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SECONDARY};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 6px;
            }}
        """)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(10, 8, 10, 8)
        fl.setSpacing(4)

        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {Theme.ACCENT_PRIMARY}; font-size: 12px; "
            f"font-weight: bold; border: none;"
        )
        fl.addWidget(lbl)

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(content)
        text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: transparent;
                color: {Theme.TEXT_PRIMARY};
                border: none;
                font-size: 11px;
            }}
        """)
        line_count = content.count("\n") + 1
        text_edit.setFixedHeight(min(max(line_count * 18 + 10, 40), 200))
        fl.addWidget(text_edit)
        self._section_widgets[title] = text_edit

        idx = self._results_layout.count() - 1
        self._results_layout.insertWidget(idx, frame)

    def _add_safe_search_section(self, safe_data: dict):
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SECONDARY};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 6px;
            }}
        """)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(10, 8, 10, 8)
        fl.setSpacing(6)

        lbl = QLabel("Safe Search")
        lbl.setStyleSheet(
            f"color: {Theme.ACCENT_PRIMARY}; font-size: 12px; "
            f"font-weight: bold; border: none;"
        )
        fl.addWidget(lbl)

        for key, value in safe_data.items():
            pct, color, kr_text = _LIKELIHOOD.get(value, (0, "#9E9E9E", value))
            kr_label = _SAFE_LABELS.get(key, key)

            row = QHBoxLayout()
            row.setSpacing(8)

            name_lbl = QLabel(kr_label)
            name_lbl.setFixedWidth(45)
            name_lbl.setStyleSheet(
                f"color: {Theme.TEXT_PRIMARY}; font-size: 11px; border: none;"
            )
            row.addWidget(name_lbl)

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setFixedHeight(16)
            bar.setTextVisible(False)
            bar.setStyleSheet(f"""
                QProgressBar {{
                    background-color: {Theme.BG_INPUT};
                    border: none;
                    border-radius: 3px;
                }}
                QProgressBar::chunk {{
                    background-color: {color};
                    border-radius: 3px;
                }}
            """)
            row.addWidget(bar, 1)

            val_lbl = QLabel(kr_text)
            val_lbl.setFixedWidth(75)
            val_lbl.setStyleSheet(
                f"color: {color}; font-size: 11px; font-weight: bold; border: none;"
            )
            row.addWidget(val_lbl)

            fl.addLayout(row)

        idx = self._results_layout.count() - 1
        self._results_layout.insertWidget(idx, frame)

    def get_raw_results(self) -> dict | None:
        return self._raw_results
