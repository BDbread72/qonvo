"""
모델 선택 — 명령 팔레트(검색 중심) 피커.

기존 노드는 평면 QComboBox 에 전 모델을 때려넣어 모델이 많아지면 찾기 어려웠다.
여기서는 헤더에 현재 모델만 작은 버튼으로 보여주고, 누르면 검색창+리스트 팝업이 떠
타이핑으로 즉시 필터링한다(provider/modality 태그까지 검색 대상).

`ModelSelectorButton` 은 chat_node 가 쓰던 QComboBox API 일부를 그대로 노출하는
드롭인 위젯이다:
    addItem(text, data) / findData(data) -> int / setCurrentIndex(i)
    currentData() / currentText() / count() / setEnabled(bool)
    currentIndexChanged(int)  시그널
덕분에 주변 코드는 거의 수정 없이 콤보 → 팔레트로 교체된다.
"""
from PyQt6.QtWidgets import (
    QPushButton, QFrame, QVBoxLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize

from v.theme import Theme
from v.model_plugin import get_all_model_meta


# provider 별 색상 점(검색·구분용). 미정의 provider 는 회색.
_PROVIDER_COLORS = {
    "gemini": "#4a90e2",
    "anthropic": "#d97757",
    "openai": "#10a37f",
}
_DEFAULT_PROVIDER_COLOR = Theme.TEXT_TERTIARY


def _provider_color(provider: str) -> str:
    return _PROVIDER_COLORS.get((provider or "").lower(), _DEFAULT_PROVIDER_COLOR)


class _RowWidget(QWidget):
    """팝업 리스트 한 줄: ● provider점 + 모델명 + 우측 dim 태그(provider · modality)."""

    def __init__(self, text: str, provider: str, modality: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {_provider_color(provider)}; font-size: 11px;")
        lay.addWidget(dot)

        name = QLabel(text)
        name.setStyleSheet(f"color: {Theme.TEXT_PRIMARY}; font-size: 12px; background: transparent;")
        lay.addWidget(name)

        lay.addStretch()

        tag_bits = [b for b in (provider, modality) if b]
        tag = QLabel("  ·  ".join(tag_bits))
        tag.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; background: transparent;")
        lay.addWidget(tag)


class ModelPickerPopup(QFrame):
    """검색창 + 필터 리스트 팝업. 타이핑→필터, ↑↓ 이동, Enter 선택, Esc 닫기."""

    selected = pyqtSignal(int)  # 선택된 model index (버튼의 _items 기준)

    def __init__(self, items, current_index, parent=None):
        super().__init__(parent)
        self._items = items  # [(text, data)]
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setStyleSheet(
            f"""
            ModelPickerPopup {{
                background-color: {Theme.BG_SECONDARY};
                border: 1px solid #444; border-radius: 8px;
            }}
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  모델 검색…")
        self.search.setStyleSheet(
            f"""
            QLineEdit {{
                background-color: {Theme.BG_INPUT}; color: {Theme.TEXT_PRIMARY};
                border: 1px solid #444; border-radius: 6px;
                padding: 6px 10px; font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {Theme.ACCENT_PRIMARY}; }}
            """
        )
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 키 입력은 search 가 받는다
        self.list.setStyleSheet(
            f"""
            QListWidget {{
                background-color: transparent; border: none; outline: none;
            }}
            QListWidget::item {{ border-radius: 6px; margin: 1px 0; }}
            QListWidget::item:selected {{ background-color: {Theme.ACCENT_PRIMARY}; }}
            QListWidget::item:hover {{ background-color: {Theme.BG_HOVER}; }}
            """
        )
        self.list.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.list)

        meta = get_all_model_meta()
        for idx, (text, data) in enumerate(self._items):
            m = meta.get(data, {})
            provider = m.get("provider", "")
            modality = m.get("modality", "")
            item = QListWidgetItem(self.list)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            # 검색 매칭용 문자열(소문자) 저장
            item.setData(Qt.ItemDataRole.UserRole + 1,
                         f"{text} {provider} {modality} {data}".lower())
            row = _RowWidget(text, provider, modality)
            item.setSizeHint(QSize(row.sizeHint().width(), 30))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)

        self.setFixedWidth(320)
        self._update_height()

        if 0 <= current_index < self.list.count():
            self.list.setCurrentRow(current_index)
        elif self.list.count():
            self.list.setCurrentRow(0)

        self.search.textChanged.connect(self._on_filter)
        self.search.installEventFilter(self)

    def _update_height(self):
        visible = sum(1 for i in range(self.list.count()) if not self.list.item(i).isHidden())
        visible = max(1, min(visible, 12))
        self.list.setFixedHeight(visible * 32 + 4)
        self.adjustSize()

    def _on_filter(self, text: str):
        q = text.strip().lower()
        terms = q.split()
        first_visible = -1
        for i in range(self.list.count()):
            item = self.list.item(i)
            hay = item.data(Qt.ItemDataRole.UserRole + 1) or ""
            match = all(term in hay for term in terms)
            item.setHidden(not match)
            if match and first_visible < 0:
                first_visible = i
        if first_visible >= 0:
            self.list.setCurrentRow(first_visible)
        self._update_height()

    def _move_selection(self, delta: int):
        count = self.list.count()
        if not count:
            return
        cur = self.list.currentRow()
        i = cur
        for _ in range(count):
            i = (i + delta) % count
            if not self.list.item(i).isHidden():
                self.list.setCurrentRow(i)
                return

    def _commit(self):
        item = self.list.currentItem()
        if item and not item.isHidden():
            self.selected.emit(item.data(Qt.ItemDataRole.UserRole))
        self.close()

    def _on_item_clicked(self, item):
        self.selected.emit(item.data(Qt.ItemDataRole.UserRole))
        self.close()

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move_selection(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._commit()
                return True
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, event)

    def show_at(self, global_pos):
        self.adjustSize()
        x, y = global_pos.x(), global_pos.y()
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            w, h = self.width(), self.height()
            if x + w > avail.right():
                x = avail.right() - w
            if y + h > avail.bottom():
                y = max(avail.top(), global_pos.y() - h)  # 화면 아래면 위로 펼침
            x = max(avail.left(), x)
            y = max(avail.top(), y)
        self.move(x, y)
        self.show()
        self.search.setFocus()


class ModelSelectorButton(QPushButton):
    """QComboBox 드롭인 — 헤더엔 현재 모델만, 클릭하면 명령 팔레트 팝업."""

    currentIndexChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []          # [(text, data)]
        self._current_index = -1
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_style()
        self.clicked.connect(self._open_popup)

    # ── QComboBox 호환 API ──
    def addItem(self, text, data=None):
        self._items.append((text, data))
        if self._current_index < 0:
            self._current_index = 0
            self._refresh_label()

    def count(self):
        return len(self._items)

    def findData(self, data):
        for i, (_t, d) in enumerate(self._items):
            if d == data:
                return i
        return -1

    def setCurrentIndex(self, index):
        if index == self._current_index:
            return
        if 0 <= index < len(self._items):
            self._current_index = index
            self._refresh_label()
            self.currentIndexChanged.emit(index)

    def currentIndex(self):
        return self._current_index

    def currentData(self):
        if 0 <= self._current_index < len(self._items):
            return self._items[self._current_index][1]
        return None

    def currentText(self):
        if 0 <= self._current_index < len(self._items):
            return self._items[self._current_index][0]
        return ""

    def setMaxVisibleItems(self, _n):
        pass  # 팝업이 알아서 높이 조절 — 호환용 no-op

    # ── 내부 ──
    def _apply_style(self):
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: #333; color: {Theme.TEXT_PRIMARY};
                border: 1px solid #444; border-radius: 6px;
                padding: 6px 12px; min-width: 150px; font-size: 12px;
                text-align: left;
            }}
            QPushButton:hover {{ border-color: {Theme.ACCENT_PRIMARY}; background-color: {Theme.BG_HOVER}; }}
            QPushButton:disabled {{ color: {Theme.TEXT_DISABLED}; }}
            """
        )

    def _refresh_label(self):
        text = self.currentText()
        data = self.currentData()
        color = _DEFAULT_PROVIDER_COLOR
        if data:
            try:
                meta = get_all_model_meta().get(data, {})
                color = _provider_color(meta.get("provider", ""))
            except Exception:
                pass
        # QPushButton 은 리치텍스트 불가 → provider 색은 좌측 보더로, 텍스트는 모델명 + ▾
        self.setText(f"{text}    ▾")
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: #333; color: {Theme.TEXT_PRIMARY};
                border: 1px solid #444; border-left: 3px solid {color};
                border-radius: 6px; padding: 6px 12px; min-width: 150px;
                font-size: 12px; text-align: left;
            }}
            QPushButton:hover {{ border-color: {Theme.ACCENT_PRIMARY}; border-left-color: {color}; background-color: {Theme.BG_HOVER}; }}
            QPushButton:disabled {{ color: {Theme.TEXT_DISABLED}; }}
            """
        )

    def _open_popup(self):
        if not self._items:
            return
        popup = ModelPickerPopup(self._items, self._current_index, self)
        popup.selected.connect(self.setCurrentIndex)
        # 노드는 QGraphicsProxyWidget 안에 있어 self.mapToGlobal 이 어긋날 수 있다.
        # 방금 클릭이 버튼 위에서 일어났으므로 커서 위치를 앵커로 쓰면 가장 견고하다.
        from PyQt6.QtGui import QCursor
        anchor = QCursor.pos()
        popup.show_at(anchor)
