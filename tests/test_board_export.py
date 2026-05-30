"""
.qonvo ↔ 폴더 round-trip 테스트 (standalone, pytest 아님).

  python tests/test_board_export.py

검증:
  1. export → 폴더 (board.json + attachments/ + _readable 뷰 생성)
  2. import → 새 .qonvo 재패키징
  3. 원본 .qonvo와 재패키징 .qonvo의 board.json(파싱) + 첨부 바이트 동일 (무손실 round-trip)
"""
import json
import os
import re
import sys
import tempfile
import zlib
from pathlib import Path

if os.name == 'nt':
    os.system('chcp 65001 > nul')

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from v.board import _write_qonvo, _parse_toc, _FLAG_COMPRESSED  # noqa: E402
from v import board_export  # noqa: E402

_fail = 0


def check(cond, msg):
    global _fail
    print(f"  [{'OK' if cond else 'FAIL'}] {msg}")
    if not cond:
        _fail += 1


def read_all_entries(qonvo_path):
    """{name: raw_bytes} (압축 해제된 상태)로 전체 엔트리 반환."""
    out = {}
    with open(qonvo_path, 'rb') as f:
        toc = _parse_toc(f)
        for name, offset, size, flags in toc:
            f.seek(offset)
            raw = f.read(size)
            if flags & _FLAG_COMPRESSED:
                raw = zlib.decompress(raw)
            out[name] = raw
    return out


# 1x1 PNG (실제 바이너리)
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da6364f80f000100010001b8b7b8e90000000049454e44ae426082"
)


def build_sample_qonvo(path: Path):
    """채팅(이미지 포함) + 스티키 + 차원(중첩) 보드를 가진 .qonvo 생성."""
    img_a = "attachments/aaaa1111.png"
    img_b = "attachments/bbbb2222.png"

    board = {
        "type": "WhiteBoard",
        "name": "sample",
        "version": "beta-1.2.1",
        "saved_at": "2026-05-30T00:00:00",
        "system_prompt": "너는 테스트 도우미야.",
        "next_id": 99,
        "nodes": [{
            "id": 1, "x": 0, "y": 0, "model": "gemini-2.0-flash",
            "user_message": "", "ai_response": "",
            "history": [
                {"user": "안녕?", "files": [], "response": "안녕하세요!",
                 "images": [img_a], "tokens_in": 3, "tokens_out": 5,
                 "model": "gemini-2.0-flash"},
                {"user": "이미지 보여줘", "files": [], "response": "여기요",
                 "images": [img_b], "tokens_in": 4, "tokens_out": 2,
                 "model": "gemini-2.0-flash"},
            ],
        }],
        "sticky_notes": [
            # 같은 줄(y≈0)에서 chat(x=0) 오른쪽(x=300) → 읽기순 2번째
            {"node_id": 2, "x": 300, "y": 0, "title": "메모", "body": "할 일 정리", "color": "yellow"},
        ],
        "checklists": [
            # 다음 줄(y=400) → 읽기순 3번째
            {"node_id": 3, "x": 0, "y": 400, "title": "체크", "items": [
                {"text": "첫째", "checked": True}, {"text": "둘째", "checked": False}]},
        ],
        "dimensions": [{
            "node_id": 4, "title": "서브 차원", "board_data": {
                "type": "WhiteBoard",
                "nodes": [{"id": 1, "model": "gemini-2.0-flash", "history": [
                    {"user": "중첩 질문", "response": "중첩 답변", "images": [], "files": []}]}],
                "sticky_notes": [],
                "next_id": 5,
            },
        }],
    }

    entries = {
        "board.json": json.dumps(board, ensure_ascii=False).encode("utf-8"),
        img_a: PNG_1x1,
        img_b: PNG_1x1 + b"\x00trailer",  # 두 첨부를 다르게
    }
    _write_qonvo(path, entries)


