"""
뷰포트 기반 레이지 로딩 매니저

보드 로드 시 모든 아이템을 한꺼번에 생성하지 않고,
뷰포트 내 아이템만 먼저 로드한 뒤, 팬/줌 시 온디맨드 생성.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QRectF, QTimer

from v.constants import LAZY_LOAD_DEBOUNCE_MS, LAZY_LOAD_VIEWPORT_MARGIN
from v.logger import get_logger

logger = get_logger("qonvo.lazy_loader")

# 카테고리별 ID 키 매핑
ID_KEY_MAP = {
    "nodes": "id",
    "function_nodes": "id",
    "sticky_notes": "node_id",
    "buttons": "node_id",
    "round_tables": "id",

    "checklists": "node_id",
    "repository_nodes": "id",
    "texts": "id",
    "group_frames": "id",
    "image_cards": "node_id",
    "dimensions": "node_id",
    "prompt_nodes": "node_id",
}

# 기본 아이템 크기 (w, h가 없는 경우 사용)
_DEFAULT_SIZE = {
    "nodes": (280, 200),
    "function_nodes": (280, 200),
    "sticky_notes": (200, 150),
    "buttons": (80, 80),
    "round_tables": (280, 200),

    "checklists": (200, 200),
    "repository_nodes": (280, 200),
    "texts": (200, 50),
    "group_frames": (300, 200),
    "image_cards": (300, 300),
    "dimensions": (200, 200),
    "prompt_nodes": (240, 180),
}


class LazyLoadManager:
    """뷰포트 기반 레이지 로딩 관리자.

    JSON 데이터를 파싱하여 pending 상태로 보관하고,
    뷰포트 내 아이템만 선택적으로 생성(materialize)한다.
    """

    def __init__(self):
        self._pending: Dict[str, Dict[int, dict]] = {}
        self._pending_edges: List[dict] = []
        self._materialized_ids: Set[int] = set()
        self._spatial_entries: List[Tuple[float, float, float, float, str, int]] = []
        self._active = False
        self._timer: Optional[QTimer] = None
        self._callback = None

    def reset(self):
        """새 보드 로드 시 전체 상태 초기화."""
        self._pending.clear()
        self._pending_edges.clear()
        self._materialized_ids.clear()
        self._spatial_entries.clear()
        self._active = False

    def setup_timer(self, parent, callback):
        """디바운스 QTimer 설정."""
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.setInterval(LAZY_LOAD_DEBOUNCE_MS)
        self._callback = callback
        self._timer.timeout.connect(callback)

    def schedule_check(self):
        """뷰포트 변경 시 타이머 재시작 (디바운스)."""
        if not self._active or self._timer is None:
            return
        self._timer.start()

    def has_pending(self) -> bool:
        """미생성 아이템이 남아있는지 확인."""
        return any(items for items in self._pending.values()) or bool(self._pending_edges)

    def ingest_data(self, data: dict):
        """JSON 파싱 → pending + spatial index 구축."""
        for category, id_key in ID_KEY_MAP.items():
            rows = data.get(category, [])
            if not rows:
                continue
            cat_dict: Dict[int, dict] = {}
            default_w, default_h = _DEFAULT_SIZE.get(category, (280, 200))

            for row in rows:
                node_id = row.get(id_key)
                if node_id is None:
                    continue
                cat_dict[node_id] = row

                # spatial index에 추가
                x = row.get("x", 0)
                y = row.get("y", 0)
                w = row.get("width", default_w) or default_w
                h = row.get("height", default_h) or default_h
                self._spatial_entries.append((x, y, w, h, category, node_id))

            if cat_dict:
                self._pending[category] = cat_dict

        # 엣지 데이터 보관
        self._pending_edges = list(data.get("edges", []))

        total = sum(len(d) for d in self._pending.values())
        logger.info(f"[LAZY] Ingested {total} items, {len(self._pending_edges)} edges")
        self._active = total > 0

    def query_visible(self, viewport_rect: QRectF) -> List[Tuple[str, int, dict]]:
        """확장된 뷰포트와 교차하는 pending 아이템 반환.

        Returns:
            list of (category, node_id, row_data)
        """
        margin_w = viewport_rect.width() * LAZY_LOAD_VIEWPORT_MARGIN
        margin_h = viewport_rect.height() * LAZY_LOAD_VIEWPORT_MARGIN
        expanded = QRectF(
            viewport_rect.x() - margin_w,
            viewport_rect.y() - margin_h,
            viewport_rect.width() + 2 * margin_w,
            viewport_rect.height() + 2 * margin_h,
        )

        result = []
        remaining = []

        for entry in self._spatial_entries:
            x, y, w, h, category, node_id = entry
            if node_id in self._materialized_ids:
                continue
            item_rect = QRectF(x, y, w, h)
            if expanded.intersects(item_rect):
                cat_dict = self._pending.get(category, {})
                row = cat_dict.get(node_id)
                if row is not None:
                    result.append((category, node_id, row))
            else:
                remaining.append(entry)

        # 매칭된 엔트리는 spatial_entries에서 제거하지 않음
        # (mark_materialized에서 처리)
        return result

    def mark_materialized(self, node_id: int, category: str):
        """pending에서 제거, materialized에 추가."""
        self._materialized_ids.add(node_id)
        cat_dict = self._pending.get(category, {})
        cat_dict.pop(node_id, None)
        if not cat_dict and category in self._pending:
            del self._pending[category]

    def get_resolvable_edges(self) -> List[dict]:
        """양쪽 노드 모두 생성된 엣지 반환, pending에서 제거."""
        resolvable = []
        still_pending = []

        for edge in self._pending_edges:
            s_id = edge.get("source_node_id", edge.get("start_node_id"))
            t_id = edge.get("target_node_id", edge.get("end_node_id"))
            if s_id in self._materialized_ids and t_id in self._materialized_ids:
                resolvable.append(edge)
            else:
                still_pending.append(edge)

        self._pending_edges = still_pending
        return resolvable

    def get_pending_item_by_id(self, node_id: int) -> Optional[Tuple[str, dict]]:
        """특정 node_id의 pending 데이터 반환."""
        for category, items in self._pending.items():
            if node_id in items:
                return category, items[node_id]
        return None

    def get_all_pending_data(self) -> Dict[str, List[dict]]:
        """저장용: 미생성 아이템 JSON 반환."""
        result = {}
        for category, items in self._pending.items():
            if items:
                result[category] = list(items.values())
        return result

    def get_pending_edges(self) -> List[dict]:
        """저장용: 미생성 엣지 JSON 반환."""
        return list(self._pending_edges)
