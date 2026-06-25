"""
체크리스트 위젯 — 그래프-네이티브 + AI 보조 + 실시간 협업 작업 노드.

- 체크박스 + 텍스트 + 담당자 + 마감일 항목 목록
- 진행률 바, "내 작업만" 필터, ✨ AI(분해/정리) 버튼
- STRING 출력 포트(마크다운) + ⚡ 완료 신호 포트(모두 체크 시 발화)
- apply_sync_data 로 협업 시 제자리(in-place) 동기화
- 드래그/리사이즈 지원
"""
import uuid
from datetime import date

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QCheckBox, QScrollArea, QFrame, QLabel, QMenu, QInputDialog,
)
from PyQt6.QtCore import Qt

from v.theme import Theme
from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle

try:
    from q import t as _q_t
except Exception:  # 로컬라이제이션 미가용 시 폴백
    _q_t = None


def t(key, default=None):
    """번역 조회 + 누락 시 기본값 폴백 (실제 q.t 는 positional default 미지원)."""
    if _q_t is not None:
        try:
            val = _q_t(key)
            if val and val != key:
                return val
        except Exception:
            pass
    return default if default is not None else key


# 담당자 칩 색상 팔레트 (username 해시로 안정적으로 매핑)
_ASSIGNEE_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#9b59b6", "#f39c12",
    "#1abc9c", "#e67e22", "#16a085", "#d35400", "#27ae60",
]


def _color_for(name: str) -> str:
    if not name:
        return Theme.TEXT_DISABLED
    return _ASSIGNEE_COLORS[sum(ord(c) for c in name) % len(_ASSIGNEE_COLORS)]


