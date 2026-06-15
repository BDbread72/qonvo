"""보드 → DM 공유 — 캔버스 이미지를 동료 merri DM 으로 보낸다.

캔버스 이미지카드 우클릭 → "동료에게 보내기" → 연락처 선택 → 업로드+전송.
merri_client 를 재사용하며 네트워크는 워커 스레드에서 처리한다.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QThread, QBuffer, QByteArray, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QMessageBox,
)

from .merri_client import MerriClient, get_contacts


def _png_bytes(pixmap: QPixmap) -> bytes:
    if pixmap is None or pixmap.isNull():
        return b""
    ba = QByteArray(); buf = QBuffer(ba); buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    return ba.data()


class _Call(QThread):
    done = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent); self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:
            self.fail.emit(str(e))


class _ShareDialog(QDialog):
    """연락처 선택 → 이미지 DM 전송."""

    def __init__(self, client: MerriClient, data: bytes, parent=None):
        super().__init__(parent)
        self._client = client; self._data = data
        self._me_id = ""; self._jobs: set = set()
        self.setWindowTitle("동료에게 보내기")
        self.setModal(True); self.resize(360, 460)
        self.setStyleSheet("QDialog{background:#252525;}")
        v = QVBoxLayout(self); v.setContentsMargins(14, 14, 14, 14); v.setSpacing(8)
        t = QLabel("이미지를 보낼 동료를 고르세요")
        t.setStyleSheet("color:#fff;font-weight:bold;font-size:14px;background:transparent;")
        v.addWidget(t)
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget{background:#1e1e1e;color:#ddd;border:1px solid #444;border-radius:8px;}"
            "QListWidget::item{padding:9px;border-radius:6px;}"
            "QListWidget::item:hover{background:#33363c;}"
            "QListWidget::item:selected{background:#0d6efd;}")
        self._list.itemDoubleClicked.connect(lambda _i: self._send())
        v.addWidget(self._list, 1)
        self._status = QLabel("불러오는 중…"); self._status.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        v.addWidget(self._status)
        row = QHBoxLayout()
        c = QPushButton("취소"); c.clicked.connect(self.reject); row.addWidget(c)
        row.addStretch()
        self._send_btn = QPushButton("보내기"); self._send_btn.setEnabled(False)
        self._send_btn.setStyleSheet("padding:7px 18px;background:#43b581;color:#fff;font-weight:bold;border-radius:6px;")
        self._send_btn.clicked.connect(self._send); row.addWidget(self._send_btn)
        v.addLayout(row)
        self._load()

    def _run(self, fn, on_done, on_fail=None):
        job = _Call(fn, self); self._jobs.add(job)
        job.done.connect(on_done)
        job.fail.connect(on_fail or (lambda m: self._status.setText(f"오류: {m}")))
        job.done.connect(lambda *_: self._jobs.discard(job))
        job.fail.connect(lambda *_: self._jobs.discard(job))
        job.start()

    def _load(self):
        ids = get_contacts()
        if not ids:
            self._status.setText("연락처가 없어요. People에서 먼저 동료를 추가하세요.")
            return
        self._run(lambda: (self._client.me(), self._client.users_by_ids(ids)), self._on_loaded)

    def _on_loaded(self, result):
        me, users = result
        self._me_id = me.get("id", "")
        self._list.clear()
        for u in users:
            it = QListWidgetItem(u.get("username", "?"))
            it.setData(Qt.ItemDataRole.UserRole, u)
            self._list.addItem(it)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._send_btn.setEnabled(True)
        self._status.setText("")

    def _send(self):
        it = self._list.currentItem()
        if not it:
            return
        user = it.data(Qt.ItemDataRole.UserRole)
        uid = user.get("id", "")
        self._send_btn.setEnabled(False); self._status.setText("보내는 중…")
        cli = self._client; data = self._data; me_id = self._me_id

        def work():
            ch = cli.direct_channel(me_id, uid).get("id", "")
            if not ch:
                return False
            fid = cli.upload_file(ch, "board-image.png", data)
            if not fid:
                return False
            cli.create_post(ch, "", file_ids=[fid])
            return True

        def done(ok):
            if ok:
                self.accept()
            else:
                self._send_btn.setEnabled(True)
                self._status.setText("전송 실패")
        self._run(work, done)


def share_pixmap_to_contact(pixmap: QPixmap, parent=None) -> None:
    """캔버스 이미지(QPixmap)를 동료 DM 으로 보낸다."""
    cli = MerriClient.from_profile()
    if cli is None:
        QMessageBox.information(parent, "동료에게 보내기",
                               "merri 로그인이 필요합니다. People 창에서 로그인하세요.")
        return
    data = _png_bytes(pixmap)
    if not data:
        QMessageBox.warning(parent, "동료에게 보내기", "이미지를 읽을 수 없습니다.")
        return
    _ShareDialog(cli, data, parent).exec()
