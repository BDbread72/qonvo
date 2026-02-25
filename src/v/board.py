"""
보드 저장/불러오기 관리
.qonvo 파일은 QONVO 바이너리 포맷 (Header + TOC + Data body)
레거시 ZIP 아카이브 로드도 하위 호환 지원
"""
import json
import shutil
import struct
import sys
import threading
import tomllib
import zlib
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
import uuid
import os
from v.logger import get_logger

logger = get_logger("qonvo.board")

# ── QONVO 바이너리 포맷 상수 ──
QONVO_MAGIC = b"QONVO"
QONVO_FORMAT_VERSION = 1
HEADER_SIZE = 24
_FLAG_COMPRESSED = 0x01

# 이미 압축된 포맷 → zlib 적용 안 함
_NO_COMPRESS_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp',
    '.mp4', '.mp3', '.zip', '.gz', '.rar', '.7z',
}


# ── QONVO 바이너리 I/O ──

def _should_compress(name: str) -> bool:
    """엔트리 이름(파일 경로)을 보고 zlib 압축 여부 결정."""
    ext = Path(name).suffix.lower()
    return ext not in _NO_COMPRESS_EXTS


def _write_qonvo(filepath: Path, entries: Dict[str, bytes]) -> None:
    """QONVO 바이너리 포맷으로 파일 기록.

    구조: Header(24B) → TOC(가변) → Data body(순차)

    Args:
        filepath: 출력 파일 경로
        entries: {"board.json": b"...", "attachments/xxx.png": b"..."} 형태
    """
    # 1) 각 엔트리의 압축 여부 결정 & 실제 저장 바이트 준비
    prepared: list[tuple[str, bytes, int]] = []  # (name, stored_data, flags)
    for name, raw in entries.items():
        if _should_compress(name):
            compressed = zlib.compress(raw, level=6)
            # 압축률이 10% 이상일 때만 사용
            if len(compressed) < len(raw) * 0.9:
                prepared.append((name, compressed, _FLAG_COMPRESSED))
            else:
                prepared.append((name, raw, 0))
        else:
            prepared.append((name, raw, 0))

    entry_count = len(prepared)

    # 2) TOC 크기 계산
    toc_size = 0
    for name, _, _ in prepared:
        name_bytes = name.encode('utf-8')
        # name_len(2) + name + offset(8) + size(8) + flags(1) = 19 + len(name)
        toc_size += 2 + len(name_bytes) + 8 + 8 + 1

    data_section_offset = HEADER_SIZE + toc_size

    # 3) 각 엔트리의 data offset 계산
    toc_entries: list[tuple[str, int, int, int]] = []  # (name, offset, size, flags)
    current_offset = data_section_offset
    for name, stored, flags in prepared:
        toc_entries.append((name, current_offset, len(stored), flags))
        current_offset += len(stored)

    # 4) 파일 기록
    with open(filepath, 'wb') as f:
        # Header (24 bytes)
        f.write(QONVO_MAGIC)                                    # [0:5] Magic
        f.write(struct.pack('<B', QONVO_FORMAT_VERSION))         # [5]   Version
        f.write(struct.pack('<H', 0))                            # [6:8] Flags (reserved)
        f.write(struct.pack('<I', entry_count))                  # [8:12] Entry count
        f.write(struct.pack('<Q', data_section_offset))          # [12:20] Data offset
        f.write(b'\x00' * 4)                                     # [20:24] Reserved

        # TOC
        for name, offset, size, flags in toc_entries:
            name_bytes = name.encode('utf-8')
            f.write(struct.pack('<H', len(name_bytes)))
            f.write(name_bytes)
            f.write(struct.pack('<Q', offset))
            f.write(struct.pack('<Q', size))
            f.write(struct.pack('<B', flags))

        # Data body
        for _, stored, _ in prepared:
            f.write(stored)

    logger.info(f"[QONVO] Written {entry_count} entries, "
                f"total {current_offset} bytes to {filepath}")


