from __future__ import annotations

from typing import Any, Dict, List, Optional


class BaseNode:
    """Shared node behavior for whiteboard widgets.

    Mixin class for all node widgets on the whiteboard.
    Provides common port management, serialization, and utility methods.

    Usage:
        class MyNodeWidget(QWidget, BaseNode):
            def __init__(self, node_id, on_modified=None):
                super().__init__()
                self.init_base_node(node_id, on_modified)
                # ... rest of initialization
    """

    def __init__(self, *args, **kwargs):
        """Empty __init__ for proper MRO chain in multiple inheritance."""
        super().__init__(*args, **kwargs)

    def init_base_node(self, node_id: Optional[int] = None, on_modified=None) -> None:
        """Initialize BaseNode properties.

        Call this in your widget's __init__ after super().__init__().
        """
        self.node_id = node_id
        self.proxy = None
        self.on_modified = on_modified
        self.sent = False

        # Common single-port slots
        self.input_port = None
        self.output_port = None
        self.signal_input_port = None
        self.signal_output_port = None

        # Common multi-port slots (don't override if already set)
        if not hasattr(self, 'input_ports'):
            self.input_ports: Dict[str, Any] = {}
        if not hasattr(self, 'output_ports'):
            self.output_ports: Dict[str, Any] = {}

    def set_node_id(self, node_id: int) -> None:
        self.node_id = node_id

    def set_proxy(self, proxy) -> None:
        self.proxy = proxy

    def notify_modified(self) -> None:
        if self.on_modified:
            self.on_modified()

    def iter_ports(self) -> List[Any]:
        """Return all known ports (single + multi) without duplicates."""
        ports: List[Any] = []
        seen: set[int] = set()

        def add_port(port: Any) -> None:
            if not port:
                return
            port_id = id(port)
            if port_id in seen:
                return
            seen.add(port_id)
            ports.append(port)

        add_port(getattr(self, "input_port", None))
        add_port(getattr(self, "output_port", None))
        add_port(getattr(self, "signal_input_port", None))
        add_port(getattr(self, "signal_output_port", None))

        in_ports = getattr(self, "input_ports", None)
        if isinstance(in_ports, dict):
            for port in in_ports.values():
                add_port(port)

        out_ports = getattr(self, "output_ports", None)
        if isinstance(out_ports, dict):
            for port in out_ports.values():
                add_port(port)

        meta_ports = getattr(self, "meta_output_ports", None)
        if isinstance(meta_ports, dict):
            for port in meta_ports.values():
                add_port(port)

        return ports

    def reposition_ports(self) -> None:
        for port in self.iter_ports():
            try:
                port.reposition()
            except Exception:
                continue

    def _collect_input_data(self):
        """입력 포트에 연결된 소스 노드에서 텍스트 데이터 수집.

        단일 포트(input_port)와 dict 포트(input_ports) 모두 지원.
        """
        port = getattr(self, 'input_port', None)
        if port is None:
            ports = getattr(self, 'input_ports', {})
            if ports:
                port = next(iter(ports.values()), None)
        if port is None or not port.edges:
            return None

        source_proxy = port.edges[0].source_port.parent_proxy
        if not source_proxy:
            return None
        source_node = source_proxy.widget() if hasattr(source_proxy, 'widget') else source_proxy

        source_port = port.edges[0].source_port
        if hasattr(source_port, 'port_value') and source_port.port_value is not None:
            return str(source_port.port_value)
        if hasattr(source_node, 'ai_response') and source_node.ai_response:
            return source_node.ai_response
        if hasattr(source_node, 'text_content') and source_node.text_content:
            return source_node.text_content
        if hasattr(source_node, 'body_edit') and hasattr(source_node.body_edit, 'toPlainText'):
            return source_node.body_edit.toPlainText()
        return None

    def on_signal_input(self, input_data=None):
        """⚡ 실행 신호 수신 시 호출. 서브클래스에서 오버라이드 가능."""
        self.sent = False
        data = input_data or self._collect_input_data() or ""
        self._send(data, [])

    def resizeEvent(self, event):
        """공통 리사이즈 이벤트 - 핸들 재배치 + 포트 재배치.

        Mixin이므로 super().resizeEvent() 호출하지 않음.
        MRO에서 BaseNode 다음은 object이고, QWidget.resizeEvent()는
        기본적으로 아무 동작을 하지 않으므로 안전함.
        """
        if hasattr(self, 'resize_handle'):
            self.resize_handle.move(self.width() - 16, self.height() - 16)
            self.resize_handle.raise_()
        self.reposition_ports()

    def serialize_common(self) -> Dict[str, Any]:
        """Serialize shared node geometry/state fields."""
        x = 0
        y = 0
        if self.proxy is not None and hasattr(self.proxy, "pos"):
            pos = self.proxy.pos()
            x = pos.x()
            y = pos.y()

        width = self.width() if hasattr(self, "width") else 0
        height = self.height() if hasattr(self, "height") else 0

        return {
            "id": self.node_id,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    def restore_common(self, data: Dict[str, Any]) -> None:
        """Restore shared geometry-related fields from serialized data."""
        if "id" in data:
            self.node_id = data["id"]

        if hasattr(self, "resize"):
            width = data.get("width")
            height = data.get("height")
            if width is not None and height is not None:
                self.resize(width, height)