def main():
    print("=" * 70)
    print(".qonvo ↔ folder round-trip test")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src_qonvo = tmp / "sample.qonvo"
        out_dir = tmp / "sample_export"
        repacked = tmp / "repacked.qonvo"

        print("\n[1] 샘플 .qonvo 생성")
        build_sample_qonvo(src_qonvo)
        check(src_qonvo.exists(), "sample.qonvo 작성됨")

        print("\n[2] export → 폴더")
        result = board_export.export_qonvo_to_folder(src_qonvo, out_dir)
        check((out_dir / "board.json").exists(), "board.json 추출됨")
        check((out_dir / "attachments").is_dir(), "attachments/ 추출됨")
        check((out_dir / "_readable" / "index.md").exists(), "_readable/index.md 생성됨")
        # 채팅 대화 렌더
        convs = list(out_dir.glob("_readable/nodes/*chat*/conversation.md"))
        check(len(convs) == 1, "conversation.md 1개 생성됨")
        if convs:
            text = convs[0].read_text(encoding="utf-8")
            check("안녕하세요!" in text, "대화 응답이 md에 렌더됨")
            check("Turn 2" in text, "두 번째 턴 렌더됨")
        # 복사된 이미지
        imgs = list(out_dir.glob("_readable/nodes/*chat*/images/*.png"))
        check(len(imgs) == 2, f"채팅 이미지 2개 복사됨 (실제 {len(imgs)})")
        # 차원 재귀
        check((out_dir / "_readable" / "dimensions").is_dir(), "dimensions/ 재귀 생성됨")
        print(f"      counts={result['counts']}")

        # B: 좌표 기반 정렬/명명 — 폴더가 001_/002_/003_ 시퀀스, 읽기순(chat→sticky→checklist)
        node_dirs = sorted(d.name for d in (out_dir / "_readable" / "nodes").iterdir() if d.is_dir())
        print(f"      node_dirs={node_dirs}")
        check(all(re.match(r"\d{3}_", n) for n in node_dirs), "모든 노드 폴더에 001_ 시퀀스 prefix")
        order = [n.split("_", 2)[1] for n in node_dirs]  # 시퀀스 뒤 타입
        check(order == ["chat", "sticky", "checklist"],
              f"좌표 읽기순 정렬됨 (실제 {order})")
        idx_text = (out_dir / "_readable" / "index.md").read_text(encoding="utf-8")
        check("@ (0, 0)" in idx_text and "@ (300, 0)" in idx_text, "index.md에 좌표 표기됨")

        print("\n[2b] progress 콜백 호출 검증")
        # 새 폴더에 progress 추적하며 다시 export
        out_dir2 = tmp / "sample_export2"
        events = []
        board_export.export_qonvo_to_folder(
            src_qonvo, out_dir2,
            progress=lambda d, t, l: events.append((d, t, l)) or True)
        check(len(events) > 0, f"progress 콜백 호출됨 ({len(events)}회)")
        check(events[-1][0] == events[-1][1], "마지막 done == total (finish)")
        check(all(d <= t for d, t, _ in events), "done이 total을 넘지 않음")

        print("\n[2c] 취소(cancel) 검증")
        out_dir3 = tmp / "sample_export3"

        def cancel_after_2(d, t, l):
            return d < 2  # 2번째 step에서 False 반환 → 취소

        try:
            board_export.export_qonvo_to_folder(
                src_qonvo, out_dir3, progress=cancel_after_2)
            check(False, "취소 시 ExportCancelled 발생해야 함")
        except board_export.ExportCancelled:
            check(True, "ExportCancelled 발생함")
        check(not out_dir3.exists(), "취소 시 부분 폴더 정리됨")

        print("\n[3] import → 재패키징 .qonvo")
        board_export.import_folder_to_qonvo(out_dir, repacked)
        check(repacked.exists(), "repacked.qonvo 작성됨")

        print("\n[4] 무손실 round-trip 검증")
        orig = read_all_entries(src_qonvo)
        new = read_all_entries(repacked)

        ob = json.loads(orig["board.json"])
        nb = json.loads(new["board.json"])
        check(ob == nb, "board.json 의미 동일 (파싱 후 비교)")

        orig_attach = {k: v for k, v in orig.items() if k != "board.json"}
        new_attach = {k: v for k, v in new.items() if k != "board.json"}
        check(orig_attach.keys() == new_attach.keys(),
              f"첨부 엔트리 집합 동일 ({len(orig_attach)}개)")
        check(orig_attach == new_attach, "첨부 바이트 전부 동일")

    print("\n" + "=" * 70)
    if _fail == 0:
        print("ALL PASS")
    else:
        print(f"{_fail} CHECK(S) FAILED")
    print("=" * 70)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
