"""보드 영구저장 + op 로그 기반 delta sync.

저장 구조: boards/<board_id>/
  - snapshot.json   권위 문서(restore_data 호환: {category: [node,...], "edges": [...]}) + seq
  - oplog.jsonl     seq 순서대로 누적된 op (delta sync 용)

권위 모델: 서버가 op 를 적용해 snapshot 을 유지한다. 새 클라이언트는
join 시 last_seq 를 보내고, 서버는 그 이후의 op 만 delta 로, 아니면 full sync 로 보낸다.

UUID 파일명/원자적 저장(.tmp→replace) 규칙을 따른다. (CLAUDE.md Storage Rules)
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_boards_dir

# restore_data 가 사용하는 카테고리 키 (리스트 형태로 저장됨)
_LIST_CATEGORIES = {
    "nodes", "function_nodes", "round_tables", "sticky_notes", "prompt_nodes",
    "markdown_nodes", "buttons", "checklists", "repository_nodes", "nixi_nodes",
    "ups_nodes", "rmv_nodes", "switch_nodes", "latch_nodes", "and_gates",
    "or_gates", "not_gates", "xor_gates", "bulb_nodes", "texts", "group_frames",
    "image_cards", "dimensions", "edges", "functions_library",
}

# 보드 id 안전화 (경로 조작 방지)
_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")

# oplog 가 이 크기를 넘으면 join 시 delta 대신 full sync 를 강제
_MAX_DELTA_OPS = 5000


def safe_board_id(board_id: str) -> str:
    """보드 id 를 파일시스템 안전 문자열로 정규화한다."""
    s = _SAFE.sub("_", (board_id or "").strip())
    return s[:64] or "default"


def safe_attachment_name(name: str) -> Optional[str]:
    """첨부 파일명을 안전하게 정규화한다(basename + 화이트리스트). 부적합 시 None."""
    if not name:
        return None
    base = os.path.basename(name.replace("\\", "/")).strip()
    if not base or base in (".", "..") or _SAFE.sub("", base) != base:
        return None
    return base[:128]


def list_boards() -> List[str]:
    """저장된 보드 id 목록을 반환한다."""
    root = get_boards_dir()
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "snapshot.json").exists())


class Board:
    """단일 보드의 권위 문서 + op 로그를 관리한다."""

    def __init__(self, board_id: str):
        self.board_id = safe_board_id(board_id)
        self._dir = get_boards_dir() / self.board_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._attach_dir = self._dir / "attachments"
        self._attach_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.seq: int = 0
        self.doc: Dict[str, Any] = {}
        self._oplog: List[dict] = []  # [{seq, op, author}]
        self._dirty: bool = False     # 마지막 저장 이후 변경 여부
        self._load()
        # 신규 보드(스냅샷 없음)는 즉시 빈 스냅샷을 기록해 list_boards 에 노출
        if not self._snapshot_path().exists():
            self._write_snapshot()

    # ---- 첨부 파일 (이미지 등) -----------------------------------------
    @property
    def attachments_dir(self) -> Path:
        return self._attach_dir

    def attachment_path(self, name: str) -> Optional[Path]:
        """안전한 첨부 파일 경로를 반환(경로 조작 차단). 부적합하면 None."""
        safe = safe_attachment_name(name)
        if not safe:
            return None
        return self._attach_dir / safe

    def has_attachment(self, name: str) -> bool:
        p = self.attachment_path(name)
        return bool(p and p.exists())

    def save_attachment(self, name: str, data: bytes) -> bool:
        """첨부 바이너리를 원자적으로 저장한다."""
        p = self.attachment_path(name)
        if p is None:
            return False
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        return True

    def list_attachments(self) -> List[str]:
        return sorted(f.name for f in self._attach_dir.iterdir() if f.is_file())

    def node_count(self) -> int:
        """보드의 노드 총수(엣지/라이브러리 제외)를 doc 에서 센다."""
        with self._lock:
            total = 0
            for cat, v in self.doc.items():
                if cat in ("edges", "functions_library") or not isinstance(v, list):
                    continue
                total += len(v)
            return total

    # ---- 영속화 ---------------------------------------------------------
    def _snapshot_path(self) -> Path:
        return self._dir / "snapshot.json"

    def _oplog_path(self) -> Path:
        return self._dir / "oplog.jsonl"

    def _load(self) -> None:
        snap_seq = 0
        sp = self._snapshot_path()
        if sp.exists():
            try:
                payload = json.loads(sp.read_text(encoding="utf-8"))
                self.doc = payload.get("doc", {})
                self.seq = int(payload.get("seq", 0))
                snap_seq = self.seq
            except Exception:
                self.doc, self.seq = {}, 0
        op = self._oplog_path()
        if op.exists():
            try:
                for line in op.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        self._oplog.append(json.loads(line))
            except Exception:
                self._oplog = []
        # 스냅샷 저장 이후의 op 를 doc 에 재생(재시작 일관성) + seq 를 최신으로.
        max_seq = snap_seq
        for rec in self._oplog:
            s = int(rec.get("seq", 0))
            if s > snap_seq:
                try:
                    self._apply_one(rec.get("op", {}))
                except Exception:
                    pass
            max_seq = max(max_seq, s)
        self.seq = max_seq

    def _write_snapshot(self) -> None:
        """스냅샷 파일을 원자적으로 기록한다(dirty 무관)."""
        sp = self._snapshot_path()
        tmp = sp.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"seq": self.seq, "doc": self.doc}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, sp)

    def save_snapshot(self) -> None:
        """현재 권위 문서를 원자적으로 저장하고 oplog 를 최근 것만 남긴다."""
        with self._lock:
            if not self._dirty:
                return
            self._write_snapshot()
            # oplog 압축: delta sync 용 최근 op 만 유지(무한 성장 방지)
            if len(self._oplog) > _MAX_DELTA_OPS:
                self._oplog = self._oplog[-_MAX_DELTA_OPS:]
                opp = self._oplog_path()
                otmp = opp.with_suffix(".jsonl.tmp")
                otmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                        for r in self._oplog), encoding="utf-8")
                os.replace(otmp, opp)
            self._dirty = False

    def _append_oplog(self, record: dict) -> None:
        with open(self._oplog_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ---- 동기화 ---------------------------------------------------------
    def snapshot_for_join(self, last_seq: int) -> dict:
        """join 시 보낼 메시지를 구성한다.

        last_seq 이후 op 만 보낼 수 있으면 delta, 아니면 full sync.
        """
        with self._lock:
            if 0 < last_seq <= self.seq and len(self._oplog) <= _MAX_DELTA_OPS:
                # 이미 최신이면 빈 delta
                if last_seq == self.seq:
                    return {"type": "delta", "seq": self.seq, "ops": []}
                # 요청한 seq 이후 op 가 로그에 연속으로 남아있는 경우만 delta
                newer = [r for r in self._oplog if r["seq"] > last_seq]
                if newer and newer[0]["seq"] == last_seq + 1:
                    return {
                        "type": "delta",
                        "seq": self.seq,
                        "ops": [r["op"] for r in newer],
                    }
            return {"type": "sync", "seq": self.seq, "snapshot": json.loads(json.dumps(self.doc))}

    def apply_ops(self, ops: List[dict], author: str) -> int:
        """op 들을 권위 문서에 적용하고 새 seq 를 반환한다."""
        with self._lock:
            for op in ops:
                try:
                    self._apply_one(op)
                except Exception:
                    pass  # 개별 op 실패는 무시(브로드캐스트는 계속)
                self.seq += 1
                record = {"seq": self.seq, "op": op, "author": author}
                self._oplog.append(record)
                self._append_oplog(record)
            self._dirty = True
            return self.seq

    # ---- op 적용 (권위 문서 갱신) ---------------------------------------
    def _list_of(self, category: str) -> List[dict]:
        if category not in self.doc or not isinstance(self.doc.get(category), list):
            self.doc[category] = []
        return self.doc[category]

    def _find_node(self, target) -> Optional[dict]:
        tid = _as_id(target)
        for cat in _LIST_CATEGORIES:
            if cat in ("edges", "functions_library"):
                continue
            for n in self.doc.get(cat, []) or []:
                if _node_id(n) == tid:
                    return n
        return None

    def _apply_one(self, op: dict) -> None:
        t = op.get("op_type", "")
        target = op.get("target", "")
        data = op.get("data", {}) or {}

        if t == "node_add":
            cat = data.get("_category", "nodes")
            tid = _as_id(target)
            lst = self._list_of(cat)
            if not any(_node_id(n) == tid for n in lst):
                node = {"id": tid, "x": data.get("x", 0), "y": data.get("y", 0)}
                lst.append(node)
        elif t == "node_remove":
            tid = _as_id(target)
            for cat in _LIST_CATEGORIES:
                if cat in ("edges", "functions_library"):
                    continue
                lst = self.doc.get(cat)
                if isinstance(lst, list):
                    self.doc[cat] = [n for n in lst if _node_id(n) != tid]
        elif t == "node_move":
            n = self._find_node(target)
            if n is not None:
                n["x"] = data.get("x", n.get("x", 0))
                n["y"] = data.get("y", n.get("y", 0))
        elif t == "node_prop":
            n = self._find_node(target)
            if n is not None:
                full = data.get("data")
                if isinstance(full, dict):
                    # 전체 노드 데이터 머지(텍스트/제목/색상/크기 등) — 늦은 합류·영속용
                    for k, v in full.items():
                        n[k] = v
                else:
                    key = data.get("key")
                    if key:
                        n[key] = data.get("value")
        elif t == "edge_add":
            edges = self._list_of("edges")
            if not _edge_exists(edges, data):
                edges.append(_normalize_edge(data))
        elif t == "edge_remove":
            edges = self._list_of("edges")
            self.doc["edges"] = [e for e in edges if not _edge_match(e, data)]
        elif t == "chat_append":
            n = self._find_node(target)
            msg = data.get("message")
            if n is not None and msg:
                n.setdefault("history", []).append(msg)

    def append_assistant_message(self, node_id, text: str, images: list) -> None:
        """AI 응답을 권위 문서의 채팅 노드 history 에 누적한다."""
        with self._lock:
            n = self._find_node(node_id)
            if n is not None:
                entry = {"role": "assistant", "content": text}
                if images:
                    entry["images"] = list(images)
                n.setdefault("history", []).append(entry)
                self._dirty = True


# ---- 헬퍼 ---------------------------------------------------------------
def _as_id(target):
    if isinstance(target, str) and target.isdigit():
        return int(target)
    return target


def _node_id(n: dict):
    """노드 dict 의 id 를 반환 ('id' 또는 'node_id')."""
    return n.get("id", n.get("node_id"))


def _normalize_edge(data: dict) -> dict:
    s, t = data.get("source_node_id"), data.get("target_node_id")
    sp = data.get("source_port_name", "_default")
    tp = data.get("target_port_name", "_default")
    return {
        "source_node_id": s, "target_node_id": t,
        "source_port_name": sp, "target_port_name": tp,
        "start_node_id": s, "end_node_id": t,
        "start_key": sp, "end_key": tp,
    }


def _edge_match(e: dict, data: dict) -> bool:
    return (
        e.get("source_node_id") == data.get("source_node_id")
        and e.get("target_node_id") == data.get("target_node_id")
        and e.get("source_port_name", "_default") == data.get("source_port_name", "_default")
        and e.get("target_port_name", "_default") == data.get("target_port_name", "_default")
    )


def _edge_exists(edges: List[dict], data: dict) -> bool:
    return any(_edge_match(e, data) for e in edges)


class BoardManager:
    """접속된 보드들을 메모리에 캐시하고 lazy-load 한다."""

    def __init__(self):
        self._boards: Dict[str, Board] = {}
        self._lock = threading.Lock()

    def get(self, board_id: str) -> Board:
        bid = safe_board_id(board_id)
        with self._lock:
            if bid not in self._boards:
                self._boards[bid] = Board(bid)
            return self._boards[bid]

    def save_all(self) -> None:
        with self._lock:
            for b in self._boards.values():
                b.save_snapshot()

    def delete(self, board_id: str) -> bool:
        """보드를 캐시에서 내리고 디스크 디렉토리(snapshot/oplog/attachments)를 삭제한다."""
        import shutil
        bid = safe_board_id(board_id)
        with self._lock:
            self._boards.pop(bid, None)
        d = get_boards_dir() / bid
        if not d.exists():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return not d.exists()

    def rename(self, old_id: str, new_id: str) -> bool:
        """보드 디렉토리명을 바꾼다(대상이 이미 있으면 실패). 캐시는 비워 새로 로드되게 한다."""
        o = safe_board_id(old_id)
        n = safe_board_id(new_id)
        if not n or o == n:
            return False
        src = get_boards_dir() / o
        dst = get_boards_dir() / n
        if not src.exists() or dst.exists():
            return False
        with self._lock:
            self._boards.pop(o, None)
            self._boards.pop(n, None)
        try:
            src.rename(dst)
        except Exception:
            return False
        return True
