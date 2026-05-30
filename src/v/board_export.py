"""
.qonvo ↔ 폴더 양방향 변환.

- export_qonvo_to_folder(qonvo_path, out_dir):
    .qonvo 바이너리를 사람이 읽고 탐색할 수 있는 폴더로 풀어냄.
    정본(canonical) = board.json + attachments/ (round-trip 기준).
    그 위에 conversation.md / note.md / index.md 등 "읽기 전용 뷰"를 함께 생성.

- import_folder_to_qonvo(folder, qonvo_path):
    폴더(board.json + attachments/)를 다시 .qonvo로 재패키징.
    렌더링 뷰(.md)는 무시하고 board.json만 정본으로 사용 → 무손실 round-trip.

설계 노트:
  * board.json 안의 첨부 경로는 항상 아카이브 상대경로("attachments/<uuid>.png").
    export는 아카이브 raw board.json을 그대로 쓰므로 경로가 이미 상대경로다.
  * 차원(dimension)은 board_data를 중첩으로 들고 있고, 첨부는 전부 최상위
    attachments/ 네임스페이스에 평탄하게 저장된다 → 이미지 해석은 항상 추출 루트 기준.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from v.board import (
    _FLAG_COMPRESSED,
    _is_qonvo_binary,
    _parse_toc,
    _write_qonvo,
)
from v.logger import get_logger

logger = get_logger("qonvo.export")

# 아카이브 내 "데이터 파일" 접두사 (board.json 외)
_DATA_PREFIXES = ("attachments/", "repositories/", "archives/")

# 좌표 읽기순 정렬 시 같은 "줄"로 묶는 y 간격(px). 이 범위 안은 x 오름차순.
_ROW_BUCKET = 200

_ROLE_LABEL = {
    "system": "시스템",
    "user": "사용자",
    "assistant": "어시스턴트",
    "model": "모델",
}

# progress 콜백: (done, total, label) -> bool. False 반환 = 취소 요청.
ProgressCb = Optional[Callable[[int, int, str], bool]]


class ExportCancelled(Exception):
    """progress 콜백이 취소를 요청했을 때 발생."""


class _Progress:
    """진행 카운터 — 콜백이 False를 반환하면 ExportCancelled 발생."""

    def __init__(self, cb: ProgressCb, total: int):
        self._cb = cb
        self.total = max(1, int(total))
        self.done = 0

    def step(self, label: str = "") -> None:
        self.done += 1
        if self._cb and self._cb(self.done, self.total, label) is False:
            raise ExportCancelled()

    def tick(self, label: str) -> None:
        """카운트 증가 없이 라벨만 갱신(취소 확인 겸용)."""
        if self._cb and self._cb(self.done, self.total, label) is False:
            raise ExportCancelled()

    def finish(self, label: str = "완료") -> None:
        if self._cb:
            self._cb(self.total, self.total, label)


# ── 파일명 정리 ──

_SAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(s: str, fallback: str = "untitled", maxlen: int = 60) -> str:
    """임의 문자열을 안전한 폴더/파일 이름으로 변환."""
    if not s:
        return fallback
    s = _SAFE_RE.sub("_", str(s))
    s = re.sub(r"\s+", " ", s).strip(" ._-")
    if len(s) > maxlen:
        s = s[:maxlen].strip(" ._-")
    return s or fallback


def _preview(text: str, n: int = 50) -> str:
    """한 줄 미리보기 (개행 제거, n자 제한)."""
    if not text:
        return ""
    line = " ".join(str(text).split())
    return (line[:n] + "…") if len(line) > n else line


# ── 추출 ──

def _read_board_json(toc, f) -> bytes:
    """이미 파싱된 TOC에서 board.json 바이트만 읽어 반환."""
    for name, offset, size, flags in toc:
        if name == "board.json":
            f.seek(offset)
            raw = f.read(size)
            if flags & _FLAG_COMPRESSED:
                raw = zlib.decompress(raw)
            return raw
    raise ValueError("board.json 엔트리가 .qonvo 파일에 없습니다")


def _extract_data_entries(qonvo_path: Path, out_dir: Path, entries, prog: _Progress) -> int:
    """attachments/ repositories/ archives/ 엔트리를 out_dir에 추출. (board.json 제외)"""
    n = 0
    with open(qonvo_path, "rb") as f:
        for name, offset, size, flags in entries:
            f.seek(offset)
            raw = f.read(size)
            if flags & _FLAG_COMPRESSED:
                raw = zlib.decompress(raw)
            out_path = out_dir / name
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(raw)
            n += 1
            prog.step(f"추출: {name}")
    logger.info(f"[EXPORT] Extracted {n} data files to {out_dir}")
    return n


def _count_nodes(board: Dict[str, Any]) -> int:
    """렌더 대상 노드 수(차원 재귀 포함) — 진행률 total 추정용."""
    n = 0
    for cat in _RENDERERS:
        n += len(board.get(cat, []))
    for dim in board.get("dimensions", []):
        bd = dim.get("board_data")
        if bd:
            n += _count_nodes(bd)
        n += 1  # 차원 자체도 한 단위
    return n


# ── 첨부 → 노드 폴더 복사 ──

class _Ctx:
    """렌더링 컨텍스트 — 추출 루트(첨부 원본)와 누적 통계."""

    def __init__(self, root: Path):
        self.root = root            # 첨부가 추출된 최상위 폴더
        self.counts: Dict[str, int] = {}

    def bump(self, key: str, n: int = 1):
        self.counts[key] = self.counts.get(key, 0) + n

    def resolve(self, archive_rel: str) -> Optional[Path]:
        """'attachments/xxx.png' → 실제 추출 파일 경로 (없으면 None)."""
        if not archive_rel:
            return None
        norm = str(archive_rel).replace("\\", "/")
        if not norm.startswith(_DATA_PREFIXES):
            # 절대경로 등 — 존재하면 그대로
            p = Path(archive_rel)
            return p if p.exists() else None
        p = self.root / norm
        return p if p.exists() else None


def _copy_images(ctx: _Ctx, refs: List[str], dest_dir: Path, prefix: str) -> List[str]:
    """이미지 참조들을 dest_dir/images/ 로 복사하고, 마크다운에서 쓸 상대경로 목록 반환."""
    out: List[str] = []
    if not refs:
        return out
    images_dir = dest_dir / "images"
    for i, ref in enumerate(refs, 1):
        src = ctx.resolve(ref)
        if src is None:
            out.append("")  # 누락 표시
            continue
        images_dir.mkdir(parents=True, exist_ok=True)
        ext = src.suffix or ".png"
        fname = f"{prefix}_{i:02d}{ext}"
        try:
            shutil.copy2(src, images_dir / fname)
            out.append(f"images/{fname}")
        except OSError as e:
            logger.warning(f"[EXPORT] image copy failed {src}: {e}")
            out.append("")
    return out


# ── 노드별 렌더러 (각자 node_dir에 .md 작성) ──

def _render_chat(ctx: _Ctx, node: Dict[str, Any], node_dir: Path) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    nid = node.get("id", "?")
    model = node.get("model", "")
    lines: List[str] = [f"# Chat Node #{nid}", ""]
    if model:
        lines.append(f"**Model:** `{model}`")
        lines.append("")

    history = node.get("history") or []
    turn = 0
    for entry in history:
        turn += 1
        user = entry.get("user", "")
        resp = entry.get("response", "")
        emodel = entry.get("model", model)
        ti, to = entry.get("tokens_in"), entry.get("tokens_out")

        lines.append("---")
        lines.append(f"## Turn {turn}")
        lines.append("")
        lines.append("### 👤 User")
        lines.append(user or "_(empty)_")
        lines.append("")
        ufiles = _copy_images(ctx, entry.get("files", []), node_dir, f"t{turn:02d}_userfile")
        for rel in ufiles:
            if rel:
                lines.append(f"![user file]({rel})")
        if any(ufiles):
            lines.append("")
        lines.append("### 🤖 Assistant")
        lines.append(resp or "_(empty)_")
        lines.append("")
        imgs = _copy_images(ctx, entry.get("images", []), node_dir, f"t{turn:02d}_ai")
        for rel in imgs:
            if rel:
                lines.append(f"![ai image]({rel})")
        if any(imgs):
            lines.append("")
        meta = []
        if ti is not None or to is not None:
            meta.append(f"tokens: {ti or 0} in / {to or 0} out")
        if emodel:
            meta.append(f"model: {emodel}")
        if meta:
            lines.append(f"_{' · '.join(meta)}_")
            lines.append("")

    # 아직 history에 안 들어간 현재 입력/응답 턴
    cur_u = node.get("user_message", "")
    cur_r = node.get("ai_response", "")
    if cur_u or cur_r:
        lines.append("---")
        lines.append("## Current (unsent / latest)")
        lines.append("")
        lines.append("### 👤 User")
        lines.append(cur_u or "_(empty)_")
        lines.append("")
        lines.append("### 🤖 Assistant")
        lines.append(cur_r or "_(empty)_")
        lines.append("")
        imgs = _copy_images(ctx, node.get("ai_image_paths", []), node_dir, "cur_ai")
        for rel in imgs:
            if rel:
                lines.append(f"![ai image]({rel})")

    if node.get("archive_path"):
        lines.append("---")
        lines.append(f"> 📦 archived history: `{node['archive_path']}` "
                     f"({node.get('archived_count', '?')} turns) — 원본은 archives/ 에 보존됨")

    (node_dir / "conversation.md").write_text("\n".join(lines), encoding="utf-8")
    ctx.bump("chat")


def _render_sticky(ctx: _Ctx, node: Dict[str, Any], node_dir: Path) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    title = node.get("title", "")
    body = node.get("body", "")
    md = [f"# 🗒️ {title}" if title else "# 🗒️ Sticky Note", "", body or "_(empty)_"]
    (node_dir / "note.md").write_text("\n".join(md), encoding="utf-8")
    ctx.bump("sticky")


def _render_prompt(ctx: _Ctx, node: Dict[str, Any], node_dir: Path) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    title = node.get("title", "")
    role = node.get("role", "system")
    role_kr = _ROLE_LABEL.get(role, role)
    enabled = node.get("enabled", True)
    md = [
        f"# 💬 {title}" if title else "# 💬 Prompt",
        "",
        f"- **Role:** {role_kr} (`{role}`)",
        f"- **Enabled:** {'✅' if enabled else '❌'}",
        f"- **Priority:** {node.get('priority', 0)}",
        "",
        "---",
        "",
        node.get("body", "") or "_(empty)_",
    ]
    (node_dir / "prompt.md").write_text("\n".join(md), encoding="utf-8")
    ctx.bump("prompt")


def _render_markdown(ctx: _Ctx, node: Dict[str, Any], node_dir: Path) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "content.md").write_text(node.get("markdown", "") or "", encoding="utf-8")
    ctx.bump("markdown")


def _render_text(ctx: _Ctx, node: Dict[str, Any], node_dir: Path) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "text.md").write_text(node.get("text", "") or "", encoding="utf-8")
    ctx.bump("text")


def _render_checklist(ctx: _Ctx, node: Dict[str, Any], node_dir: Path) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    title = node.get("title", "")
    md = [f"# ☑️ {title}" if title else "# ☑️ Checklist", ""]
    for item in node.get("items", []):
        if isinstance(item, dict):
            box = "x" if item.get("checked") else " "
            md.append(f"- [{box}] {item.get('text', '')}")
        else:
            md.append(f"- [ ] {item}")
    (node_dir / "checklist.md").write_text("\n".join(md), encoding="utf-8")
    ctx.bump("checklist")


def _render_image_card(ctx: _Ctx, node: Dict[str, Any], node_dir: Path) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    rels = _copy_images(ctx, [node.get("image_path", "")], node_dir, "image")
    md = [f"# 🖼️ Image Card #{node.get('node_id', '?')}", ""]
    if rels and rels[0]:
        md.append(f"![image]({rels[0]})")
    else:
        md.append("_(image not found)_")
    vr = node.get("vision_results")
    if vr:
        md.append("")
        md.append("## Vision")
        md.append("```")
        md.append(json.dumps(vr, ensure_ascii=False, indent=2))
        md.append("```")
    (node_dir / "image.md").write_text("\n".join(md), encoding="utf-8")
    ctx.bump("image_card")


# board.json 카테고리 → (폴더 prefix, 렌더러, id 키, 제목 추출 함수)
def _chat_label(n):
    h = n.get("history") or []
    first = h[0].get("user") if h else n.get("user_message", "")
    return _preview(first or "")


_RENDERERS = {
    "nodes":        ("chat",      _render_chat,      "id",      _chat_label),
    "sticky_notes": ("sticky",    _render_sticky,    "node_id", lambda n: _preview(n.get("title") or n.get("body", ""))),
    "prompt_nodes": ("prompt",    _render_prompt,    "node_id", lambda n: _preview(n.get("title", ""))),
    "markdown_nodes": ("markdown", _render_markdown, "node_id", lambda n: _preview(n.get("markdown", ""))),
    "texts":        ("text",      _render_text,      "id",      lambda n: _preview(n.get("text", ""))),
    "checklists":   ("checklist", _render_checklist, "node_id", lambda n: _preview(n.get("title", ""))),
    "image_cards":  ("image",     _render_image_card, "node_id", lambda n: ""),
}

# index에 요약만 남기는 카테고리 (전용 렌더러 없음)
_SUMMARY_CATS = {
    "function_nodes": "Function",
    "round_tables": "Round Table",
    "repository_nodes": "Repository",
    "buttons": "Button",
    "switch_nodes": "Switch",
    "latch_nodes": "Latch",
    "and_gates": "AND gate",
    "or_gates": "OR gate",
    "not_gates": "NOT gate",
    "xor_gates": "XOR gate",
    "bulb_nodes": "Bulb",
    "nixi_nodes": "Nixi",
    "ups_nodes": "Upscale",
    "rmv_nodes": "BG Remove",
    "group_frames": "Group Frame",
}


def _render_board(board: Dict[str, Any], board_dir: Path, ctx: _Ctx,
                  *, title: str = "", is_root: bool = False,
                  prog: Optional[_Progress] = None) -> List[str]:
    """board_data를 board_dir에 렌더. index.md용 요약 라인 리스트 반환."""
    nodes_dir = board_dir / "nodes"
    index_links: List[str] = []

    # 콘텐츠 노드 전부 수집 (좌표 포함) → 캔버스 읽기순 정렬
    collected = []  # (cat, prefix, renderer, id_key, labeler, node, x, y)
    for cat, (prefix, renderer, id_key, labeler) in _RENDERERS.items():
        for node in board.get(cat, []):
            x = node.get("x", 0) or 0
            y = node.get("y", 0) or 0
            collected.append((cat, prefix, renderer, id_key, labeler, node, x, y))

    # 같은 줄(row bucket)끼리 묶어 위→아래, 왼→오 (PPT/문서 읽기순)
    collected.sort(key=lambda c: (round(c[7] / _ROW_BUCKET), c[6]))

    for seq, (cat, prefix, renderer, id_key, labeler, node, x, y) in enumerate(collected, 1):
        nid = node.get(id_key, "x")
        label = ""
        try:
            label = labeler(node) or ""
        except Exception:
            pass
        # 시퀀스 prefix → 탐색기에서 캔버스 읽기순으로 나열됨
        folder = f"{seq:03d}_{prefix}"
        folder += f"_{_safe_name(label, '', 30)}" if label else f"_{nid}"
        node_dir = nodes_dir / folder
        try:
            renderer(ctx, node, node_dir)
            view = next(iter(node_dir.glob("*.md")), None)
            rel = os.path.relpath(view, board_dir).replace("\\", "/") if view else folder
            desc = f" — {label}" if label else ""
            index_links.append(
                f"- `{seq:03d}` [{prefix} #{nid}]({rel}) @ ({int(x)}, {int(y)}){desc}")
        except Exception as e:
            logger.warning(f"[EXPORT] render failed {cat}#{nid}: {e}")
        if prog:
            prog.step(f"렌더: {prefix} #{nid}")

    # 요약 카테고리
    for cat, human in _SUMMARY_CATS.items():
        items = board.get(cat, [])
        if items:
            index_links.append(f"- {human}: {len(items)}개")

    # 차원 (재귀)
    dims = board.get("dimensions", [])
    if dims:
        dims_dir = board_dir / "dimensions"
        for dim in dims:
            dtitle = dim.get("title") or f"dim_{dim.get('node_id', 'x')}"
            sub_dir = dims_dir / _safe_name(dtitle, f"dim_{dim.get('node_id', 'x')}")
            bd = dim.get("board_data")
            if bd:
                _render_board(bd, sub_dir, ctx, title=dtitle, prog=prog)
                rel = os.path.relpath(sub_dir / "index.md", board_dir).replace("\\", "/")
                dx, dy = int(dim.get("x", 0) or 0), int(dim.get("y", 0) or 0)
                index_links.append(f"- 🔲 [Dimension: {dtitle}]({rel}) @ ({dx}, {dy})")
            ctx.bump("dimension")
            if prog:
                prog.step(f"렌더: dimension {dtitle}")

    # index.md
    head = title or board.get("name", "Board")
    idx: List[str] = [f"# {head}", ""]
    if is_root:
        meta = []
        if board.get("version"):
            meta.append(f"- **Version:** {board['version']}")
        if board.get("saved_at"):
            meta.append(f"- **Saved:** {board['saved_at']}")
        if board.get("system_prompt"):
            meta.append(f"- **System prompt:** {_preview(board['system_prompt'], 120)}")
        if meta:
            idx += meta + [""]
    idx.append("## Contents")
    if index_links:
        idx += index_links
    else:
        idx.append("_(empty board)_")
    idx.append("")
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "index.md").write_text("\n".join(idx), encoding="utf-8")
    return index_links


# ── 공개 API ──

def export_qonvo_to_folder(qonvo_path: str | Path, out_dir: str | Path,
                           progress: ProgressCb = None) -> Dict[str, Any]:
    """`.qonvo` 파일을 out_dir 폴더로 export.

    out_dir 가 이미 있고 비어있지 않으면 ValueError (덮어쓰기 사고 방지).
    progress(done, total, label)가 False를 반환하면 ExportCancelled 발생 + 부분 폴더 정리.

    Returns:
        {"out_dir": str, "counts": {...}, "board_name": str}
    """
    qonvo_path = Path(qonvo_path)
    out_dir = Path(out_dir)

    if not qonvo_path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {qonvo_path}")
    if not _is_qonvo_binary(qonvo_path):
        raise ValueError(f"QONVO 바이너리 파일이 아닙니다: {qonvo_path}")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"대상 폴더가 비어있지 않습니다: {out_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        # TOC + board.json 을 먼저 한 번에 읽어 total 추정
        with open(qonvo_path, "rb") as f:
            toc = _parse_toc(f)
            board_json = _read_board_json(toc, f)
        board = json.loads(board_json)
        data_entries = [
            t for t in toc
            if t[0] != "board.json" and t[0].startswith(_DATA_PREFIXES)
        ]
        prog = _Progress(progress, len(data_entries) + _count_nodes(board))

        # 1) 데이터 엔트리 추출
        _extract_data_entries(qonvo_path, out_dir, data_entries, prog)

        # board.json 은 pretty-print 로 디스크에 (탐색/diff 용이). import는 이걸 다시 읽음.
        (out_dir / "board.json").write_text(
            json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 2) 렌더링 뷰 (정본 폴더 옆에 _readable/ 로 분리)
        ctx = _Ctx(out_dir)
        _render_board(board, out_dir / "_readable", ctx,
                      title=board.get("name", qonvo_path.stem), is_root=True, prog=prog)

        prog.finish()
    except ExportCancelled:
        logger.info(f"[EXPORT] Cancelled — cleaning up {out_dir}")
        shutil.rmtree(out_dir, ignore_errors=True)
        raise

    logger.info(f"[EXPORT] Done: {qonvo_path.name} -> {out_dir} ({ctx.counts})")
    return {
        "out_dir": str(out_dir),
        "counts": ctx.counts,
        "board_name": board.get("name", qonvo_path.stem),
    }


def import_folder_to_qonvo(folder: str | Path, qonvo_path: str | Path,
                           progress: ProgressCb = None) -> str:
    """export로 만든 폴더(board.json + attachments/)를 다시 `.qonvo`로 재패키징.

    렌더링 뷰(_readable/, *.md)는 무시. board.json + 데이터 파일만 정본으로 사용.

    Returns:
        생성된 .qonvo 경로 (str)
    """
    folder = Path(folder)
    qonvo_path = Path(qonvo_path)

    board_file = folder / "board.json"
    if not board_file.exists():
        raise FileNotFoundError(f"board.json 이 폴더에 없습니다: {folder}")

    # board.json 검증 + 정규화 (compact 재직렬화)
    try:
        board = json.loads(board_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"board.json 형식 오류: {e}")

    entries: Dict[str, bytes] = {
        "board.json": json.dumps(board, ensure_ascii=False).encode("utf-8")
    }

    # 먼저 수집 대상 파일 목록 (진행률 total 계산용)
    files_to_pack: List[Path] = []
    for prefix in _DATA_PREFIXES:
        base = folder / prefix.rstrip("/")
        if not base.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                files_to_pack.append(Path(dirpath) / fn)

    prog = _Progress(progress, len(files_to_pack) + 1)  # +1: _write_qonvo 단계
    for full in files_to_pack:
        rel = full.relative_to(folder).as_posix()
        entries[rel] = full.read_bytes()
        prog.step(f"수집: {rel}")
    n = len(files_to_pack)

    prog.tick("아카이브 작성 중…")
    qonvo_path.parent.mkdir(parents=True, exist_ok=True)
    _write_qonvo(qonvo_path, entries)
    prog.finish()
    logger.info(f"[IMPORT] Repacked {n} data files -> {qonvo_path}")
    return str(qonvo_path)
