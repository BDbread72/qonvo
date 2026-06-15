"""로컬 .qonvo 보드를 서버 보드로 시딩한다.

  python -m server.seed_board <path-to.qonvo> <board_id>

.qonvo 의 board.json(상대경로 attachments/xxx)을 서버 보드의 snapshot 으로,
첨부 이미지들을 boards/<id>/attachments/ 로 옮긴다. 그러면 클라이언트가
서버모드로 그 보드에 접속해 첨부까지 받아 로컬과 동일하게 본다.

board.py 추출 로직은 stdlib(+v.logger)만 쓰므로 헤드리스 서버에서도 동작한다.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from v.board import _extract_qonvo_to_dir, _is_qonvo_binary, _migrate_board_data

from .board_store import safe_board_id
from .config import get_boards_dir


def seed(qonvo_path: str, board_id: str) -> None:
    src = Path(qonvo_path)
    if not src.exists():
        raise SystemExit(f"file not found: {src}")
    if not _is_qonvo_binary(src):
        raise SystemExit("not a QONVO binary file (legacy zip not supported here)")

    bid = safe_board_id(board_id)
    bdir = get_boards_dir() / bid
    adir = bdir / "attachments"
    adir.mkdir(parents=True, exist_ok=True)

    staging = bdir / "_seed_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    print(f"[seed] extracting {src.name} ...")
    board_json = _extract_qonvo_to_dir(src, staging)
    doc = json.loads(board_json)
    doc = _migrate_board_data(doc, doc.get("version", "1.0"))

    # 첨부 이미지 이동 (attachments/ 평면)
    n = 0
    src_att = staging / "attachments"
    if src_att.exists():
        for f in src_att.iterdir():
            if f.is_file():
                shutil.move(str(f), str(adir / f.name))
                n += 1

    # repositories/ archives/ 는 보드 디렉토리에 보존(현재 HTTP로는 attachments 만 제공)
    extras = 0
    for extra in ("repositories", "archives"):
        s = staging / extra
        if s.exists():
            dest = bdir / extra
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(s), str(dest))
            extras += 1

    # 스냅샷 기록 + oplog 초기화
    (bdir / "snapshot.json").write_text(
        json.dumps({"seq": 0, "doc": doc}, ensure_ascii=False), encoding="utf-8"
    )
    oplog = bdir / "oplog.jsonl"
    if oplog.exists():
        oplog.unlink()
    shutil.rmtree(staging, ignore_errors=True)

    snap_size = (bdir / "snapshot.json").stat().st_size
    print(f"[seed] board '{bid}': {n} attachments, snapshot {snap_size//1024} KB"
          + (f", {extras} extra dirs" if extras else ""))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python -m server.seed_board <file.qonvo> <board_id>")
        sys.exit(2)
    seed(sys.argv[1], sys.argv[2])
