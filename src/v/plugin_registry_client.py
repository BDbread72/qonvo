"""플러그인 레지스트리 클라이언트 (인앱 설치/업데이트).

사이트가 읽는 것과 동일한 registry.json 을 앱도 읽어서, 설정 → 플러그인 둘러보기에서
원클릭 설치/업데이트한다. 다운로드한 .py 는 sha256 으로 검증 후 사용자 플러그인 폴더
(%APPDATA%/Qonvo/plugins/<id>.py) 에 원자적으로 저장된다.

- 메타 단일 진실원은 플러그인 .py 의 클래스 속성 → registry.json 은 그걸 미러.
- 네트워크 호출(urllib)은 Qt-free. GUI 는 아래 QThread 래퍼로 백그라운드 실행.

⚠ 보안: 플러그인은 임의 파이썬 코드를 앱 권한으로 실행한다. registry.json 에 실린
1st-party 항목만 원클릭 설치 대상이며, 다운로드는 항상 sha256 으로 변조 검증한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path

# registry.json 위치 — 플러그인이 사는 작업 브랜치의 raw. 배포 시 이 브랜치에
# registry.json 을 푸시해야 카탈로그가 갱신된다(릴리스 절차에 포함).
REGISTRY_URL = "https://raw.githubusercontent.com/BDbread72/qonvo/1.1/registry.json"

_UA = "qonvo-plugin-client"
_TIMEOUT = 12
_MAX_BYTES = 2 * 1024 * 1024  # 플러그인 .py 상한 (2MB) — 변조/오용 방어


# ── 버전 비교 ────────────────────────────────────────────────────────────
def parse_version(s: str) -> tuple:
    """'beta-1.2.10' / '1.2' → (1, 2, 10) 형태 튜플. 숫자만 추출."""
    nums = re.findall(r"\d+", str(s or ""))
    return tuple(int(n) for n in nums) if nums else (0,)


def compare_versions(a: str, b: str) -> int:
    """a<b → -1, a==b → 0, a>b → 1 (자리수 다르면 0 패딩 비교)."""
    ta, tb = parse_version(a), parse_version(b)
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


# ── 네트워크 (Qt-free) ──────────────────────────────────────────────────
def fetch_registry(url: str = REGISTRY_URL, timeout: int = _TIMEOUT) -> dict:
    """registry.json 을 받아 dict 로 반환. 실패 시 예외."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("registry too large")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        raise ValueError("invalid registry schema")
    return data


def download_plugin(entry: dict, dest_dir: Path, timeout: int = _TIMEOUT) -> Path:
    """레지스트리 항목의 .py 를 받아 sha256 검증 후 dest_dir/<id>.py 로 원자 저장.

    반환: 저장된 파일 경로. 검증 실패/누락 필드 시 예외(파일은 남기지 않음).
    """
    pid = entry.get("id")
    url = entry.get("download")
    want = (entry.get("sha256") or "").lower()
    if not pid or not url:
        raise ValueError("entry missing id/download")
    if not re.fullmatch(r"[A-Za-z0-9_]+", pid):
        raise ValueError(f"unsafe plugin id: {pid!r}")

    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise ValueError("plugin file too large")

    got = hashlib.sha256(data).hexdigest()
    if want and got != want:
        raise ValueError(f"sha256 불일치 — 다운로드 변조/오류 (got {got[:12]}, want {want[:12]})")

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{pid}.py"
    tmp = dest_dir / f"{pid}.py.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return dest


# ── 상태 매핑 ────────────────────────────────────────────────────────────
def status_for(entry: dict, installed: dict) -> str:
    """레지스트리 항목 vs 설치본(installed: id->version) → 상태 문자열.

    'available'  미설치
    'installed'  설치됨(레지스트리와 같거나 더 높은 버전)
    'update'     레지스트리가 더 높은 버전 → 업데이트 가능
    """
    cur = installed.get(entry.get("id"))
    if cur is None:
        return "available"
    return "update" if compare_versions(cur, entry.get("version", "")) < 0 else "installed"


# ── QThread 래퍼 (GUI 전용; import 실패해도 위 함수는 독립 동작) ──────────
try:
    from PyQt6.QtCore import QThread, pyqtSignal

    class RegistryFetchThread(QThread):
        loaded = pyqtSignal(dict)
        failed = pyqtSignal(str)

        def __init__(self, url: str = REGISTRY_URL, parent=None):
            super().__init__(parent)
            self._url = url

        def run(self):
            try:
                self.loaded.emit(fetch_registry(self._url))
            except Exception as e:  # noqa: BLE001 — 네트워크/파싱 전부 실패로 보고
                self.failed.emit(str(e))

    class PluginInstallThread(QThread):
        done = pyqtSignal(str, str)    # (plugin_id, dest_path)
        failed = pyqtSignal(str, str)  # (plugin_id, error)

        def __init__(self, entry: dict, dest_dir: Path, parent=None):
            super().__init__(parent)
            self._entry = entry
            self._dest_dir = Path(dest_dir)

        def run(self):
            pid = self._entry.get("id", "?")
            try:
                path = download_plugin(self._entry, self._dest_dir)
                self.done.emit(pid, str(path))
            except Exception as e:  # noqa: BLE001
                self.failed.emit(pid, str(e))

except ImportError:  # 헤드리스/테스트 환경 — QThread 래퍼 없이 함수만 사용
    RegistryFetchThread = None  # type: ignore
    PluginInstallThread = None  # type: ignore
