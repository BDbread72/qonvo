import os
import uuid

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QFont

from v.theme import Theme
from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle


class RmvWorker(QThread):

    finished_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, input_path: str, output_dir: str):
        super().__init__()
        self.input_path = input_path
        self.output_dir = output_dir

    def run(self):
        try:
            from rembg import remove
            from PIL import Image

            input_image = Image.open(self.input_path)
            result = remove(input_image)

            os.makedirs(self.output_dir, exist_ok=True)
            filename = uuid.uuid4().hex + ".png"
            result_path = os.path.join(self.output_dir, filename)
            result.save(result_path, "PNG")

            self.finished_signal.emit(result_path)
        except Exception as e:
            self.error_signal.emit(str(e))


class RmvNodeWidget(QWidget, BaseNode):

    _board_temp_dir: str | None = None

    def __init__(self, node_id, on_modified=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        self.init_base_node(node_id=node_id, on_modified=on_modified)

        self.ai_response = None
        self._last_result_path = None
        self._worker = None

        self.setMinimumSize(200, 150)
        self.resize(220, 200)
        self.setStyleSheet(f"""
            RmvNodeWidget {{
                background-color: {Theme.BG_TERTIARY};
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
                background-color: {Theme.NODE_HEADER};
                border: none;
                border-bottom: 1px solid {Theme.NODE_BORDER};
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }}
        """)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 2, 8, 2)
        header_layout.setSpacing(4)

        self.title_label = QLabel(f"Rmv #{self.node_id}")
        self.title_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.title_label.setStyleSheet(f"color: {Theme.TEXT_PRIMARY}; border: none;")
        header_layout.addWidget(self.title_label, 1)

        layout.addWidget(self.header)

        body_layout = QVBoxLayout()
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(4)

        self.status_label = QLabel("Idle")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {Theme.TEXT_SECONDARY};
                font-size: 10px;
                border: none;
                background: transparent;
            }}
        """)
        body_layout.addWidget(self.status_label)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(100, 80)
        self.preview_label.setStyleSheet(f"""
            QLabel {{
                background-color: {Theme.BG_INPUT};
                border: 1px solid {Theme.NODE_BORDER};
                border-radius: 4px;
            }}
        """)
        body_layout.addWidget(self.preview_label, 1)

        layout.addLayout(body_layout)

        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)

    def on_signal_input(self, input_data=None):
        if self._worker is not None and self._worker.isRunning():
            return

        file_path = self._resolve_input_file()
        if not file_path:
            self.status_label.setText("No input file")
            self.status_label.setStyleSheet(f"""
                QLabel {{
                    color: {Theme.ACCENT_WARNING};
                    font-size: 10px;
                    border: none;
                    background: transparent;
                }}
            """)
            return

        output_dir = self._board_temp_dir or os.path.join(
            os.environ.get("APPDATA", ""), "Qonvo", "boards", ".temp", "rmv_output"
        )

        self.status_label.setText("Processing...")
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {Theme.ACCENT_PRIMARY};
                font-size: 10px;
                border: none;
                background: transparent;
            }}
        """)

        self._worker = RmvWorker(file_path, output_dir)
        self._worker.finished_signal.connect(self._on_finished)
        self._worker.error_signal.connect(self._on_error)
        self._worker.start()

    def _resolve_input_file(self):
        port = self.input_port
        if port is None:
            return None
        if not port.edges:
            return None

        source_proxy = port.edges[0].source_port.parent_proxy
        if not source_proxy:
            return None
        source_node = source_proxy.widget() if hasattr(source_proxy, 'widget') else source_proxy

        if hasattr(source_node, 'ai_response') and source_node.ai_response:
            candidate = source_node.ai_response
            if isinstance(candidate, str) and os.path.isfile(candidate):
                return candidate

        if hasattr(source_node, 'image_path') and source_node.image_path:
            candidate = source_node.image_path
            if isinstance(candidate, str) and os.path.isfile(candidate):
                return candidate

        if hasattr(source_node, 'ai_image_paths') and source_node.ai_image_paths:
            for candidate in source_node.ai_image_paths:
                if isinstance(candidate, str) and os.path.isfile(candidate):
                    return candidate

        return None

    def _on_finished(self, result_path: str):
        self.ai_response = result_path
        self._last_result_path = result_path
        self._worker = None

        self.status_label.setText("Done")
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {Theme.ACCENT_SUCCESS};
                font-size: 10px;
                border: none;
                background: transparent;
            }}
        """)

        self._update_preview(result_path)

        if self.on_modified:
            self.on_modified()

    def _on_error(self, err: str):
        self._worker = None
        self.status_label.setText(f"Error: {err[:60]}")
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {Theme.ACCENT_DANGER};
                font-size: 10px;
                border: none;
                background: transparent;
            }}
        """)

    def _update_preview(self, image_path: str):
        if not image_path or not os.path.isfile(image_path):
            return
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            return
        scaled = pixmap.scaled(
            self.preview_label.width(),
            self.preview_label.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def get_data(self):
        return {
            "type": "rmv_node",
            "node_id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "last_result_path": self._last_result_path,
        }
