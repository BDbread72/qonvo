import os
import uuid
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QFont

from v.theme import Theme
from v.board import BoardManager
from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle


class UpsWorker(QThread):
    finished_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, input_path, scale, output_dir):
        super().__init__()
        self.input_path = input_path
        self.scale = scale
        self.output_dir = output_dir

    def run(self):
        try:
            from PIL import Image

            input_path = Path(self.input_path)
            if not input_path.exists():
                self.error_signal.emit(f"File not found: {self.input_path}")
                return

            img = Image.open(input_path)
            new_w = img.width * self.scale
            new_h = img.height * self.scale

            ort_available = False
            try:
                import onnxruntime as ort
                models_dir = Path(os.environ.get("APPDATA", "")) / "Qonvo" / "models"
                model_path = models_dir / f"realesrgan_x{self.scale}.onnx"
                if model_path.exists():
                    session = ort.InferenceSession(str(model_path))
                    import numpy as np
                    img_rgb = img.convert("RGB")
                    arr = np.array(img_rgb).astype(np.float32) / 255.0
                    arr = np.transpose(arr, (2, 0, 1))
                    arr = np.expand_dims(arr, axis=0)
                    input_name = session.get_inputs()[0].name
                    output_name = session.get_outputs()[0].name
                    result = session.run([output_name], {input_name: arr})[0]
                    result = np.squeeze(result, axis=0)
                    result = np.transpose(result, (1, 2, 0))
                    result = np.clip(result * 255.0, 0, 255).astype(np.uint8)
                    upscaled = Image.fromarray(result)
                    ort_available = True
                else:
                    ort_available = False
            except ImportError:
                ort_available = False
            except Exception:
                ort_available = False

            if not ort_available:
                upscaled = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            output_dir = Path(self.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            out_name = f"{uuid.uuid4().hex}.png"
            out_path = output_dir / out_name
            upscaled.save(str(out_path), "PNG")

            self.finished_signal.emit(str(out_path))

        except Exception as e:
            self.error_signal.emit(str(e))


class UpsNodeWidget(QWidget, BaseNode):

    _board_temp_dir: str | None = None

    def __init__(self, node_id, on_modified=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        self.init_base_node(node_id=node_id, on_modified=on_modified)

        self.ai_response = None
        self._last_result_path = None
        self._worker = None
        self._scale = 2

        self.setMinimumSize(200, 150)
        self.resize(220, 200)
        self.setStyleSheet(f"""
            UpsNodeWidget {{
                background-color: {Theme.BG_SECONDARY};
                border: 2px solid {Theme.NODE_BORDER};
                border-radius: 8px;
            }}
        """)

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = DraggableHeader(self)
        self.header.setFixedHeight(30)
        self.header.setStyleSheet(f"""
            QFrame {{
                background-color: #1a3a5c;
                border: none;
                border-bottom: 1px solid #2a5a8c;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }}
        """)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 2, 8, 2)
        header_layout.setSpacing(6)

        title_label = QLabel(f"Ups #{self.node_id}")
        title_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #5dade2; border: none; background: transparent;")
        self._title_label = title_label
        header_layout.addWidget(title_label, 1)

        layout.addWidget(self.header)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(8, 6, 8, 2)
        controls_layout.setSpacing(6)

        scale_label = QLabel("Scale:")
        scale_label.setStyleSheet("color: #aaa; font-size: 10px; border: none; background: transparent;")
        controls_layout.addWidget(scale_label)

        self.scale_combo = QComboBox()
        self.scale_combo.addItem("2x", 2)
        self.scale_combo.addItem("4x", 4)
        self.scale_combo.setFixedWidth(55)
        self.scale_combo.setFixedHeight(22)
        self.scale_combo.setStyleSheet(f"""
            QComboBox {{
                background: {Theme.BG_PRIMARY};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid #555;
                border-radius: 3px;
                font-size: 10px;
                padding: 0 4px;
            }}
            QComboBox::drop-down {{ border: none; width: 14px; }}
            QComboBox::down-arrow {{ image: none; border: none; }}
            QComboBox QAbstractItemView {{
                background: {Theme.BG_PRIMARY};
                color: {Theme.TEXT_PRIMARY};
                selection-background-color: #3a3a5c;
                border: 1px solid #555;
            }}
        """)
        self.scale_combo.currentIndexChanged.connect(self._on_scale_changed)
        controls_layout.addWidget(self.scale_combo)

        controls_layout.addStretch()
        layout.addLayout(controls_layout)

        self.status_label = QLabel("Idle")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setFixedHeight(18)
        self.status_label.setStyleSheet("color: #888; font-size: 10px; border: none; background: transparent;")
        layout.addWidget(self.status_label)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(60)
        self.preview_label.setStyleSheet("border: none; background: transparent;")
        layout.addWidget(self.preview_label, 1)

        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)

    def _on_scale_changed(self):
        self._scale = self.scale_combo.currentData()
        if self.on_modified:
            self.on_modified()

    def on_signal_input(self, input_data=None):
        self.sent = False
        file_path = input_data or self._collect_input_data()
        if not file_path:
            self.status_label.setText("No input")
            self.status_label.setStyleSheet("color: #e74c3c; font-size: 10px; border: none; background: transparent;")
            return

        if isinstance(file_path, str):
            file_path = file_path.strip()

        if not Path(file_path).exists():
            self.status_label.setText("File not found")
            self.status_label.setStyleSheet("color: #e74c3c; font-size: 10px; border: none; background: transparent;")
            return

        self.status_label.setText("Processing...")
        self.status_label.setStyleSheet("color: #f39c12; font-size: 10px; border: none; background: transparent;")

        board_name = "untitled"
        if self._board_temp_dir:
            output_dir = Path(self._board_temp_dir)
        else:
            output_dir = BoardManager.get_boards_dir() / '.temp' / board_name / 'attachments'
        output_dir.mkdir(parents=True, exist_ok=True)

        self._worker = UpsWorker(file_path, self._scale, str(output_dir))
        self._worker.finished_signal.connect(self._on_finished)
        self._worker.error_signal.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, result_path):
        self.ai_response = result_path
        self._last_result_path = result_path
        self._worker = None
        self._on_done()

    def _on_error(self, error_msg):
        self.status_label.setText(f"Error")
        self.status_label.setToolTip(error_msg)
        self.status_label.setStyleSheet("color: #e74c3c; font-size: 10px; border: none; background: transparent;")
        self._worker = None

    def _on_done(self):
        self.status_label.setText("Done")
        self.status_label.setStyleSheet("color: #27ae60; font-size: 10px; border: none; background: transparent;")
        self._update_preview()
        if self.on_modified:
            self.on_modified()

    def _update_preview(self):
        if not self._last_result_path or not Path(self._last_result_path).exists():
            return
        pixmap = QPixmap(self._last_result_path)
        if pixmap.isNull():
            return
        preview_w = self.preview_label.width() - 4
        preview_h = self.preview_label.height() - 4
        if preview_w > 0 and preview_h > 0:
            scaled = pixmap.scaled(
                preview_w, preview_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_preview()

    def get_data(self):
        return {
            "type": "ups_node",
            "node_id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "scale": self._scale,
            "last_result_path": self._last_result_path,
        }

    @staticmethod
    def from_dict(data, on_modified=None):
        widget = UpsNodeWidget(
            node_id=data.get("node_id"),
            on_modified=on_modified,
        )
        scale = data.get("scale", 2)
        widget._scale = scale
        idx = widget.scale_combo.findData(scale)
        if idx >= 0:
            widget.scale_combo.setCurrentIndex(idx)
        widget._last_result_path = data.get("last_result_path")
        widget.ai_response = widget._last_result_path
        if widget._last_result_path:
            widget.status_label.setText("Done")
            widget.status_label.setStyleSheet("color: #27ae60; font-size: 10px; border: none; background: transparent;")
            widget._update_preview()
        return widget
