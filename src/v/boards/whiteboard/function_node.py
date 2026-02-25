"""
함수 노드 위젯 - 라이브러리에서 함수를 선택하고 신호로 실행
- 함수 선택: FunctionLibraryDialog로 함수 선택
- 신호 기반 실행: ⚡ 실행 신호 포트로 함수 실행
- 입출력: 단자를 통한 데이터 흐름
- 신호 완료: 함수 완료 후 ⚡ 완료 신호 발송
"""
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QApplication, QComboBox, QSpinBox, QDoubleSpinBox, QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from q import t
from v.theme import Theme
from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle


class FunctionNodeWidget(QWidget, BaseNode):
    """함수 라이브러리에서 선택한 함수를 실행하는 노드"""

    def __init__(self, node_id, on_send=None, on_modified=None, on_open_library=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # Initialize BaseNode
        self.init_base_node(node_id=node_id, on_modified=on_modified)

        # Function node specific attributes
        self.on_send = on_send
        self.on_open_library = on_open_library  # plugin의 _open_function_library
        self.function_id = None  # 선택된 함수 ID
        self.function_name = None  # 선택된 함수 이름
        self.sent = False
        self.ai_response = None
        self.node_options = {}
        self.pinned = False
        self.tokens_in = 0
        self.tokens_out = 0
        self.notify_on_complete = False
        self.preferred_options_enabled = False
        self.preferred_options_count = 3
        self.pending_results = []
        self._on_preferred_selected = None

        self.setMinimumSize(280, 200)
        self.resize(280, 200)
        self.setStyleSheet(
            f"""
            FunctionNodeWidget {{
                background-color: {Theme.BG_TERTIARY};
                border: 3px solid {Theme.ACCENT_PRIMARY};
                border-radius: 12px;
            }}
            """
        )

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(0)

        # 헤더
        self.header = DraggableHeader(self)
        self.header.setFixedHeight(36)
        self.header.setStyleSheet(
            f"""
            DraggableHeader {{
                background-color: {Theme.NODE_HEADER};
                border-top-left-radius: 9px;
                border-top-right-radius: 9px;
                border-bottom: 1px solid {Theme.BG_HOVER};
            }}
            """
        )
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        self.title_label = QLabel(f"#{self.node_id} Function")
        self.title_label.setStyleSheet(
            f"color: {Theme.TEXT_SECONDARY}; font-weight: bold; font-size: 12px;"
        )
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.btn_pin = QPushButton("P")
        self.btn_pin.setFixedSize(28, 28)
        self.btn_pin.setCheckable(True)
        self.btn_pin.setStyleSheet(
            f"""
            QPushButton {{ background: transparent; border: none; font-size: 14px; border-radius: 4px; opacity: 0.4; }}
            QPushButton:checked {{ background-color: #3a3e1e; }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
            """
        )
        self.btn_pin.setToolTip(t("tooltip.pin") if hasattr(t, '__call__') else "Pin")
        self.btn_pin.toggled.connect(self._toggle_pin)
        header_layout.addWidget(self.btn_pin)

        self.btn_notify = QPushButton("N")
        self.btn_notify.setFixedSize(28, 28)
        self.btn_notify.setCheckable(True)
        self.btn_notify.setStyleSheet(
            f"""
            QPushButton {{ background: transparent; border: none; font-size: 14px; border-radius: 4px; }}
            QPushButton:checked {{ background-color: #1e3a1e; color: {Theme.ACCENT_SUCCESS}; }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
            """
        )
        self.btn_notify.setToolTip(t("tooltip.notify_on_complete"))
        self.btn_notify.toggled.connect(lambda c: setattr(self, 'notify_on_complete', c))
        header_layout.addWidget(self.btn_notify)

        layout.addWidget(self.header)

        # 함수 선택 영역
        func_bar = QWidget()
        func_bar.setStyleSheet(f"background-color: {Theme.BG_INPUT}; border: none;")
        func_layout = QHBoxLayout(func_bar)
        func_layout.setContentsMargins(12, 6, 12, 6)
        func_layout.setSpacing(8)

        # 함수 이름 라벨
        self.func_name_label = QLabel("(함수 선택 필요)")
        self.func_name_label.setStyleSheet(
            f"color: {Theme.TEXT_SECONDARY}; font-size: 11px; font-weight: bold;"
        )
        self.func_name_label.setMinimumHeight(20)
        func_layout.addWidget(self.func_name_label, 1)

        # 함수 선택 버튼
        btn_select = QPushButton("선택")
        btn_select.setFixedSize(60, 24)
        btn_select.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {Theme.ACCENT_PRIMARY}; color: white; border: none;
                border-radius: 4px; font-weight: bold; font-size: 11px;
            }}
            QPushButton:hover {{ background-color: {Theme.ACCENT_HOVER}; }}
            """
        )
        btn_select.clicked.connect(self._select_function)
        func_layout.addWidget(btn_select)

        # 비율 콤보
        ratio_label = QLabel("비율")
        ratio_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px;")
        func_layout.addWidget(ratio_label)

        self.ratio_combo = QComboBox()
        self.ratio_combo.addItems(["1:1", "16:9", "9:16", "4:3", "3:4"])
        self.ratio_combo.setFixedWidth(62)
        self.ratio_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 3px 5px; font-size: 10px;
            }}
            QComboBox:hover {{ border-color: {Theme.ACCENT_PRIMARY}; }}
            QComboBox::drop-down {{ border: none; width: 16px; }}
            QComboBox::down-arrow {{
                image: none; border-left: 3px solid transparent;
                border-right: 3px solid transparent; border-top: 4px solid {Theme.TEXT_SECONDARY};
                margin-right: 3px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Theme.BG_SECONDARY}; color: {Theme.TEXT_PRIMARY};
                border: 1px solid #444; selection-background-color: {Theme.ACCENT_PRIMARY};
            }}
            """
        )
        func_layout.addWidget(self.ratio_combo)

        # ⚙ 옵션 토글 버튼
        self.btn_opts_toggle = QPushButton("G")
        self.btn_opts_toggle.setFixedSize(24, 24)
        self.btn_opts_toggle.setCheckable(True)
        self.btn_opts_toggle.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; font-size: 12px;
                border-radius: 4px; color: {Theme.TEXT_TERTIARY};
            }}
            QPushButton:checked {{ background-color: {Theme.ACCENT_PRIMARY}; color: white; }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
        """)
        self.btn_opts_toggle.setToolTip("Generation Options")
        self.btn_opts_toggle.clicked.connect(self._toggle_opts_panel)
        func_layout.addWidget(self.btn_opts_toggle)

        layout.addWidget(func_bar)

        # 생성 옵션 패널 (기본 숨김)
        self.opts_panel = QFrame()
        self.opts_panel.setStyleSheet(f"QFrame {{ background-color: {Theme.BG_INPUT}; border: none; }}")
        opts_layout = QHBoxLayout(self.opts_panel)
        opts_layout.setContentsMargins(12, 4, 12, 4)
        opts_layout.setSpacing(6)

        t_label = QLabel("T:")
        t_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px;")
        opts_layout.addWidget(t_label)

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setDecimals(2)
        self.temp_spin.setValue(1.0)
        self.temp_spin.setFixedWidth(58)
        self.temp_spin.setStyleSheet(f"""
            QDoubleSpinBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 2px; font-size: 10px;
            }}
        """)
        opts_layout.addWidget(self.temp_spin)

        p_label = QLabel("P:")
        p_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px;")
        opts_layout.addWidget(p_label)

        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setRange(0.0, 1.0)
        self.top_p_spin.setSingleStep(0.05)
        self.top_p_spin.setDecimals(2)
        self.top_p_spin.setValue(0.95)
        self.top_p_spin.setFixedWidth(58)
        self.top_p_spin.setStyleSheet(f"""
            QDoubleSpinBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 2px; font-size: 10px;
            }}
        """)
        opts_layout.addWidget(self.top_p_spin)

        max_label = QLabel("Max:")
        max_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px;")
        opts_layout.addWidget(max_label)

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(1, 65536)
        self.max_tokens_spin.setValue(8192)
        self.max_tokens_spin.setFixedWidth(68)
        self.max_tokens_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 2px; font-size: 10px;
            }}
        """)
        opts_layout.addWidget(self.max_tokens_spin)
        opts_layout.addStretch()

        self.opts_panel.hide()
        layout.addWidget(self.opts_panel)

        # Preferred Options 바
        pref_bar = QFrame()
        pref_bar.setStyleSheet(f"QFrame {{ background-color: {Theme.BG_INPUT}; border: none; }}")
        pref_layout = QHBoxLayout(pref_bar)
        pref_layout.setContentsMargins(12, 4, 12, 4)
        pref_layout.setSpacing(6)

        self.btn_pref_toggle = QPushButton("Preferred")
        self.btn_pref_toggle.setFixedHeight(24)
        self.btn_pref_toggle.setCheckable(True)
        self.btn_pref_toggle.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {Theme.TEXT_TERTIARY}; border: 1px solid {Theme.TEXT_DISABLED};
                border-radius: 4px; font-size: 10px; padding: 2px 8px;
            }}
            QPushButton:checked {{
                background-color: {Theme.ACCENT_PRIMARY}; color: white; border: 1px solid {Theme.ACCENT_PRIMARY};
            }}
        """)
        self.btn_pref_toggle.setToolTip(t("tooltip.preferred_options"))
        self.btn_pref_toggle.toggled.connect(self._toggle_preferred)
        pref_layout.addWidget(self.btn_pref_toggle)

        x_label = QLabel("x")
        x_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px;")
        pref_layout.addWidget(x_label)

        self.pref_count_spin = QSpinBox()
        self.pref_count_spin.setRange(2, 32)
        self.pref_count_spin.setValue(3)
        self.pref_count_spin.setFixedWidth(50)
        self.pref_count_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 2px; font-size: 10px;
            }}
        """)
        self.pref_count_spin.setEnabled(False)
        self.pref_count_spin.valueChanged.connect(lambda v: setattr(self, 'preferred_options_count', v))
        pref_layout.addWidget(self.pref_count_spin)

        pref_layout.addStretch()
        layout.addWidget(pref_bar)

        # 결과 영역
        self.result_area = QScrollArea()
        self.result_area.setWidgetResizable(True)
        self.result_area.setStyleSheet(
            f"QScrollArea {{ background-color: {Theme.BG_TERTIARY}; border: none; }}"
        )

        self.result_widget = QWidget()
        self.result_widget.setStyleSheet("background: transparent;")
        self.result_layout = QVBoxLayout(self.result_widget)
        self.result_layout.setContentsMargins(10, 10, 10, 10)
        self.result_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.result_area.setWidget(self.result_widget)
        layout.addWidget(self.result_area)

        self._result_label = None
        self.tokens_label = QLabel("")
        self.tokens_label.setStyleSheet(
            f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; padding: 2px 10px;"
        )
        self.tokens_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.tokens_label.hide()
        layout.addWidget(self.tokens_label)

        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)
        self.resize_handle.raise_()

    @property
    def input_port(self):
        if "_default" in self.input_ports:
            return self.input_ports["_default"]
        if self.input_ports:
            return next(iter(self.input_ports.values()))
        return None

    @input_port.setter
    def input_port(self, value):
        if value is None:
            self.input_ports = {}
        else:
            self.input_ports["_default"] = value

    @property
    def output_port(self):
        if "_default" in self.output_ports:
            return self.output_ports["_default"]
        if self.output_ports:
            return next(iter(self.output_ports.values()))
        return None

    @output_port.setter
    def output_port(self, value):
        if value is None:
            self.output_ports = {}
        else:
            self.output_ports["_default"] = value

    def _toggle_opts_panel(self):
        self.opts_panel.setVisible(self.btn_opts_toggle.isChecked())

    def _toggle_preferred(self, checked):
        self.preferred_options_enabled = checked
        self.pref_count_spin.setEnabled(checked)

    def show_preferred_results(self, results):
        """결과 준비 완료 — 노드에 '결과 보기' 버튼 표시 (창은 사용자가 클릭 시 열림)."""
        self.pending_results = results
        self._pref_window = None

        # 노드 내부에 "결과 보기" 버튼 표시
        self._clear_response()
        count = len(results)
        status = QLabel(f"{count}개 결과 준비됨")
        status.setStyleSheet(
            f"color: {Theme.ACCENT_SUCCESS}; font-size: 11px; font-weight: bold; "
            f"padding: 6px; border: none;"
        )
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_layout.addWidget(status)

        btn_view = QPushButton(f"결과 보기 ({count}개)")
        btn_view.setFixedHeight(36)
        btn_view.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.ACCENT_PRIMARY};
                color: white; border: none; border-radius: 8px;
                font-weight: bold; font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {Theme.ACCENT_HOVER}; }}
        """)
        btn_view.clicked.connect(self._open_preferred_window)
        self.result_layout.addWidget(btn_view)

    def _open_preferred_window(self):
        """결과 선택 창 열기 (사용자 클릭 시)"""
        if not self.pending_results:
            return

        # 이미 열려있으면 앞으로 가져오기만
        if self._pref_window is not None:
            self._pref_window.raise_()
            self._pref_window.activateWindow()
            return

        from .preferred_dialog import PreferredResultsWindow

        self._pref_window = PreferredResultsWindow(self.pending_results)
        self._pref_window.selection_confirmed.connect(self._on_pref_confirmed)
        self._pref_window.selection_cancelled.connect(self._on_pref_cancelled)
        self._pref_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._pref_window.show()

    def _on_pref_confirmed(self, selected_indices):
        """선택 창에서 확인 시그널 수신"""
        self._pref_window = None
        if not selected_indices:
            self.set_response("선택 없음", done=True)
            return
        selections = [self.pending_results[i] for i in selected_indices]
        self._apply_preferred_selections(selections)

    def _on_pref_cancelled(self):
        """선택 창 닫힘 (선택 없이)"""
        self._pref_window = None

    def _apply_preferred_selections(self, selections):
        """선택된 결과들 적용 — 다중 선택 지원"""
        if not selections:
            return

        first_text, first_images = selections[0]
        self.ai_response = first_text

        self._clear_response()
        if first_images:
            self.set_image_response(first_text, first_images, [])
        else:
            label = f"{len(selections)}개 선택됨" if len(selections) > 1 else first_text
            self.set_response(label, done=True)

        if self._on_preferred_selected:
            self._on_preferred_selected(self, selections)

    def set_function(self, function_id: str, function_name: str):
        """함수 선택 - 라이브러리에서 함수를 선택했을 때 호출"""
        self.function_id = function_id
        self.function_name = function_name
        self.func_name_label.setText(function_name)
        self.sent = False
        self._clear_response()

    def _select_function(self):
        """함수 선택 다이얼로그 열기"""
        if self.on_open_library:
            self.on_open_library(self)

    def on_signal_input(self, input_data=None):
        """⚡ 실행 신호 포트로부터 신호를 받았을 때 호출

        Args:
            input_data: 입력 포트(_default)로부터 받은 데이터
        """
        # 새 실행을 위해 sent 플래그 리셋
        self.sent = False

        if not self.function_id:
            self.set_response("함수를 선택하세요", done=True)
            return

        # 모든 입력 포트로부터 데이터 수집
        parameters = self._collect_all_input_data()

        # 기본 입력 데이터 (첫 번째 text 파라미터 또는 빈 문자열)
        default_input = ""
        for param_name, param_info in parameters.items():
            if param_info.get("type") == "text":
                default_input = param_info.get("value", "")
                break

        self._execute_function(default_input, parameters)

    def _collect_all_input_data(self):
        """모든 입력 포트로부터 데이터 수집

        Returns:
            dict: {param_name: {"type": "text"|"image", "value": str}}
        """
        parameters = {}

        if not hasattr(self, 'input_ports'):
            return parameters

        for port_name, port_item in self.input_ports.items():
            if not port_item.edges:
                continue

            edge = port_item.edges[0]
            source_proxy = edge.source_port.parent_proxy

            if not source_proxy:
                continue

            if hasattr(source_proxy, 'widget'):
                source_node = source_proxy.widget()
            else:
                source_node = source_proxy

            param_type = "image" if port_item.port_data_type == port_item.TYPE_FILE else "text"

            data = None
            if param_type == "image":
                if hasattr(source_node, 'image_path') and source_node.image_path:
                    data = source_node.image_path
                elif hasattr(source_node, 'ai_response') and source_node.ai_response:
                    data = source_node.ai_response
            else:
                if hasattr(source_node, 'ai_response') and source_node.ai_response:
                    data = source_node.ai_response
                elif hasattr(source_node, 'text_content') and source_node.text_content:
                    data = source_node.text_content
                elif hasattr(source_node, 'body_edit') and hasattr(source_node.body_edit, 'toPlainText'):
                    data = source_node.body_edit.toPlainText()

            if data:
                parameters[port_name] = {
                    "type": param_type,
                    "value": data
                }

        return parameters

    # _collect_input_data()는 BaseNode에서 상속

    def _execute_function(self, input_data=None, parameters=None):
        """함수 실행 - plugin의 execute_function_graph 호출"""
        if self.sent:
            return

        self.sent = True
        self.set_response(f"함수 실행 중: {self.function_name}...", done=False)

        self.parameters = parameters or {}
        self.node_options = {
            "aspect_ratio": self.ratio_combo.currentText(),
            "temperature": self.temp_spin.value(),
            "top_p": self.top_p_spin.value(),
            "max_output_tokens": self.max_tokens_spin.value(),
        }

        if self.on_send:
            self.on_send(self.node_id, self.function_id, input_data, None)

    def _clear_response(self):
        """결과 영역 초기화"""
        while self.result_layout.count():
            item = self.result_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._result_label = None
        self.ai_response = None
        self.tokens_label.hide()

    def set_response(self, response, done=False):
        """실행 결과 표시"""
        self.ai_response = response

        if not hasattr(self, '_result_label') or self._result_label is None:
            self._result_label = QLabel(response)
            self._result_label.setWordWrap(True)
            self._result_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._result_label.setStyleSheet(
                f"""
                background-color: {Theme.BG_INPUT};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 12px;
                padding: 10px 12px; color: {Theme.TEXT_PRIMARY}; font-size: 11px;
                """
            )
            self.result_layout.addWidget(self._result_label)
        else:
            self._result_label.setText(response)

    def set_image_response(self, text, images, thought_signatures=None):
        """이미지 결과 표시"""
        import os
        import uuid
        import tempfile
        from PyQt6.QtGui import QPixmap
        from v.temp_file_manager import TempFileManager

        self.ai_response = text
        temp_manager = TempFileManager()

        for i, img_data in enumerate(images):
            raw_bytes = self._decode_image_data(img_data)
            if not raw_bytes:
                continue

            temp_path = os.path.join(
                tempfile.gettempdir(), f"qonvo_img_{uuid.uuid4().hex[:12]}.png"
            )
            try:
                with open(temp_path, "wb") as f:
                    f.write(raw_bytes)
                temp_manager.register(temp_path)
            except Exception:
                continue

            pixmap = QPixmap(temp_path)
            if pixmap.isNull():
                continue

            display = pixmap.scaled(
                300, 300,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            img_label = QLabel()
            img_label.setPixmap(display)
            img_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self.result_layout.addWidget(img_label)

        if text:
            self.set_response(text, done=True)

    def _decode_image_data(self, img_data):
        """이미지 데이터 디코딩"""
        import base64
        if isinstance(img_data, bytes):
            return img_data
        elif isinstance(img_data, str):
            if img_data.startswith("data:image"):
                header, encoded = img_data.split(",", 1)
                return base64.b64decode(encoded)
            else:
                return base64.b64decode(img_data)
        return None

    def set_tokens(self, tokens_in: int, tokens_out: int):
        """토큰 사용량 업데이트"""
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.tokens_label.setText(f"{tokens_in:,}  {tokens_out:,}")
        self.tokens_label.show()

    def _toggle_pin(self, checked):
        """노드 핀 토글"""
        self.pinned = checked

    def get_data(self) -> dict:
        """노드 상태 저장"""
        return {
            "type": "function_node",
            "id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "function_id": self.function_id,
            "function_name": self.function_name,
            "ai_response": self.ai_response,
            "node_options": {
                "aspect_ratio": self.ratio_combo.currentText(),
                "temperature": self.temp_spin.value(),
                "top_p": self.top_p_spin.value(),
                "max_output_tokens": self.max_tokens_spin.value(),
            },
            "sent": self.sent,
            "pinned": self.pinned,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "notify_on_complete": self.notify_on_complete,
            "preferred_options_enabled": self.preferred_options_enabled,
            "preferred_options_count": self.preferred_options_count,
            "opts_panel_visible": self.btn_opts_toggle.isChecked(),
        }