def _parse_toc(f) -> list[tuple[str, int, int, int]]:
    """열린 파일 핸들에서 Header + TOC를 파싱하여 반환.

    파일 포인터는 Header 시작 위치여야 함.

    Returns:
        [(name, offset, size, flags), ...] 리스트
    Raises:
        ValueError: 헤더 손상 시
    """
    header = f.read(HEADER_SIZE)
    if len(header) < HEADER_SIZE:
        raise ValueError("파일이 너무 짧습니다 (헤더 불완전)")

    if header[0:5] != QONVO_MAGIC:
        raise ValueError(f"유효하지 않은 QONVO 파일 (매직: {header[0:5]!r})")

    version = struct.unpack('<B', header[5:6])[0]
    if version > QONVO_FORMAT_VERSION:
        logger.warning(f"[QONVO] 파일 버전 {version} > 지원 버전 {QONVO_FORMAT_VERSION}")

    entry_count = struct.unpack('<I', header[8:12])[0]

    toc: list[tuple[str, int, int, int]] = []
    for _ in range(entry_count):
        name_len = struct.unpack('<H', f.read(2))[0]
        name = f.read(name_len).decode('utf-8')
        offset = struct.unpack('<Q', f.read(8))[0]
        size = struct.unpack('<Q', f.read(8))[0]
        flags = struct.unpack('<B', f.read(1))[0]
        toc.append((name, offset, size, flags))

    return toc


def _read_qonvo_entry(filepath: Path, name: str) -> bytes | None:
    """QONVO 파일에서 단일 엔트리만 seek 기반으로 읽기.

    TOC 파싱 후 해당 이름의 엔트리만 seek → read.
    board.json만 빠르게 읽을 때 사용.
    """
    with open(filepath, 'rb') as f:
        try:
            toc = _parse_toc(f)
        except ValueError:
            return None

        for entry_name, offset, size, flags in toc:
            if entry_name == name:
                f.seek(offset)
                raw = f.read(size)
                if flags & _FLAG_COMPRESSED:
                    raw = zlib.decompress(raw)
                return raw

    return None


_STREAM_CHUNK = 256 * 1024  # 256KB chunks for streaming


def _extract_qonvo_to_dir(filepath: Path, temp_dir: Path) -> bytes:
    """QONVO 파일에서 board.json을 반환하고, 첨부파일은 temp_dir에 스트리밍 추출.

    메모리에 전체 파일을 올리지 않음 — 한 번에 하나의 첨부파일만 처리.

    Args:
        filepath: .qonvo 파일 경로
        temp_dir: 첨부파일 추출 대상 디렉토리

    Returns:
        board.json의 원본 바이트 (디코딩 전)

    Raises:
        ValueError: 파일 손상 또는 board.json 없음
    """
    board_json_data: bytes | None = None
    attachment_count = 0

    with open(filepath, 'rb') as f:
        toc = _parse_toc(f)

        for name, offset, size, flags in toc:
            f.seek(offset)

            if name == 'board.json':
                # board.json은 메모리에 (작은 데이터)
                raw = f.read(size)
                if flags & _FLAG_COMPRESSED:
                    raw = zlib.decompress(raw)
                board_json_data = raw

            elif name.startswith('attachments/') or name.startswith('repositories/'):
                # 첨부파일은 디스크에 직접 스트리밍 (메모리 최소화)
                out_path = temp_dir / name
                out_path.parent.mkdir(parents=True, exist_ok=True)

                if flags & _FLAG_COMPRESSED:
                    # 압축된 경우: 전체 읽기 후 해제 (zlib는 스트리밍 해제 어려움)
                    raw = f.read(size)
                    out_path.write_bytes(zlib.decompress(raw))
                else:
                    # 비압축: 청크 단위 스트리밍 복사
                    remaining = size
                    with open(out_path, 'wb') as out_f:
                        while remaining > 0:
                            chunk_size = min(_STREAM_CHUNK, remaining)
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            out_f.write(chunk)
                            remaining -= len(chunk)

                attachment_count += 1

    if board_json_data is None:
        raise ValueError("board.json 엔트리가 파일 내에 없습니다")

    logger.info(f"[QONVO] Streamed {attachment_count} attachments to {temp_dir}")
    return board_json_data


