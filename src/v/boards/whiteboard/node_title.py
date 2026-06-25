"""노드 이름 — 헤더에 일체화된 편집형 제목.

모든 노드가 동일한 방식으로 이름을 갖게 한다. 두 가지 표현:

- `NodeTitleEdit` (QLineEdit): **헤더가 있는 노드**(self.header)는 헤더 바 *안에* 이름칸을
  넣어 노드의 일부처럼 보이게 한다(평소 라벨, 더블클릭하면 편집). ← 주 경로.
- `NodeTitleItem` (QGraphicsItem): 헤더가 없는 노드(이미지·파일·디멘션·그룹·게이트)는
  자식 아이템으로 이름을 띄운다(폴백).

둘 다 commit 시 `on_rename(node_id, name)`(plugin.rename_node) 을 호출한다 — 영속/서버sync는
plugin 쪽 단일 경로. 기본 이름은 타입명(`default_name_for`), 글자색은 노드별(`title_color_for`).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainterPath, QTextCursor
from PyQt6.QtWidgets import QGraphicsTextItem, QLineEdit


# 제목 메타데이터는 각 노드 클래스의 클래스 속성으로 산다(중앙 룩업표 아님):
#   TITLE_NAME   기본 표시 이름(타입명)   — 미지정 시 "Node"
#   TITLE_COLOR  제목 글자색             — 미지정 시 _DEFAULT_COLOR
#   WANTS_TITLE  공용 편집형 이름표 부착 여부 — 자기 라벨을 이미 가진 노드(버튼·게이트)는 False
# 기본값은 BaseNode/SceneItemMixin 에 선언되어 있고, 각 노드가 필요 시 오버라이드한다.
_DEFAULT_COLOR = "#c8cdd6"


def default_name_for(obj) -> str:
    """노드의 기본 표시 이름 — 노드 클래스의 TITLE_NAME 속성."""
    return getattr(obj, "TITLE_NAME", None) or "Node"


def title_color_for(obj) -> str:
    """제목 글자색 — 노드 클래스의 TITLE_COLOR 속성(없으면 기본)."""
    return getattr(obj, "TITLE_COLOR", None) or _DEFAULT_COLOR


def wants_title(obj) -> bool:
    """이 노드에 공용 편집형 이름표를 붙일지 — 노드 클래스의 WANTS_TITLE 속성."""
    return bool(getattr(obj, "WANTS_TITLE", True))


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip()[:60]


class NodeTitleEdit(QLineEdit):
    """헤더 바 안의 편집형 이름. 평소 읽기전용(라벨처럼), 더블클릭하면 편집."""

    def __init__(self, node_id: int, name: str, on_rename, color: str, parent=None):
        super().__init__(name, parent)
        self._node_id = node_id
        self._on_rename = on_rename
        self._committed = name
        self.setReadOnly(True)
        self.setFrame(False)
        self.setCursorPosition(0)
        self.setMinimumWidth(24)
        self.setStyleSheet(
            "QLineEdit { background: transparent; border: none; padding: 0 1px;"
            f" color: {color}; font-size: 12px; font-weight: bold; }}"
            " QLineEdit[readOnly=\"false\"] { background: rgba(255,255,255,0.12);"
            " border-radius: 4px; }")
        self.setToolTip(f"노드 #{node_id} · 더블클릭해서 이름 변경")
        self.editingFinished.connect(self._commit)

    def mouseDoubleClickEvent(self, e):
        if self.isReadOnly():
            self.setReadOnly(False)
            self.selectAll()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.blockSignals(True)
            self.setText(self._committed)
            self.blockSignals(False)
            self._finish()
            e.accept()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        self._commit()
        super().focusOutEvent(e)

    def _commit(self):
        if self.isReadOnly():
            return
        name = _clean(self.text()) or self._committed
        if self.text() != name:
            self.blockSignals(True)
            self.setText(name)
            self.blockSignals(False)
        changed = name != self._committed
        self._committed = name
        self._finish()
        if changed and self._on_rename:
            try:
                self._on_rename(self._node_id, name)
            except Exception:
                pass

    def _finish(self):
        self.setReadOnly(True)
        self.setCursorPosition(0)
        self.deselect()

    def set_name(self, name: str):
        """외부(원격/복원)에서 이름 갱신 — 편집 중이 아니면만."""
        if not self.isReadOnly():
            return
        if name != self.text():
            self._committed = name
            self.blockSignals(True)
            self.setText(name)
            self.blockSignals(False)
            self.setCursorPosition(0)


_FONT = QFont()
_FONT.setPointSize(9)
_FONT.setBold(True)


class NodeTitleItem(QGraphicsTextItem):
    """헤더가 없는 노드(이미지·파일·디멘션·그룹·게이트)용 — 노드 위에 자식으로 이름 표시."""

    def __init__(self, node_id: int, name: str, on_rename, color: str, parent=None):
        super().__init__(name, parent)
        self._node_id = node_id
        self._on_rename = on_rename
        self._editing = False
        self._before = name
        self.setFont(_FONT)
        self.setDefaultTextColor(QColor(color))
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setPos(4, -21)
        self.setZValue(5)
        self.setToolTip(f"노드 #{node_id} · 더블클릭해서 이름 변경")

    def set_name(self, name: str):
        if self._editing:
            return
        if name != self.toPlainText():
            self.setPlainText(name)

    def paint(self, p, opt, widget=None):
        # 캔버스/이미지 위에서 읽히도록 옅은 배경.
        r = self.boundingRect().adjusted(-4, -1, 4, 1)
        p.setRenderHint(p.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(r, 5, 5)
        p.fillPath(path, QColor(20, 21, 24, 170 if not self._editing else 230))
        super().paint(p, opt, widget)

    def boundingRect(self):
        return super().boundingRect().adjusted(-5, -2, 5, 2)

    def mouseDoubleClickEvent(self, e):
        self._begin_edit()
        e.accept()

    def _begin_edit(self):
        self._editing = True
        self._before = self.toPlainText()
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        cur = self.textCursor()
        cur.select(QTextCursor.SelectionType.Document)
        self.setTextCursor(cur)
        self.update()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit()
            e.accept()
            return
        if e.key() == Qt.Key.Key_Escape:
            self.setPlainText(self._before)
            self._commit()
            e.accept()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        if self._editing:
            self._commit()
        super().focusOutEvent(e)

    def _commit(self):
        if not self._editing:
            return
        self._editing = False
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        name = _clean(self.toPlainText()) or self._before
        self.setPlainText(name)
        self.update()
        if name != self._before and self._on_rename:
            try:
                self._on_rename(self._node_id, name)
            except Exception:
                pass
