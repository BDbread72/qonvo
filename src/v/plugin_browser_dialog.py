"""플러그인 둘러보기/설치 다이얼로그.

설정 → 플러그인 페이지의 "둘러보기/업데이트" 버튼이 연다. registry.json 을 받아
설치 가능한 플러그인을 카드로 보여주고, 원클릭 설치/업데이트한다. 설치본은
PluginRegistry 의 discovered 메타와 버전 비교해 상태(미설치/설치됨/업데이트)를 표시.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QScrollArea, QFrame,
)
from PyQt6.QtCore import pyqtSignal

from q import t
from v import plugin_registry_client as prc


class PluginBrowserDialog(QDialog):
    """레지스트리 기반 플러그인 설치/업데이트 UI."""

    # 무언가 설치/갱신되면 발생 → 부모(설정) 가 플러그인 목록 새로고침
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("plugin.browse_title"))
        self.setMinimumSize(560, 480)
        self.setModal(True)
        self.setStyleSheet("QDialog { background-color: #1e1e1e; } QLabel { color: #ddd; }")

        self._rows: dict[str, dict] = {}    # plugin_id -> {frame, btn, status}
        self._threads: list = []            # 설치 스레드 보관(GC 방지)
        self._fetch = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        title = QLabel(t("plugin.browse_title"))
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #fff;")
        root.addWidget(title)
        hint = QLabel(t("plugin.browse_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #999; font-size: 11px;")
        root.addWidget(hint)

        self._status = QLabel(t("plugin.browse_loading"))
        self._status.setStyleSheet("color: #888; font-size: 12px; padding: 8px 0;")
        root.addWidget(self._status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch()
        scroll.setWidget(self._list_host)
        root.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        btn_close = QPushButton(t("common.close") if _has("common.close") else "닫기")
        btn_close.setStyleSheet("padding: 6px 16px; border-radius: 6px;")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        self._load()

    # ── 데이터 로드 ──────────────────────────────────────────────────────
    def _installed_map(self) -> dict:
        from v.model_plugin import PluginRegistry
        return {
            p["id"]: p["version"]
            for p in PluginRegistry.instance().get_discovered_plugins()
        }

    def _load(self):
        if prc.RegistryFetchThread is None:
            self._status.setText(t("plugin.browse_error").replace("{err}", "no Qt"))
            return
        self._fetch = prc.RegistryFetchThread(prc.REGISTRY_URL, self)
        self._fetch.loaded.connect(self._on_loaded)
        self._fetch.failed.connect(self._on_failed)
        self._fetch.start()

    def _on_failed(self, err: str):
        self._status.setText(t("plugin.browse_error").replace("{err}", err))

    def _on_loaded(self, registry: dict):
        plugins = registry.get("plugins", [])
        if not plugins:
            self._status.setText(t("plugin.browse_empty"))
            return
        self._status.setText(t("plugin.browse_count").replace("{n}", str(len(plugins))))
        installed = self._installed_map()
        for entry in plugins:
            self._add_card(entry, installed)

    # ── 카드 ─────────────────────────────────────────────────────────────
    def _add_card(self, entry: dict, installed: dict):
        pid = entry.get("id", "?")
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background: #262626; border: 1px solid #333; border-radius: 8px; }"
        )
        v = QVBoxLayout(frame)
        v.setContentsMargins(14, 10, 14, 12)
        v.setSpacing(4)

        head = QHBoxLayout()
        name = QLabel(f'{entry.get("name", pid)}')
        name.setStyleSheet("font-size: 14px; font-weight: 600; color: #fff; border: none;")
        head.addWidget(name)
        ver = QLabel(f'v{entry.get("version", "?")}')
        ver.setStyleSheet("color: #7aa2ff; font-size: 12px; border: none;")
        head.addWidget(ver)
        head.addStretch()
        author = QLabel(entry.get("author", ""))
        author.setStyleSheet("color: #777; font-size: 11px; border: none;")
        head.addWidget(author)
        v.addLayout(head)

        desc = QLabel(entry.get("description", ""))
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; font-size: 11px; border: none;")
        v.addWidget(desc)

        models = entry.get("models", {})
        if models:
            m = QLabel("· " + ", ".join(list(models.values())[:6]) +
                       (" …" if len(models) > 6 else ""))
            m.setWordWrap(True)
            m.setStyleSheet("color: #888; font-size: 10px; border: none;")
            v.addWidget(m)

        row = QHBoxLayout()
        status_lbl = QLabel("")
        status_lbl.setStyleSheet("font-size: 11px; border: none;")
        row.addWidget(status_lbl)
        row.addStretch()
        btn = QPushButton("")
        btn.setStyleSheet("padding: 5px 14px; border-radius: 6px; font-size: 12px;")
        btn.clicked.connect(lambda _=False, e=entry: self._install(e))
        row.addWidget(btn)
        v.addLayout(row)

        self._list_layout.insertWidget(self._list_layout.count() - 1, frame)
        self._rows[pid] = {"frame": frame, "btn": btn, "status": status_lbl, "entry": entry}
        self._apply_status(pid, prc.status_for(entry, installed))

    def _apply_status(self, pid: str, status: str):
        r = self._rows.get(pid)
        if not r:
            return
        btn, lbl = r["btn"], r["status"]
        if status == "installed":
            lbl.setText("✓ " + t("plugin.state_installed"))
            lbl.setStyleSheet("color: #6ac46a; font-size: 11px; border: none;")
            btn.setText(t("plugin.btn_installed"))
            btn.setEnabled(False)
        elif status == "update":
            lbl.setText("↑ " + t("plugin.state_update"))
            lbl.setStyleSheet("color: #e0a13a; font-size: 11px; border: none;")
            btn.setText(t("plugin.btn_update"))
            btn.setEnabled(True)
        else:  # available
            lbl.setText("")
            btn.setText(t("plugin.btn_install"))
            btn.setEnabled(True)

    # ── 설치 ─────────────────────────────────────────────────────────────
    def _install(self, entry: dict):
        from v.model_plugin import get_plugins_dir
        pid = entry.get("id", "?")
        r = self._rows.get(pid)
        if r:
            r["btn"].setEnabled(False)
            r["btn"].setText(t("plugin.btn_installing"))
        th = prc.PluginInstallThread(entry, get_plugins_dir(), self)
        th.done.connect(self._on_installed)
        th.failed.connect(self._on_install_failed)
        self._threads.append(th)
        th.start()

    def _on_installed(self, pid: str, _path: str):
        # 사용자 폴더에 파일이 생겼으니 레지스트리 재발견 → 부모 새로고침 신호
        try:
            from v.model_plugin import PluginRegistry
            PluginRegistry.instance().load_all()
        except Exception:
            pass
        self._apply_status(pid, "installed")
        self.changed.emit()

    def _on_install_failed(self, pid: str, err: str):
        r = self._rows.get(pid)
        if r:
            r["status"].setText("✗ " + err)
            r["status"].setStyleSheet("color: #e06a6a; font-size: 11px; border: none;")
            # 재시도 가능하게 버튼 복구
            self._apply_status(pid, prc.status_for(r["entry"], self._installed_map()))


def _has(key: str) -> bool:
    """번역 키 존재 여부 — 없으면 폴백 텍스트를 쓰기 위함."""
    try:
        return t(key) != key
    except Exception:
        return False
