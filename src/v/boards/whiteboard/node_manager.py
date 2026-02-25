from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from PyQt6.QtWidgets import QGraphicsProxyWidget


@dataclass
class NodeRecord:
    node_id: int
    kind: str
    item: Any
    proxy: Optional[Any]

    @property
    def widget(self):
        if isinstance(self.item, QGraphicsProxyWidget):
            return self.item.widget()
        if self.proxy is not None and isinstance(self.proxy, QGraphicsProxyWidget):
            return self.proxy.widget()
        return self.item


class NodeManager:
    """Centralized node index and shared port operations.

    It keeps compatibility with the current plugin structure by indexing existing
    per-type dictionaries into one logical registry.
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.nodes: Dict[int, NodeRecord] = {}

    def refresh_index(self) -> Dict[int, NodeRecord]:
        nodes: Dict[int, NodeRecord] = {}

        def add(node_id: int, kind: str, item: Any, proxy: Optional[Any] = None) -> None:
            if node_id is None:
                return
            nodes[node_id] = NodeRecord(node_id=node_id, kind=kind, item=item, proxy=proxy)

        for node_id, proxy in self.plugin.proxies.items():
            add(node_id, "chat", proxy, proxy)
        for node_id, proxy in self.plugin.function_proxies.items():
            add(node_id, "function", proxy, proxy)
        for node_id, proxy in self.plugin.round_table_proxies.items():
            add(node_id, "round_table", proxy, proxy)
        for node_id, proxy in self.plugin.sticky_proxies.items():
            add(node_id, "sticky", proxy, proxy)
        for node_id, proxy in self.plugin.button_proxies.items():
            add(node_id, "button", proxy, proxy)
        for node_id, proxy in self.plugin.checklist_proxies.items():
            add(node_id, "checklist", proxy, proxy)
        for node_id, item in self.plugin.text_items.items():
            add(node_id, "text", item, None)
        for node_id, item in self.plugin.group_frame_items.items():
            add(node_id, "group_frame", item, None)
        for node_id, item in self.plugin.image_card_items.items():
            add(node_id, "image_card", item, None)
        for node_id, item in self.plugin.dimension_items.items():
            add(node_id, "dimension", item, None)

        self.nodes = nodes
        return self.nodes

    def get_proxy(self, node_id: int):
        record = self.refresh_index().get(node_id)
        if not record:
            return None
        if isinstance(record.proxy, QGraphicsProxyWidget):
            return record.proxy
        if isinstance(record.item, QGraphicsProxyWidget):
            return record.item
        return None

    def all_proxies(self) -> Dict[int, QGraphicsProxyWidget]:
        proxies: Dict[int, QGraphicsProxyWidget] = {}
        for node_id, record in self.refresh_index().items():
            proxy = None
            if isinstance(record.proxy, QGraphicsProxyWidget):
                proxy = record.proxy
            elif isinstance(record.item, QGraphicsProxyWidget):
                proxy = record.item
            if proxy is not None:
                proxies[node_id] = proxy
        return proxies

    def all_connectable(self) -> Dict[int, Any]:
        connectables: Dict[int, Any] = {}
        for node_id, record in self.refresh_index().items():
            connectables[node_id] = record.item
        return connectables

    def _iter_ports(self, owner) -> Iterable[Any]:
        seen: set[int] = set()

        def maybe_yield(port):
            if not port:
                return
            port_id = id(port)
            if port_id in seen:
                return
            seen.add(port_id)
            yield port

        # If a node already implements a unified iterator, trust it.
        iter_ports = getattr(owner, "iter_ports", None)
        if callable(iter_ports):
            for port in iter_ports():
                for out in maybe_yield(port):
                    yield out
            return

        for attr in ("input_port", "output_port", "signal_input_port", "signal_output_port"):
            for out in maybe_yield(getattr(owner, attr, None)):
                yield out

        in_ports = getattr(owner, "input_ports", None)
        if isinstance(in_ports, dict):
            for port in in_ports.values():
                for out in maybe_yield(port):
                    yield out

        out_ports = getattr(owner, "output_ports", None)
        if isinstance(out_ports, dict):
            for port in out_ports.values():
                for out in maybe_yield(port):
                    yield out

    def update_all_ports(self) -> None:
        visited: set[int] = set()

        for record in self.refresh_index().values():
            owner = record.widget
            if owner is None:
                continue

            owner_id = id(owner)
            if owner_id in visited:
                continue
            visited.add(owner_id)

            for port in self._iter_ports(owner):
                try:
                    port.reposition()
                except Exception:
                    continue
