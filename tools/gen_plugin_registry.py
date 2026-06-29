#!/usr/bin/env python3
"""플러그인 레지스트리(registry.json) 생성기.

plugins/*.py 를 AST 로 스캔해 각 플러그인의 메타데이터(NAME/VERSION/DESCRIPTION/
MODELS + 선택 AUTHOR/MIN_APP_VERSION)를 읽고, 파일 sha256 과 GitHub raw 다운로드
URL 을 붙여 repo 루트에 registry.json 을 쓴다.

- 메타는 **플러그인 .py 의 클래스 속성이 단일 진실원**. 이 스크립트는 그걸 미러할 뿐.
  → 의존성(google-genai/anthropic 등)을 임포트하지 않으려고 모듈 실행 대신 AST 로 읽는다.
- 다운로드 URL 의 git ref 는 기본적으로 **파일별 마지막 커밋 SHA**(불변) 를 쓴다.
  `--ref <tag>` 로 전체를 특정 태그/브랜치에 고정할 수도 있다.

사용:
    python tools/gen_plugin_registry.py                 # 파일별 커밋 SHA 로 고정
    python tools/gen_plugin_registry.py --ref plugins-v1   # 명시 태그로 고정
    python tools/gen_plugin_registry.py --check         # 드리프트만 검사(쓰지 않음)
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# Windows 한글 콘솔(cp949)에서 em-dash/한글 출력이 깨지지 않도록 UTF-8 강제.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

REPO = "BDbread72/qonvo"
ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = ROOT / "plugins"
OUT = ROOT / "registry.json"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}"

# 메타 키 → registry 필드. 문자열/딕셔너리 리터럴만 허용(ast.literal_eval).
_META_KEYS = ("NAME", "VERSION", "DESCRIPTION", "MODELS", "AUTHOR", "MIN_APP_VERSION")


def _plugin_class_meta(py: Path) -> dict | None:
    """PLUGIN_CLASS 로 export 된 클래스의 클래스-레벨 리터럴 속성을 AST 로 추출."""
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    except (SyntaxError, UnicodeDecodeError) as e:
        print(f"  ! parse failed {py.name}: {e}", file=sys.stderr)
        return None

    # 1) PLUGIN_CLASS = <Name> 찾기
    exported: str | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "PLUGIN_CLASS":
                    if isinstance(node.value, ast.Name):
                        exported = node.value.id
    if not exported:
        return None

    # 2) 해당 클래스의 클래스-레벨 할당 수집
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == exported),
        None,
    )
    if cls is None:
        return None

    meta: dict = {}
    for stmt in cls.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for tgt in stmt.targets:
            if isinstance(tgt, ast.Name) and tgt.id in _META_KEYS:
                try:
                    meta[tgt.id] = ast.literal_eval(stmt.value)
                except (ValueError, TypeError):
                    pass  # 리터럴이 아니면 스킵
    return meta


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _blob_at(ref: str, rel: str) -> bytes | None:
    """git 에 저장된(= GitHub raw 가 서빙하는) 바이트. 워킹카피 CRLF 와 무관하게
    raw 와 동일한 LF 콘텐츠를 돌려준다 → sha256 이 다운로드와 일치하도록."""
    try:
        return subprocess.check_output(
            ["git", "show", f"{ref}:{rel}"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _file_ref(rel: str, override: str | None) -> tuple[str, bool]:
    """다운로드 URL 에 쓸 git ref 와 '깨끗함(커밋과 일치)' 여부."""
    if override:
        return override, True
    sha = _git("log", "-1", "--format=%H", "--", rel)
    if not sha:
        return "HEAD", False
    # 워킹트리에 미커밋 변경이 있으면 SHA 고정이 내용과 어긋남 → 경고용 플래그
    dirty = bool(_git("status", "--porcelain", "--", rel))
    return sha, not dirty


def main() -> int:
    ap = argparse.ArgumentParser(description="qonvo 플러그인 registry.json 생성")
    ap.add_argument("--ref", help="모든 플러그인 다운로드 URL 을 이 git ref(태그/브랜치)로 고정")
    ap.add_argument("--check", action="store_true", help="생성하지 않고 기존 registry.json 과 비교만")
    args = ap.parse_args()

    if not PLUGINS_DIR.is_dir():
        print(f"plugins dir not found: {PLUGINS_DIR}", file=sys.stderr)
        return 1

    plugins = []
    warned_dirty = False
    for py in sorted(PLUGINS_DIR.glob("*.py")):
        if py.name.startswith("_"):
            continue
        meta = _plugin_class_meta(py)
        if not meta or "NAME" not in meta:
            print(f"  - skip {py.name} (no PLUGIN_CLASS metadata)")
            continue

        rel = f"plugins/{py.name}"
        ref, clean = _file_ref(rel, args.ref)
        # sha256/size 는 raw 가 서빙하는 git blob 기준(워킹카피 CRLF 무관). git 불가 시 워킹카피 폴백.
        data = _blob_at(ref, rel)
        if data is None:
            data = py.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        if not clean and not args.ref:
            warned_dirty = True
            print(f"  ! {py.name}: 미커밋 변경 — URL 이 {ref[:8]} 에 고정되지만 내용과 다를 수 있음")

        plugins.append({
            "id": py.stem,
            "name": meta.get("NAME", py.stem),
            "version": str(meta.get("VERSION", "0.0.0")),
            "description": meta.get("DESCRIPTION", ""),
            "author": meta.get("AUTHOR", "qonvo"),
            "models": meta.get("MODELS", {}),
            "min_app_version": meta.get("MIN_APP_VERSION", ""),
            "size": len(data),
            "sha256": sha256,
            "download": f"{RAW_BASE}/{ref}/{rel}",
        })
        print(f"  + {py.stem}  v{meta.get('VERSION', '?')}  ({len(meta.get('MODELS', {}))} models)  @ {ref[:8]}")

    registry = {
        "schema": 1,
        "repo": REPO,
        "plugins": plugins,
    }
    text = json.dumps(registry, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if old == text:
            print("registry.json 최신 — 드리프트 없음")
            return 0
        print("registry.json 이 오래됨 — `python tools/gen_plugin_registry.py` 재실행 필요", file=sys.stderr)
        return 2

    OUT.write_text(text, encoding="utf-8")
    print(f"\n→ {OUT}  ({len(plugins)} plugins)")
    if warned_dirty:
        print("⚠ 미커밋 플러그인 변경이 있음 — 커밋 후 재생성하면 URL 이 정확히 고정됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