def _is_qonvo_binary(filepath: Path) -> bool:
    """파일이 QONVO 바이너리 포맷인지 확인 (vs 레거시 ZIP)."""
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(5)
            return magic == QONVO_MAGIC
    except (IOError, OSError):
        return False


def _get_build_config() -> dict:
    """build.toml의 [app] 섹션 읽기"""
    if getattr(sys, 'frozen', False):
        toml_path = Path(sys._MEIPASS) / "build.toml"
    else:
        toml_path = Path(__file__).resolve().parent.parent.parent / "build.toml"
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("app", {})
    except Exception:
        return {}


def _get_app_version() -> str:
    """build.toml에서 앱 버전 읽기"""
    return _get_build_config().get("version", "")


def _migrate_board_data(data: Dict[str, Any], file_version: str) -> Dict[str, Any]:
    """
    보드 데이터 버전 마이그레이션
    - file_version: 파일에 저장된 버전 (예: "1.0", "1.1")
    Returns: 마이그레이션된 데이터
    """
    logger.info(f"[MIGRATE] Migrating board from version {file_version} to {_get_app_version()}")

    # 버전 파싱 (간단한 비교를 위해)
    try:
        file_ver_parts = [int(x) for x in file_version.split('.')] if file_version else [0, 0]
    except ValueError:
        file_ver_parts = [0, 0]
        logger.warning(f"[MIGRATE] Invalid version format: {file_version}, treating as 0.0")

    # Migration 1: v1.0 → v1.1 - Function Node 필드 추가
    if file_ver_parts < [1, 1]:
        logger.info("[MIGRATE] Applying v1.0 → v1.1 migration (Function Node fields)")
        for node in data.get('function_nodes', []):
            # function_id, function_name 필드가 없으면 기본값 설정
            if 'function_id' not in node:
                node['function_id'] = None
                logger.debug(f"[MIGRATE] Added function_id=None to node {node.get('id', 'unknown')}")
            if 'function_name' not in node:
                node['function_name'] = None
                logger.debug(f"[MIGRATE] Added function_name=None to node {node.get('id', 'unknown')}")
            # ai_response 필드 확인
            if 'ai_response' not in node:
                node['ai_response'] = None

    # Migration 2: Checklist 체크 상태 추가 (예정)
    # if file_ver_parts < [1, 2]:
    #     logger.info("[MIGRATE] Applying v1.1 → v1.2 migration (Checklist state)")
    #     for checklist in data.get('checklists', []):
    #         if 'items' in checklist:
    #             # items를 {"text": str, "checked": bool} 형식으로 변환
    #             pass

    logger.info("[MIGRATE] Migration completed successfully")
    return data


def _get_default_qonvo_url() -> str:
    """build.toml에서 default_qonvo URL 읽기"""
    return _get_build_config().get("default_qonvo", "")