class ChecklistWidget(QWidget, BaseNode):
    """체크리스트 위젯"""

    TITLE_NAME = "Checklist"

    def __init__(self, title="", items=None, on_modified=None,
                 on_complete=None, get_current_user=None, on_ai=None,
                 get_users=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.init_base_node(node_id=None, on_modified=on_modified)

        # 콜백 (팩토리에서 주입)
        self.on_complete = on_complete            # (node_id) -> None : 모두 체크 시
        self.get_current_user = get_current_user  # () -> str : 현재 사용자명
        self.on_ai = on_ai                        # (kind, node_id) -> None : "breakdown"/"cleanup"
        self.get_users = get_users                # () -> list[str] : 접속자명 목록

        # 상태
        self._rows = []                # 각 항목: {id, frame, cb, line, assignee_btn, due_btn,
                                       #          assignee, due, checked_by}
        self._was_complete = False     # rising-edge 감지용
        self._suppress_emit = False    # 원격 sync 적용 중 에코 방지
        self.filter_mine = False       # 사용자별 뷰 설정 (동기화/저장 안 함)

        self.setMinimumSize(180, 140)
        self.resize(240, 200)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {Theme.BG_TERTIARY};
                border: 1px solid {Theme.BG_HOVER};
                border-radius: 6px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 헤더 (드래그) ──
        self.header = DraggableHeader(self)
        self.header.setFixedHeight(30)
        self.header.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_INPUT};
                border: none; border-bottom: 1px solid {Theme.GRID_LINE};
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }}
        """)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 2, 4, 2)
        header_layout.setSpacing(4)

        # 이름은 공용 편집형 이름표(node_title.NodeTitleItem)가 표시 — 헤더 제목칸 제거.
        header_layout.addStretch(1)

        # "내 작업만" 필터 토글
        self.filter_btn = QPushButton("◐")
        self.filter_btn.setCheckable(True)
        self.filter_btn.setFixedSize(20, 20)
        self.filter_btn.setToolTip(t("checklist.filter_mine", "내 작업만 보기"))
        self.filter_btn.setStyleSheet(self._tool_btn_style())
        self.filter_btn.toggled.connect(self._on_filter_toggled)
        header_layout.addWidget(self.filter_btn)

        # ✨ AI 버튼
        self.ai_btn = QPushButton("✨")
        self.ai_btn.setFixedSize(20, 20)
        self.ai_btn.setToolTip(t("checklist.ai", "AI 보조"))
        self.ai_btn.setStyleSheet(self._tool_btn_style())
        self.ai_btn.clicked.connect(self._show_ai_menu)
        header_layout.addWidget(self.ai_btn)

        layout.addWidget(self.header)

        # ── 진행률 바 ──
        prog_row = QFrame()
        prog_row.setStyleSheet("QFrame { background: transparent; border: none; }")
        prog_layout = QHBoxLayout(prog_row)
        prog_layout.setContentsMargins(8, 4, 8, 2)
        prog_layout.setSpacing(6)

        self.progress_track = QFrame()
        self.progress_track.setFixedHeight(4)
        self.progress_track.setStyleSheet(
            f"QFrame {{ background: {Theme.BG_INPUT}; border: none; border-radius: 2px; }}")
        self.progress_fill = QFrame(self.progress_track)
        self.progress_fill.setStyleSheet(
            f"QFrame {{ background: {Theme.ACCENT_SUCCESS}; border: none; border-radius: 2px; }}")
        self.progress_fill.setGeometry(0, 0, 0, 4)
        prog_layout.addWidget(self.progress_track, 1)

        self.progress_label = QLabel("0/0")
        self.progress_label.setStyleSheet(
            f"QLabel {{ background: transparent; border: none; color: {Theme.TEXT_SECONDARY}; font-size: 10px; }}")
        prog_layout.addWidget(self.progress_label)

        layout.addWidget(prog_row)

        # ── 항목 리스트 (스크롤) ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ width: 6px; background: transparent; }}
            QScrollBar::handle:vertical {{
                background: {Theme.BG_HOVER}; border-radius: 3px; min-height: 20px;
            }}
        """)

        self.list_container = QWidget()
        self.list_container.setStyleSheet("QWidget { background: transparent; border: none; }")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(6, 4, 6, 4)
        self.list_layout.setSpacing(2)
        self.list_layout.addStretch()

        scroll.setWidget(self.list_container)
        layout.addWidget(scroll)

        # ── 추가 버튼 ──
        btn_add = QPushButton("+")
        btn_add.setFixedHeight(24)
        btn_add.setStyleSheet(f"""
            QPushButton {{
                background: {Theme.BG_INPUT}; color: {Theme.TEXT_SECONDARY};
                border: none; border-top: 1px solid {Theme.GRID_LINE};
                border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
                font-size: 16px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {Theme.BG_SECONDARY}; color: {Theme.TEXT_PRIMARY}; }}
        """)
        btn_add.clicked.connect(lambda: self._add_item(focus=True))
        layout.addWidget(btn_add)

        # 리사이즈 핸들
        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)

        # 기존 항목 복원
        for item_data in (items or []):
            if isinstance(item_data, dict):
                self._add_item(
                    text=item_data.get("text", ""),
                    checked=item_data.get("checked", False),
                    item_id=item_data.get("id"),
                    assignee=item_data.get("assignee", ""),
                    due=item_data.get("due", ""),
                    checked_by=item_data.get("checked_by", ""),
                )
            else:
                self._add_item(text=str(item_data))
        self._recompute()

    # ──────────────────────────────────────────── 스타일 헬퍼

    def _tool_btn_style(self):
        return f"""
            QPushButton {{
                background: transparent; border: none; color: {Theme.TEXT_SECONDARY};
                font-size: 12px; border-radius: 4px;
            }}
            QPushButton:hover {{ background: {Theme.BG_HOVER}; color: {Theme.TEXT_PRIMARY}; }}
            QPushButton:checked {{ background: {Theme.ACCENT_PRIMARY}; color: white; }}
        """

    # ──────────────────────────────────────────── 항목 추가/삭제

    def _add_item(self, text="", checked=False, focus=False, item_id=None,
                  assignee="", due="", checked_by=""):
        item_id = item_id or uuid.uuid4().hex

        row = QFrame()
        row.setStyleSheet("QFrame { background: transparent; border: none; }")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 1, 2, 1)
        row_layout.setSpacing(4)

        rec = {
            "id": item_id, "frame": row,
            "assignee": assignee or "", "due": due or "", "checked_by": checked_by or "",
        }

        cb = QCheckBox()
        cb.setChecked(checked)
        cb.setStyleSheet(f"""
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border: 1px solid {Theme.TEXT_DISABLED}; border-radius: 3px;
                background: {Theme.BG_SECONDARY};
            }}
            QCheckBox::indicator:checked {{
                background: {Theme.ACCENT_SUCCESS}; border-color: {Theme.ACCENT_SUCCESS};
            }}
        """)
        cb.stateChanged.connect(lambda _s, r=rec: self._on_check_toggled(r))
        row_layout.addWidget(cb)
        rec["cb"] = cb

        line = QLineEdit(text)
        line.setStyleSheet(f"""
            QLineEdit {{
                background: transparent; border: none;
                color: {Theme.TEXT_PRIMARY}; font-size: 12px; padding: 2px;
            }}
        """)
        line.textChanged.connect(self._on_change)
        row_layout.addWidget(line, 1)
        rec["line"] = line

        # 담당자 칩
        assignee_btn = QPushButton()
        assignee_btn.setFixedSize(18, 18)
        assignee_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        assignee_btn.clicked.connect(lambda _c, r=rec: self._pick_assignee(r))
        row_layout.addWidget(assignee_btn)
        rec["assignee_btn"] = assignee_btn

        # 마감일
        due_btn = QPushButton()
        due_btn.setFixedHeight(18)
        due_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        due_btn.clicked.connect(lambda _c, r=rec: self._pick_due(r))
        row_layout.addWidget(due_btn)
        rec["due_btn"] = due_btn

        btn_remove = QPushButton("✕")
        btn_remove.setFixedSize(18, 18)
        btn_remove.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {Theme.TEXT_DISABLED};
                border: none; font-size: 11px;
            }}
            QPushButton:hover {{ color: {Theme.ACCENT_DANGER}; }}
        """)
        btn_remove.clicked.connect(lambda _c, r=rec: self._remove_item(r))
        row_layout.addWidget(btn_remove)

        # stretch 앞에 삽입
        idx = self.list_layout.count() - 1
        self.list_layout.insertWidget(idx, row)
        self._rows.append(rec)

        self._render_assignee(rec)
        self._render_due(rec)
        self._apply_filter_to_row(rec)

        if focus:
            line.setFocus()

        self._on_change()
        return rec

    def _remove_item(self, rec):
        try:
            self._rows.remove(rec)
        except ValueError:
            pass
        rec["frame"].setParent(None)
        rec["frame"].deleteLater()
        self._on_change()

    def _clear_rows(self):
        for rec in list(self._rows):
            rec["frame"].setParent(None)
            rec["frame"].deleteLater()
        self._rows = []

    def _row_by_id(self, item_id):
        for rec in self._rows:
            if rec["id"] == item_id:
                return rec
        return None

    # ──────────────────────────────────────────── 담당자 / 마감일

    def _render_assignee(self, rec):
        name = rec.get("assignee", "")
        btn = rec["assignee_btn"]
        if name:
            btn.setText(name[0].upper())
            color = _color_for(name)
            btn.setToolTip(name)
            btn.setStyleSheet(f"""
                QPushButton {{ background: {color}; color: white; border: none;
                    border-radius: 9px; font-size: 9px; font-weight: bold; }}
            """)
        else:
            btn.setText("+")
            btn.setToolTip(t("checklist.assign", "담당자 지정"))
            btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {Theme.TEXT_DISABLED};
                    border: 1px dashed {Theme.TEXT_DISABLED}; border-radius: 9px; font-size: 10px; }}
                QPushButton:hover {{ color: {Theme.TEXT_PRIMARY}; border-color: {Theme.TEXT_PRIMARY}; }}
            """)

    def _render_due(self, rec):
        due = rec.get("due", "")
        btn = rec["due_btn"]
        if due:
            overdue = self._is_overdue(due)
            color = Theme.ACCENT_DANGER if overdue else Theme.TEXT_SECONDARY
            btn.setText(due[5:] if len(due) >= 10 else due)  # MM-DD 만 표시
            btn.setToolTip(due)
            btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {color}; border: none;
                    font-size: 9px; padding: 0 2px; }}
                QPushButton:hover {{ color: {Theme.TEXT_PRIMARY}; }}
            """)
        else:
            btn.setText("📅")
            btn.setToolTip(t("checklist.due", "마감일"))
            btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {Theme.TEXT_DISABLED};
                    border: none; font-size: 9px; }}
                QPushButton:hover {{ color: {Theme.TEXT_PRIMARY}; }}
            """)

    @staticmethod
    def _is_overdue(due: str) -> bool:
        try:
            y, m, d = (int(x) for x in due.split("-"))
            return date(y, m, d) < date.today()
        except Exception:
            return False

    def _pick_assignee(self, rec):
        menu = QMenu(self)
        users = []
        try:
            users = [u for u in (self.get_users() if self.get_users else []) if u]
        except Exception:
            users = []
        me = self._me()
        if me and me not in users:
            users.insert(0, me)
        for u in users:
            act = menu.addAction(("✓ " if u == rec.get("assignee") else "") + u)
            act.triggered.connect(lambda _c=False, name=u, r=rec: self._set_assignee(r, name))
        menu.addSeparator()
        act_input = menu.addAction(t("checklist.assign_other", "직접 입력…"))
        act_input.triggered.connect(lambda: self._assign_input(rec))
        if rec.get("assignee"):
            act_clear = menu.addAction(t("checklist.assign_clear", "담당자 지우기"))
            act_clear.triggered.connect(lambda: self._set_assignee(rec, ""))
        menu.exec(rec["assignee_btn"].mapToGlobal(rec["assignee_btn"].rect().bottomLeft()))

    def _assign_input(self, rec):
        name, ok = QInputDialog.getText(
            self, t("checklist.assign", "담당자 지정"),
            t("checklist.assign", "담당자"), text=rec.get("assignee", ""))
        if ok:
            self._set_assignee(rec, name.strip())

    def _set_assignee(self, rec, name):
        rec["assignee"] = name or ""
        self._render_assignee(rec)
        self._apply_filter_to_row(rec)
        self._on_change()

    def _pick_due(self, rec):
        cur = rec.get("due", "")
        text, ok = QInputDialog.getText(
            self, t("checklist.due", "마감일"),
            t("checklist.due_hint", "마감일 (YYYY-MM-DD, 비우면 해제)"), text=cur)
        if ok:
            rec["due"] = text.strip()
            self._render_due(rec)
            self._on_change()

    # ──────────────────────────────────────────── 변경/완료/진행률

    def _me(self):
        try:
            return self.get_current_user() if self.get_current_user else ""
        except Exception:
            return ""

    def _on_check_toggled(self, rec):
        rec["checked_by"] = self._me() if rec["cb"].isChecked() else ""
        self._on_change()

    def _on_filter_toggled(self, on):
        self.filter_mine = bool(on)
        for rec in self._rows:
            self._apply_filter_to_row(rec)

    def _apply_filter_to_row(self, rec):
        if not self.filter_mine:
            rec["frame"].setVisible(True)
            return
        me = self._me()
        rec["frame"].setVisible((not me) or rec.get("assignee", "") == me)

    def _recompute(self):
        """진행률 UI + 출력 포트(마크다운) 갱신. (에코와 무관하게 항상 안전)"""
        total = len(self._rows)
        done = sum(1 for r in self._rows if r["cb"].isChecked())
        self.progress_label.setText(f"{done}/{total}")
        ratio = (done / total) if total else 0
        tw = self.progress_track.width()
        self.progress_fill.setGeometry(0, 0, int(tw * ratio), 4)

        # 출력 포트(마크다운)에 값 주입 — 다운스트림 노드가 읽음
        md = self.get_markdown()
        port = getattr(self, "output_port", None)
        if port is not None:
            port.port_value = md

    def _on_change(self):
        self._recompute()
        if self._suppress_emit:
            return
        if self.on_modified:
            self.on_modified()
        # 완료 신호 (rising edge: 빈 리스트는 완료 아님)
        total = len(self._rows)
        done = sum(1 for r in self._rows if r["cb"].isChecked())
        all_checked = total > 0 and done == total
        if all_checked and not self._was_complete:
            self._was_complete = True
            if self.on_complete:
                try:
                    self.on_complete(self.node_id)
                except Exception:
                    pass
        elif not all_checked:
            self._was_complete = False

    @property
    def text_content(self):
        """다운스트림 _collect_input_data 폴백 호환."""
        return self.get_markdown()

    # ──────────────────────────────────────────── 직렬화 / 출력

    def get_items(self):
        """모든 항목 데이터 반환 (filter_mine 은 제외)."""
        items = []
        for rec in self._rows:
            items.append({
                "id": rec["id"],
                "text": rec["line"].text(),
                "checked": rec["cb"].isChecked(),
                "assignee": rec.get("assignee", ""),
                "due": rec.get("due", ""),
                "checked_by": rec.get("checked_by", ""),
            })
        return items

    def get_markdown(self) -> str:
        lines = [f"# {getattr(self, '_display_name', None) or 'Checklist'}"]
        for rec in self._rows:
            box = "[x]" if rec["cb"].isChecked() else "[ ]"
            extra = []
            if rec.get("assignee"):
                extra.append(f"@{rec['assignee']}")
            if rec.get("due"):
                extra.append(f"~{rec['due']}")
            suffix = f"  ({', '.join(extra)})" if extra else ""
            lines.append(f"- {box} {rec['line'].text()}{suffix}")
        return "\n".join(lines)

    def get_data(self):
        return {
            "type": "checklist",
            "node_id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "items": self.get_items(),
        }

    # ──────────────────────────────────────────── 협업 동기화

    def apply_sync_data(self, data):
        """원격 편집을 제자리 반영(에코 방지, 포커스 칸 skip, id 기준 머지).
        이름은 node_rename op 로 별도 동기화됨(여기선 항목만)."""
        self._suppress_emit = True
        try:

            incoming = data.get("items", [])
            incoming = [it for it in incoming if isinstance(it, dict)]
            incoming_ids = {it.get("id") for it in incoming if it.get("id")}

            # 사라진 항목 제거
            for rec in list(self._rows):
                if rec["id"] not in incoming_ids:
                    self._rows.remove(rec)
                    rec["frame"].setParent(None)
                    rec["frame"].deleteLater()

            # 갱신 / 추가
            for it in incoming:
                iid = it.get("id")
                if not iid:
                    continue
                rec = self._row_by_id(iid)
                if rec is None:
                    self._add_item(
                        text=it.get("text", ""), checked=it.get("checked", False),
                        item_id=iid, assignee=it.get("assignee", ""),
                        due=it.get("due", ""), checked_by=it.get("checked_by", ""))
                    continue
                # 텍스트 (편집 중이면 skip)
                txt = it.get("text", "")
                if not rec["line"].hasFocus() and rec["line"].text() != txt:
                    rec["line"].blockSignals(True)
                    rec["line"].setText(txt)
                    rec["line"].blockSignals(False)
                # 체크
                chk = bool(it.get("checked", False))
                if rec["cb"].isChecked() != chk:
                    rec["cb"].blockSignals(True)
                    rec["cb"].setChecked(chk)
                    rec["cb"].blockSignals(False)
                rec["checked_by"] = it.get("checked_by", "")
                rec["assignee"] = it.get("assignee", "")
                rec["due"] = it.get("due", "")
                self._render_assignee(rec)
                self._render_due(rec)
                self._apply_filter_to_row(rec)

            # 크기
            w, h = data.get("width"), data.get("height")
            if w and h and (self.width() != w or self.height() != h):
                self.resize(int(w), int(h))
        finally:
            self._suppress_emit = False
        self._recompute()

    # ──────────────────────────────────────────── AI

    def _show_ai_menu(self):
        menu = QMenu(self)
        a1 = menu.addAction(t("checklist.ai_breakdown", "목표를 항목으로 분해"))
        a1.triggered.connect(lambda: self._trigger_ai("breakdown"))
        a2 = menu.addAction(t("checklist.ai_cleanup", "항목 정리·요약"))
        a2.triggered.connect(lambda: self._trigger_ai("cleanup"))
        menu.exec(self.ai_btn.mapToGlobal(self.ai_btn.rect().bottomLeft()))

    def _trigger_ai(self, kind):
        if self.on_ai:
            try:
                self.on_ai(kind, self.node_id)
            except Exception:
                pass

    def set_ai_busy(self, busy: bool):
        self.ai_btn.setEnabled(not busy)
        self.ai_btn.setText("…" if busy else "✨")

    def apply_ai_items(self, items, replace=False):
        """AI 결과 항목 적용. items: [{text}|str, ...]. replace=True 면 기존 교체."""
        self.set_ai_busy(False)
        texts = []
        for it in (items or []):
            if isinstance(it, dict):
                txt = (it.get("text") or "").strip()
            else:
                txt = str(it).strip()
            if txt:
                texts.append(txt)
        if not texts:
            return
        if replace:
            self._clear_rows()
        for txt in texts:
            self._add_item(text=txt)
        self._on_change()