class BoardManager:
    """보드 파일 관리"""

    # save/load 동시 실행 방지 Lock (같은 temp 디렉토리 충돌 방지)
    _io_lock = threading.Lock()

    @staticmethod
    def get_boards_dir() -> Path:
        """보드 저장 디렉토리 반환"""
        from v.settings import get_app_data_path
        boards_dir = get_app_data_path() / 'boards'
        boards_dir.mkdir(parents=True, exist_ok=True)
        return boards_dir

    @staticmethod
    def list_boards() -> List[str]:
        """저장된 보드 목록 반환"""
        boards_dir = BoardManager.get_boards_dir()
        boards = []
        for f in boards_dir.glob("*.qonvo"):
            boards.append(f.stem)
        return sorted(boards)

    @staticmethod
    def save(name: str, data: Dict[str, Any]) -> str:
        """
        보드 저장 (원자적 쓰기 + 백업)
        - name: 보드 이름
        - data: 보드 데이터 (nodes, edges 등)
        Returns: 저장된 파일 경로
        """
        boards_dir = BoardManager.get_boards_dir()
        filepath = boards_dir / f"{name}.qonvo"
        temp_filepath = boards_dir / f"{name}.qonvo.tmp"
        backup_filepath = boards_dir / f"{name}.qonvo.backup"

        logger.info(f"[SAVE] Starting board save: {name}")

        with BoardManager._io_lock:
          return BoardManager._save_impl(name, data, boards_dir, filepath, temp_filepath, backup_filepath)

    @staticmethod
    def _save_impl(name, data, boards_dir, filepath, temp_filepath, backup_filepath):
        try:
            # D1: 기존 파일 백업 (최대 3세대 유지, 각 단계 보호)
            if filepath.exists():
                backup3 = boards_dir / f"{name}.qonvo.backup3"
                backup2 = boards_dir / f"{name}.qonvo.backup2"
                try:
                    if backup2.exists():
                        try:
                            if backup3.exists():
                                backup3.unlink()
                        except OSError as e:
                            logger.warning(f"[SAVE] Failed to remove backup3: {e}")
                        try:
                            backup2.rename(backup3)
                        except OSError as e:
                            logger.warning(f"[SAVE] Failed to rotate backup2->backup3: {e}")
                    if backup_filepath.exists():
                        try:
                            backup_filepath.rename(backup2)
                        except OSError as e:
                            logger.warning(f"[SAVE] Failed to rotate backup->backup2: {e}")
                    shutil.copy2(filepath, backup_filepath)
                    logger.info(f"[SAVE] Backup created: {backup_filepath} ({filepath.stat().st_size:,} bytes)")
                except OSError as e:
                    logger.error(f"[SAVE] Backup creation failed: {e}")

            # 메타데이터 추가
            data['name'] = name
            data['version'] = _get_app_version() or '1.0'
            data['saved_at'] = datetime.now().isoformat()

            # 첨부파일 처리
            attachments_map = {}  # 원본 경로 → 아카이브 내 경로
            missing_files = []  # 존재하지 않는 파일 추적

            # Lazy loading 미생성 아이템의 attachments/ 경로를 temp에서 해결
            temp_dir = boards_dir / '.temp' / name

            def _resolve_attachment(fpath: str) -> str | None:
                """파일 경로를 해결하여 실제 읽을 수 있는 경로 반환.
                attachments/ 상대 경로인 경우 temp 디렉토리에서 찾음."""
                if not fpath:
                    return None
                if Path(fpath).exists():
                    return fpath
                normalized = fpath.replace('\\', '/')  # Windows 경로 구분자 정규화
                # Lazy loading 미생성 아이템: attachments/xxx.png → temp_dir/attachments/xxx.png
                if normalized.startswith('attachments/') or normalized.startswith('repositories/'):
                    resolved = temp_dir / normalized
                    if resolved.exists():
                        logger.debug(f"[SAVE] Resolved lazy path: {fpath} → {resolved}")
                        return str(resolved)
                    else:
                        logger.error(f"[SAVE] UNRESOLVABLE attachment: {fpath} (not in {temp_dir})")
                return None

            def _map_file(fpath: str) -> str | None:
                """파일을 아카이브에 매핑. 성공 시 archive_name, 실패 시 None."""
                resolved = _resolve_attachment(fpath)
                if resolved and resolved not in attachments_map:
                    ext = Path(resolved).suffix
                    fsize = Path(resolved).stat().st_size
                    archive_name = f"attachments/{uuid.uuid4().hex}{ext}"
                    attachments_map[resolved] = archive_name
                    logger.info(f"[SAVE] Mapped: {fpath} → {archive_name} ({fsize:,} bytes)")
                    return archive_name
                elif resolved and resolved in attachments_map:
                    return attachments_map[resolved]
                return None

            # 노드의 첨부파일 수집
            def _process_node_attachments(node):
                """노드의 user_files, ai_image_paths, history[].images 아카이브 매핑"""
                new_files = []
                for fpath in node.get('user_files', []):
                    mapped = _map_file(fpath) if fpath else None
                    if mapped:
                        new_files.append(mapped)
                    elif fpath:
                        missing_files.append(fpath)
                        logger.warning(f"[SAVE] Missing user file: {fpath}")
                node['user_files'] = new_files

                new_ai_imgs = []
                for fpath in node.get('ai_image_paths', []):
                    mapped = _map_file(fpath) if fpath else None
                    if mapped:
                        new_ai_imgs.append(mapped)
                    elif fpath:
                        missing_files.append(fpath)
                        logger.warning(f"[SAVE] Missing AI image: {fpath}")
                node['ai_image_paths'] = new_ai_imgs

                # 히스토리 이미지
                for entry in node.get('history', []):
                    new_imgs = []
                    for fpath in entry.get('images', []):
                        mapped = _map_file(fpath) if fpath else None
                        if mapped:
                            new_imgs.append(mapped)
                        elif fpath:
                            missing_files.append(fpath)
                    entry['images'] = new_imgs

            for node in data.get('nodes', []):
                _process_node_attachments(node)

            # 이미지 카드 첨부파일 처리
            for card in data.get('image_cards', []):
                fpath = card.get('image_path', '')
                mapped = _map_file(fpath) if fpath else None
                if mapped:
                    card['image_path'] = mapped
                elif fpath:
                    missing_files.append(fpath)
                    logger.warning(f"[SAVE] Missing image card: {fpath}")
                    card['image_path'] = ''  # 유령 참조 제거

            # 차원 내부 보드 데이터의 첨부파일 처리 (재귀)
            def _process_dimension_attachments(dim_board_data):
                """차원 board_data 내 노드/이미지카드의 첨부파일을 아카이브에 매핑"""
                for node in dim_board_data.get('nodes', []):
                    _process_node_attachments(node)

                for card in dim_board_data.get('image_cards', []):
                    fpath = card.get('image_path', '')
                    mapped = _map_file(fpath) if fpath else None
                    if mapped:
                        card['image_path'] = mapped
                    elif fpath:
                        missing_files.append(fpath)
                        logger.warning(f"[SAVE] Missing dimension image card: {fpath}")
                        card['image_path'] = ''  # 유령 참조 제거

                # 중첩 차원 재귀 처리
                for nested_dim in dim_board_data.get('dimensions', []):
                    nested_bd = nested_dim.get('board_data')
                    if nested_bd:
                        _process_dimension_attachments(nested_bd)

            for dim in data.get('dimensions', []):
                dim_bd = dim.get('board_data')
                if dim_bd:
                    _process_dimension_attachments(dim_bd)

            if missing_files:
                logger.warning(f"[SAVE] {len(missing_files)} files not found, will be skipped")

            # 자료함 노드 폴더 미러링
            repo_file_count = 0
            for repo in data.get('repository_nodes', []):
                folder = repo.get('folder_path', '')
                node_id = repo.get('id')
                if not folder or not node_id or not Path(folder).is_dir():
                    continue

                repo_files = []
                for fpath in sorted(Path(folder).iterdir()):
                    if not fpath.is_file():
                        continue
                    archive_name = f"repositories/{node_id}/{fpath.name}"
                    attachments_map[str(fpath)] = archive_name
                    repo_files.append(fpath.name)
                    repo_file_count += 1

                repo['_mirrored_files'] = repo_files

            logger.info(f"[SAVE] Collected {len(attachments_map)} attachments, mirrored {repo_file_count} repository files")

            # QONVO 바이너리 포맷으로 기록 (원자적 쓰기)
            entries: Dict[str, bytes] = {}
            entries['board.json'] = json.dumps(
                data, ensure_ascii=False, indent=2
            ).encode('utf-8')

            skipped_attachments = []
            for orig_path, archive_path in attachments_map.items():
                try:
                    with open(orig_path, 'rb') as af:
                        entries[archive_path] = af.read()
                except (IOError, OSError) as e:
                    logger.warning(f"[SAVE] Skipping unreadable attachment {orig_path}: {e}")
                    skipped_attachments.append(orig_path)

            if skipped_attachments:
                logger.warning(f"[SAVE] {len(skipped_attachments)} attachments skipped (unreadable)")

            _write_qonvo(temp_filepath, entries)
            file_size = temp_filepath.stat().st_size
            attachment_count = sum(1 for k in entries if k.startswith('attachments/'))
            logger.info(
                f"[SAVE] QONVO written: {file_size:,} bytes, "
                f"{attachment_count} attachments, {len(missing_files)} missing, "
                f"{len(skipped_attachments)} skipped"
            )

            # 저장 검증: board.json 참조와 아카이브 엔트리 비교
            ref_count = 0

            def _verify_attachment_ref(path, context):
                nonlocal ref_count
                if path and path.startswith('attachments/'):
                    if path not in entries:
                        logger.error(f"[SAVE] INTEGRITY: {context} ref {path} NOT in entries!")
                    else:
                        ref_count += 1

            def _verify_board_data_refs(bd, prefix=""):
                """보드 데이터 내 모든 attachment 참조 검증 (차원 재귀 포함)"""
                ctx = f"{prefix}" if prefix else ""
                for card in bd.get('image_cards', []):
                    _verify_attachment_ref(card.get('image_path', ''), f"{ctx}image_card")
                for node in bd.get('nodes', []):
                    for p in node.get('ai_image_paths', []):
                        _verify_attachment_ref(p, f"{ctx}ai_image")
                    for p in node.get('user_files', []):
                        _verify_attachment_ref(p, f"{ctx}user_file")
                    for entry in node.get('history', []):
                        for p in entry.get('images', []):
                            _verify_attachment_ref(p, f"{ctx}history_image")
                for dim in bd.get('dimensions', []):
                    nested_bd = dim.get('board_data')
                    if nested_bd:
                        dim_title = dim.get('title', 'dim')
                        _verify_board_data_refs(nested_bd, f"{ctx}{dim_title}/")

            _verify_board_data_refs(data)
            logger.info(f"[SAVE] Integrity check: {ref_count} refs verified OK")

            # 임시 파일을 실제 파일로 원자적 이동
            temp_filepath.replace(filepath)
            logger.info(f"[SAVE] Board saved successfully: {filepath}")

            # 임시 파일 정리 (이미 replace로 이동되었으므로 존재하지 않음)
            if temp_filepath.exists():
                temp_filepath.unlink()

            return str(filepath)

        except (IOError, OSError) as e:
            logger.error(f"[SAVE] File I/O error: {e}")
            if temp_filepath.exists():
                temp_filepath.unlink()
            raise IOError(f"보드 저장 실패 (파일 오류): {e}")

        except Exception as e:
            logger.error(f"[SAVE] Unexpected error: {e}", exc_info=True)
            if temp_filepath.exists():
                temp_filepath.unlink()
            raise IOError(f"보드 저장 실패 (예상치 못한 오류): {e}")

    @staticmethod
    def load(filepath: str) -> Dict[str, Any]:
        """
        보드 불러오기 — QONVO 바이너리 / 레거시 ZIP 자동 감지
        Returns: 보드 데이터
        """
        filepath = Path(filepath)
        logger.info(f"[LOAD] Loading board: {filepath}")

        if not filepath.exists():
            logger.error(f"[LOAD] File not found: {filepath}")
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {filepath}")

        with BoardManager._io_lock:
            return BoardManager._load_impl(filepath)

    @staticmethod
    def _load_impl(filepath: Path) -> Dict[str, Any]:
        # 스테이징 디렉토리에 추출 후 기존 temp와 원자적 교체
        temp_dir = BoardManager.get_boards_dir() / '.temp' / filepath.stem
        staging_dir = BoardManager.get_boards_dir() / '.temp' / f"{filepath.stem}._staging"
        old_dir = BoardManager.get_boards_dir() / '.temp' / f"{filepath.stem}._old"

        try:
            # 이전 스테이징/올드 잔여물 정리
            for leftover in (staging_dir, old_dir):
                if leftover.exists():
                    try:
                        shutil.rmtree(leftover)
                    except Exception as e:
                        logger.warning(f"[LOAD] Failed to clean leftover {leftover}: {e}")

            staging_dir.mkdir(parents=True, exist_ok=True)

            # ── 스테이징 디렉토리에 추출 ──
            if _is_qonvo_binary(filepath):
                logger.info("[LOAD] Detected QONVO binary format")
                try:
                    board_json_bytes = _extract_qonvo_to_dir(filepath, staging_dir)
                except (ValueError, zlib.error) as e:
                    logger.error(f"[LOAD] QONVO read failed: {e}")
                    raise ValueError(f"QONVO 파일이 손상되었습니다: {e}")

                try:
                    data = json.loads(board_json_bytes)
                except json.JSONDecodeError as e:
                    logger.error(f"[LOAD] Invalid JSON in board.json: {e}")
                    raise ValueError(f"보드 파일 형식이 올바르지 않습니다: {e}")

                file_version = data.get('version', '1.0')
                logger.info(f"[LOAD] board.json loaded, version: {file_version}")
                data = _migrate_board_data(data, file_version)

            else:
                logger.info("[LOAD] Detected legacy ZIP format")
                try:
                    with zipfile.ZipFile(filepath, 'r') as zf:
                        bad_file = zf.testzip()
                        if bad_file:
                            logger.error(f"[LOAD] Corrupted file in ZIP: {bad_file}")
                            raise zipfile.BadZipFile(
                                f"손상된 파일이 포함되어 있습니다: {bad_file}")

                        try:
                            data = json.loads(zf.read('board.json'))
                            file_version = data.get('version', '1.0')
                            logger.info(f"[LOAD] board.json loaded, version: {file_version}")
                            data = _migrate_board_data(data, file_version)
                        except json.JSONDecodeError as e:
                            logger.error(f"[LOAD] Invalid JSON in board.json: {e}")
                            raise ValueError(f"보드 파일 형식이 올바르지 않습니다: {e}")
                        except KeyError:
                            logger.error("[LOAD] board.json not found in ZIP")
                            raise ValueError("board.json 파일이 ZIP 내에 없습니다")

                        attachment_count = 0
                        for name in zf.namelist():
                            if name.startswith('attachments/') or name.startswith('repositories/'):
                                try:
                                    zf.extract(name, staging_dir)
                                    attachment_count += 1
                                except (IOError, OSError) as e:
                                    logger.warning(f"[LOAD] Failed to extract {name}: {e}")

                        logger.info(f"[LOAD] Extracted {attachment_count} attachments to {staging_dir}")

                except zipfile.BadZipFile as e:
                    logger.error(f"[LOAD] Invalid ZIP file: {e}")
                    raise ValueError(f"파일이 손상되었거나 올바르지 않습니다: {e}")

            # D2: 원자적 교체: staging → temp (Windows 안전)
            # 기존 temp가 있으면 old로 이동, staging을 temp로 이동
            if temp_dir.exists():
                try:
                    temp_dir.rename(old_dir)
                except OSError:
                    # rename 실패 시 (다른 프로세스가 잡고 있을 수 있음) 폴백
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception as e:
                        logger.warning(f"[LOAD] Failed to remove old temp: {e}")
                        # rmtree도 실패 시, staging을 대체 경로로 시도
                        if temp_dir.exists():
                            alt_name = f"{filepath.stem}._old_{os.getpid()}"
                            alt_dir = BoardManager.get_boards_dir() / '.temp' / alt_name
                            try:
                                temp_dir.rename(alt_dir)
                            except OSError as e2:
                                logger.error(f"[LOAD] Cannot free temp dir: {e2}")
                                raise OSError(f"Cannot replace temp directory: {e2}")

            try:
                staging_dir.rename(temp_dir)
            except OSError as e:
                # staging rename 실패 시 — staging 내용을 temp로 복사
                logger.warning(f"[LOAD] staging rename failed, falling back to copy: {e}")
                shutil.copytree(staging_dir, temp_dir, dirs_exist_ok=True)
                shutil.rmtree(staging_dir, ignore_errors=True)
            logger.info(f"[LOAD] Atomic swap: staging -> {temp_dir}")

            # old 정리 (백그라운드에서 안전하게)
            if old_dir.exists():
                try:
                    shutil.rmtree(old_dir)
                except Exception as e:
                    logger.warning(f"[LOAD] Failed to cleanup old temp: {e}")

            # ── 공통: 첨부파일 경로를 실제 경로로 변환 ──
            missing_attachments = []

            def _resolve_path(fpath):
                """attachments/ 상대 경로 → temp_dir 실제 경로 변환"""
                if not fpath:
                    return fpath
                normalized = fpath.replace('\\', '/')  # Windows 경로 구분자 정규화
                if normalized.startswith('attachments/'):
                    real_path = temp_dir / normalized
                    if real_path.exists():
                        return str(real_path)
                    else:
                        missing_attachments.append(fpath)
                        return fpath
                return fpath

            def _resolve_node_attachments(node):
                """노드의 user_files, ai_image_paths, history[].images 경로 변환"""
                node['user_files'] = [_resolve_path(f) for f in node.get('user_files', [])]
                node['ai_image_paths'] = [_resolve_path(f) for f in node.get('ai_image_paths', [])]
                for entry in node.get('history', []):
                    entry['images'] = [_resolve_path(f) for f in entry.get('images', [])]

            def _resolve_board_data_attachments(bd):
                """보드 데이터 내 모든 첨부파일 경로 변환 (차원 재귀 포함)"""
                for node in bd.get('nodes', []):
                    _resolve_node_attachments(node)
                for card in bd.get('image_cards', []):
                    fpath = card.get('image_path', '')
                    if fpath:
                        card['image_path'] = _resolve_path(fpath)
                for dim in bd.get('dimensions', []):
                    nested_bd = dim.get('board_data')
                    if nested_bd:
                        _resolve_board_data_attachments(nested_bd)

            _resolve_board_data_attachments(data)

            # 자료함 노드 폴더 경로 변환
            for repo in data.get('repository_nodes', []):
                mirrored = repo.get('_mirrored_files')
                if not mirrored:
                    continue
                node_id = repo.get('id')
                extracted_dir = temp_dir / 'repositories' / str(node_id)
                if extracted_dir.is_dir():
                    orig_folder = repo.get('folder_path', '')
                    if not orig_folder or not Path(orig_folder).is_dir():
                        repo['folder_path'] = str(extracted_dir)
                        logger.info(f"[LOAD] Repo #{node_id} -> extracted: {extracted_dir}")
                    else:
                        logger.info(f"[LOAD] Repo #{node_id} -> original: {orig_folder}")

            if missing_attachments:
                logger.warning(
                    f"[LOAD] {len(missing_attachments)} attachments not found")

            logger.info(
                f"[LOAD] Board loaded successfully: "
                f"{len(data.get('nodes', []))} nodes, "
                f"{len(data.get('edges', []))} edges")

            return data

        except Exception as e:
            # 실패 시 스테이징만 정리, 기존 temp는 보존 (데이터 유실 방지)
            if staging_dir.exists():
                try:
                    shutil.rmtree(staging_dir)
                except Exception as cleanup_error:
                    logger.warning(f"[LOAD] Failed to cleanup staging dir: {cleanup_error}")
            logger.error(f"[LOAD] Load failed, existing temp preserved: {e}")
            raise

    @staticmethod
    def fetch_default(url: str) -> str:
        """URL에서 .qonvo 파일을 다운로드하여 보드 디렉토리에 저장"""
        import urllib.request

        boards_dir = BoardManager.get_boards_dir()

        # URL에서 파일명 추출
        filename = url.rsplit('/', 1)[-1] if '/' in url else ''
        if not filename.endswith('.qonvo'):
            filename = 'default.qonvo'

        filepath = boards_dir / filename

        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Qonvo'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(filepath, 'wb') as f:
                    f.write(resp.read())
        except Exception as e:
            logger.error(f"[FETCH] Failed to download default board from {url}: {e}")
            raise IOError(f"Default board download failed: {e}")

        return str(filepath)

    @staticmethod
    def delete(name: str) -> bool:
        """보드 삭제"""
        filepath = BoardManager.get_boards_dir() / f"{name}.qonvo"
        if filepath.exists():
            filepath.unlink()
            return True
        return False
