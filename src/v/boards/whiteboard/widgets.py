"""
공통 위젯 및 다이얼로그
- StreamWorker: AI 스트리밍 응답 워커
- ApiKeyDialog: API 키 입력
- InputDialog: 메시지 입력
- DraggableHeader: 노드 드래그 헤더
- ResizeHandle: 노드 리사이즈 핸들
"""
import os
import random
import string
import subprocess
import tempfile

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTextEdit, QFileDialog, QFrame, QApplication, QWidget
)
from PyQt6.QtCore import Qt, QPointF, QThread, pyqtSignal, QUrl, QEvent
from PyQt6.QtGui import QPainter, QPen, QColor, QCursor, QImage

from v.theme import Theme
from q import t
from v.provider import GeminiProvider, ChatMessage
from v.settings import save_api_key, get_api_keys, save_api_keys


class StreamWorker(QThread):
    """AI 스트리밍 응답을 별도 스레드에서 처리"""
    chunk_received = pyqtSignal(str)       # 텍스트 청크
    finished_signal = pyqtSignal(str)      # 전체 응답 텍스트
    error_signal = pyqtSignal(str)         # 에러
    image_received = pyqtSignal(object)    # 이미지 응답 dict
    tokens_received = pyqtSignal(int, int) # (prompt_tokens, candidates_tokens)
    signatures_received = pyqtSignal(list) # thought_signatures

    def __init__(self, provider, model, messages, **options):
        super().__init__()
        self.provider = provider
        self.model = model
        self.messages = messages
        self.options = options
        self._cancel_requested = False
        self._node_id = None

    def cancel(self):
        self._cancel_requested = True

    def run(self):
        try:
            result = self.provider.chat(
                self.model, self.messages, stream=True, **self.options
            )

            if self._cancel_requested:
                return

            # 이미지 모델은 dict 반환
            if isinstance(result, dict):
                pt = result.get("prompt_tokens", 0)
                ct = result.get("candidates_tokens", 0)
                if pt or ct:
                    self.tokens_received.emit(pt, ct)
                self.image_received.emit(result)
                return

            # 스트리밍 텍스트 (쓰로틀링 + O(n) join)
            import time
            chunks = []
            last_emit = 0.0
            for chunk in result:
                if self._cancel_requested:
                    return
                if isinstance(chunk, dict) and chunk.get("__usage__"):
                    self.tokens_received.emit(
                        chunk.get("prompt_tokens", 0),
                        chunk.get("candidates_tokens", 0))
                elif isinstance(chunk, dict) and "__thought_signatures__" in chunk:
                    self.signatures_received.emit(chunk["__thought_signatures__"])
                elif isinstance(chunk, dict) and "__error__" in chunk:  # 스트리밍 오류 감지
                    self.error_signal.emit(chunk["__error__"])
                    return
                elif isinstance(chunk, str):
                    chunks.append(chunk)
                    now = time.monotonic()
                    if now - last_emit >= 0.05:  # 50ms 쓰로틀
                        self.chunk_received.emit("".join(chunks))
                        last_emit = now

            if self._cancel_requested:
                return
            full_text = "".join(chunks)
            self.finished_signal.emit(full_text)

        except Exception as e:
            if not self._cancel_requested:
                self.error_signal.emit(str(e))


class ApiKeyDialog(QDialog):
    """API 키 입력 다이얼로그"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gemini API Key")
        self.setMinimumWidth(450)
        self.setModal(True)
        self.setStyleSheet("QDialog { background-color: #1e1e1e; }")

        layout = QVBoxLayout(self)

        label = QLabel(t("dialog.api_key_prompt"))
        label.setStyleSheet("color: #ddd; font-size: 13px; margin-bottom: 8px;")
        layout.addWidget(label)

        hint = QLabel(t("dialog.api_key_hint"))
        hint.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 12px;")
        layout.addWidget(hint)

        from PyQt6.QtWidgets import QLineEdit
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("AIza...")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                color: #ddd;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
            }
        """)
        layout.addWidget(self.key_input)

        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton(t("button.cancel"))
        btn_cancel.setStyleSheet("padding: 8px 20px;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_layout.addStretch()

        btn_save = QPushButton(t("button.save"))
        btn_save.setStyleSheet("padding: 8px 20px; background-color: #0d6efd; font-weight: bold;")
        btn_save.clicked.connect(self._save)
        btn_layout.addWidget(btn_save)

        layout.addLayout(btn_layout)

    def _save(self):
        key = self.key_input.text().strip()
        if key:
            existing = get_api_keys()
            if key not in existing:
                existing.append(key)
                save_api_keys(existing)
            else:
                save_api_key(key)
            self.accept()


class PasteableTextEdit(QTextEdit):
    """이미지/파일 붙여넣기를 지원하는 텍스트 에디터"""
    files_pasted = pyqtSignal(list)  # 붙여넣은 파일 경로 리스트

    def canInsertFromMimeData(self, source):
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        # 파일 URL (탐색기에서 복사한 파일 — 모든 파일 허용)
        if source.hasUrls():
            paths = []
            for url in source.urls():
                if url.isLocalFile():
                    fpath = url.toLocalFile()
                    if os.path.isfile(fpath):
                        paths.append(fpath)
            if paths:
                self.files_pasted.emit(paths)
                return

        # 클립보드 이미지 (스크린샷 등)
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                rand_name = ''.join(random.choices(string.ascii_lowercase, k=4))
                tmp_path = os.path.join(tempfile.gettempdir(), f"{rand_name}.png")
                image.save(tmp_path, "PNG")
                self.files_pasted.emit([tmp_path])
                return

        super().insertFromMimeData(source)


class InputDialog(QDialog):
    """메시지 입력 다이얼로그"""

    def __init__(self, parent, title=None, on_submit=None):
        if title is None:
            title = t("dialog.input_message_title")
        # 부모 없이 완전 독립 윈도우로 생성
        super().__init__(None)
        self.on_submit = on_submit
        self.attachments = []

        self.setWindowTitle(title)
        self.setMinimumSize(500, 400)
        # 완전 독립 윈도우 + 최상위
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        self.setStyleSheet("QDialog { background-color: #1e1e1e; }")

        layout = QVBoxLayout(self)

        self.textbox = PasteableTextEdit()
        self.textbox.setPlaceholderText(t("dialog.input_placeholder"))
        self.textbox.setStyleSheet("""
            QTextEdit {
                background-color: #2d2d2d;
                color: white;
                border: 1px solid #444;
                border-radius: 8px;
                padding: 10px;
                font-size: 14px;
            }
        """)
        self.textbox.files_pasted.connect(self._on_paste_files)
        layout.addWidget(self.textbox)

        toolbar = QHBoxLayout()

        btn_attach = QPushButton(t("button.attach"))
        btn_attach.setStyleSheet("padding: 6px 12px;")
        btn_attach.clicked.connect(self._attach_file)
        toolbar.addWidget(btn_attach)

        self.attach_label = QLabel("")
        self.attach_label.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(self.attach_label)

        toolbar.addStretch()

        btn_notepad = QPushButton(t("button.notepad"))
        btn_notepad.setStyleSheet("padding: 6px 12px; background-color: #444;")
        btn_notepad.clicked.connect(self._open_notepad)
        toolbar.addWidget(btn_notepad)

        layout.addLayout(toolbar)

        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton(t("button.cancel"))
        btn_cancel.setStyleSheet("padding: 8px 20px;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_layout.addStretch()

        btn_submit = QPushButton(t("button.submit"))
        btn_submit.setStyleSheet("padding: 8px 20px; background-color: #0d6efd; font-weight: bold;")
        btn_submit.clicked.connect(self._submit)
        btn_layout.addWidget(btn_submit)
        layout.addLayout(btn_layout)

    def showEvent(self, event):
        """다이얼로그가 표시될 때 최상위로 올리기"""
        super().showEvent(event)
        # 화면 중앙에 배치
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)
        self.raise_()
        self.activateWindow()
        # 텍스트박스에 포커스
        self.textbox.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._submit()
        elif event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def _attach_file(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, t("dialog.file_select_title"), "",
            t("dialog.file_select_filter")
        )
        if files:
            self.attachments.extend(files)
            self._update_attach_label()

    def _on_paste_files(self, paths):
        self.attachments.extend(paths)
        self._update_attach_label()

    def _update_attach_label(self):
        names = [os.path.basename(f) for f in self.attachments]
        self.attach_label.setText(f"📎 {', '.join(names)}")

    def _open_notepad(self):
        fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="chat_input_")
        os.close(fd)

        existing = self.textbox.toPlainText().strip()
        if existing:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(existing)

        try:
            subprocess.run(["notepad.exe", tmp_path], check=True)
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                self.textbox.setPlainText(content)
        except Exception as e:
            print(t("error.notepad_error", error=str(e)))
        finally:
            try:
                os.remove(tmp_path)
            except:
                pass

    def _submit(self):
        text = self.textbox.toPlainText().strip()
        if text or self.attachments:
            if self.on_submit:
                self.on_submit((text, self.attachments.copy()))
        self.accept()


class SystemPromptDialog(QDialog):
    """시스템 프롬프트 편집 다이얼로그 (원점 더블클릭)"""

    def __init__(self, text="", files=None, on_save=None):
        super().__init__(None)
        self.on_save = on_save
        self.files = list(files or [])

        self.setWindowTitle("시스템 프롬프트")
        self.setMinimumSize(550, 450)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setStyleSheet("QDialog { background-color: #1e1e1e; }")

        layout = QVBoxLayout(self)

        desc = QLabel("모든 대화에 적용되는 절대 프롬프트입니다.\n토큰 예산에 포함되지 않습니다.")
        desc.setStyleSheet("color: #888; font-size: 11px; margin-bottom: 8px;")
        layout.addWidget(desc)

        # 텍스트 입력
        self.textbox = PasteableTextEdit()
        self.textbox.setPlaceholderText("시스템 프롬프트를 입력하세요...")
        self.textbox.setStyleSheet("""
            QTextEdit {
                background-color: #2d2d2d;
                color: white;
                border: 1px solid #444;
                border-radius: 8px;
                padding: 10px;
                font-size: 14px;
            }
        """)
        self.textbox.files_pasted.connect(self._on_paste_files)
        if text:
            self.textbox.setPlainText(text)
        layout.addWidget(self.textbox)

        # 첨부 파일 헤더
        file_header = QHBoxLayout()
        self.file_count_label = QLabel(self._file_count_text())
        self.file_count_label.setStyleSheet("color: #aaa; font-size: 12px;")
        file_header.addWidget(self.file_count_label)
        file_header.addStretch()
        btn_add = QPushButton("파일 추가")
        btn_add.setStyleSheet("padding: 4px 10px; font-size: 11px;")
        btn_add.clicked.connect(self._add_files)
        file_header.addWidget(btn_add)
        layout.addLayout(file_header)

        # 파일 리스트
        self.file_list_widget = QFrame()
        self.file_list_widget.setStyleSheet("""
            QFrame {
                background-color: #252525;
                border: 1px solid #333;
                border-radius: 6px;
            }
        """)
        self.file_list_layout = QVBoxLayout(self.file_list_widget)
        self.file_list_layout.setContentsMargins(6, 6, 6, 6)
        self.file_list_layout.setSpacing(2)
        layout.addWidget(self.file_list_widget)

        # 기존 파일 표시
        for fpath in self.files:
            self._add_file_row(fpath)
        self._update_file_visibility()

        # 버튼
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("취소")
        btn_cancel.setStyleSheet("padding: 8px 20px;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addStretch()
        btn_save = QPushButton("저장")
        btn_save.setStyleSheet("padding: 8px 20px; background-color: #0d6efd; font-weight: bold;")
        btn_save.clicked.connect(self._save)
        btn_layout.addWidget(btn_save)
        layout.addLayout(btn_layout)

    def _file_count_text(self):
        n = len(self.files)
        return f"첨부 파일 ({n})" if n else "첨부 파일"

    def _update_file_visibility(self):
        self.file_count_label.setText(self._file_count_text())
        self.file_list_widget.setVisible(bool(self.files))

    def _add_file_row(self, fpath):
        row = QFrame()
        row.setStyleSheet("QFrame { background: transparent; border: none; }")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 2, 4, 2)

        name = QLabel(os.path.basename(fpath))
        name.setStyleSheet("color: #ccc; font-size: 12px; border: none;")
        name.setToolTip(fpath)
        row_layout.addWidget(name)
        row_layout.addStretch()

        btn_remove = QPushButton("✕")
        btn_remove.setFixedSize(22, 22)
        btn_remove.setStyleSheet("""
            QPushButton {
                background: #3a3a3a; color: #aaa;
                border: none; border-radius: 4px; font-size: 12px;
            }
            QPushButton:hover { background: #c0392b; color: white; }
        """)
        btn_remove.clicked.connect(lambda _, f=fpath, r=row: self._remove_file(f, r))
        row_layout.addWidget(btn_remove)

        self.file_list_layout.addWidget(row)

    def _remove_file(self, fpath, row_widget):
        if fpath in self.files:
            self.files.remove(fpath)
        row_widget.setParent(None)
        row_widget.deleteLater()
        self._update_file_visibility()

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "파일 선택", "",
            "모든 파일 (*);;이미지 (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;텍스트 (*.txt *.md *.json *.csv)"
        )
        for f in files:
            if f not in self.files:
                self.files.append(f)
                self._add_file_row(f)
        self._update_file_visibility()

    def _on_paste_files(self, paths):
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self._add_file_row(p)
        self._update_file_visibility()

    def _save(self):
        text = self.textbox.toPlainText().strip()
        if self.on_save:
            self.on_save(text, self.files.copy())
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)
        self.raise_()
        self.activateWindow()
        self.textbox.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


class DraggableHeader(QFrame):
    """드래그 가능한 헤더 (자식 위젯 위에서도 드래그 가능)"""

    DRAG_THRESHOLD = 5  # 드래그 시작 기준 (픽셀)

    def __init__(self, parent_widget):
        super().__init__()
        self.parent_widget = parent_widget
        self._dragging = False
        self._moved = False
        self._drag_start_scene = None    # 드래그 시작 시 씬 좌표
        self._drag_start_proxy_pos = None  # 드래그 시작 시 프록시 씬 좌표
        self._child_press_pos = None     # 자식 press 글로벌 좌표
        self._child_drag_source = None   # press가 발생한 자식 위젯
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def childEvent(self, event):
        """자식 위젯 추가 시 이벤트 필터 자동 설치"""
        super().childEvent(event)
        if event.type() == QEvent.Type.ChildAdded:
            child = event.child()
            if isinstance(child, QWidget):
                child.installEventFilter(self)

    def eventFilter(self, obj, event):
        """자식 위젯의 마우스 이벤트를 감시 → 드래그 제스처 감지"""
        if not self.parent_widget.proxy:
            return False

        # Press: 위치 기록, 자식에게 이벤트 전달
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._child_press_pos = QCursor.pos()
            self._child_drag_source = obj
            return False

        # Move: 임계값 초과 시 드래그 시작
        if event.type() == QEvent.Type.MouseMove and self._child_press_pos is not None:
            if not self._dragging:
                current = QCursor.pos()
                dx = abs(current.x() - self._child_press_pos.x())
                dy = abs(current.y() - self._child_press_pos.y())
                if dx + dy > self.DRAG_THRESHOLD:
                    self._dragging = True
                    self._moved = False
                    self._drag_start_scene = self._cursor_to_scene()
                    self._drag_start_proxy_pos = self.parent_widget.proxy.pos()
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    # 자식 위젯 상호작용 취소
                    if isinstance(self._child_drag_source, QPushButton):
                        self._child_drag_source.setDown(False)
                    if self._child_drag_source:
                        self._child_drag_source.clearFocus()
                    return True
            if self._dragging:
                self._moved = True
                current_scene = self._cursor_to_scene()
                scene_delta = current_scene - self._drag_start_scene
                self.parent_widget.proxy.setPos(self._drag_start_proxy_pos + scene_delta)
                return True

        # Release
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                moved = self._moved
                self._dragging = False
                self._drag_start_scene = None
                self._drag_start_proxy_pos = None
                self._moved = False
                self._child_press_pos = None
                self._child_drag_source = None
                self.setCursor(Qt.CursorShape.OpenHandCursor)
                if moved and hasattr(self.parent_widget, 'on_modified') and self.parent_widget.on_modified:
                    self.parent_widget.on_modified()
                return True
            self._child_press_pos = None
            self._child_drag_source = None
            return False

        return False

    def _cursor_to_scene(self):
        """QCursor 실제 스크린 좌표 → 씬 좌표 (줌/DPR 무관)"""
        proxy = self.parent_widget.proxy
        if proxy and proxy.scene() and proxy.scene().views():
            view = proxy.scene().views()[0]
            viewport_pos = view.mapFromGlobal(QCursor.pos())
            return view.mapToScene(viewport_pos)
        return QPointF()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.parent_widget.proxy:
            self._dragging = True
            self._moved = False
            self._drag_start_scene = self._cursor_to_scene()
            self._drag_start_proxy_pos = self.parent_widget.proxy.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self.parent_widget.proxy and self._drag_start_scene is not None:
            self._moved = True
            current_scene = self._cursor_to_scene()
            scene_delta = current_scene - self._drag_start_scene
            self.parent_widget.proxy.setPos(self._drag_start_proxy_pos + scene_delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            moved = self._moved
            self._dragging = False
            self._drag_start_scene = None
            self._drag_start_proxy_pos = None
            self._moved = False
            self._child_press_pos = None
            self._child_drag_source = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if moved and hasattr(self.parent_widget, 'on_modified') and self.parent_widget.on_modified:
                self.parent_widget.on_modified()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ResizeHandle(QFrame):
    """리사이즈 핸들"""

    def __init__(self, parent_widget):
        super().__init__(parent_widget)
        self.parent_widget = parent_widget
        self._resizing = False
        self._resize_start_scene = None
        self._size_start = None
        self._resized = False

        self.setFixedSize(16, 16)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event):
        painter = QPainter(self)
        pen = QPen(QColor("#555"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(10, 14, 14, 10)
        painter.drawLine(6, 14, 14, 6)

    def _cursor_to_scene(self):
        """QCursor 실제 스크린 좌표 → 씬 좌표 (줌/DPR 무관)"""
        proxy = self.parent_widget.proxy
        if proxy and proxy.scene() and proxy.scene().views():
            view = proxy.scene().views()[0]
            viewport_pos = view.mapFromGlobal(QCursor.pos())
            return view.mapToScene(viewport_pos)
        return QPointF()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._resizing = True
            self._resized = False
            self._resize_start_scene = self._cursor_to_scene()
            self._size_start = self.parent_widget.size()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._resizing and self._resize_start_scene is not None:
            self._resized = True
            # 사용자가 직접 크기를 정했다는 표식 — 자동 맞춤(_fit_height 류)이
            # 이후 이 크기를 되돌리지 않도록 노드에 남긴다.
            try:
                self.parent_widget._user_resized = True
            except Exception:
                pass
            current_scene = self._cursor_to_scene()
            scene_delta = current_scene - self._resize_start_scene

            min_size = self.parent_widget.minimumSize()
            new_w = max(min_size.width(), int(self._size_start.width() + scene_delta.x()))
            new_h = max(min_size.height(), int(self._size_start.height() + scene_delta.y()))
            self.parent_widget.resize(new_w, new_h)
            self._update_position()
            proxy = getattr(self.parent_widget, 'proxy', None)
            if proxy:
                proxy.prepareGeometryChange()
            if hasattr(self.parent_widget, 'reposition_ports'):
                self.parent_widget.reposition_ports()
            # 드래그 중에도 크기 변경을 동기화(전송은 prop_sync throttle 이 제어) → 상대 실시간 반영
            if hasattr(self.parent_widget, 'on_modified') and self.parent_widget.on_modified:
                self.parent_widget.on_modified()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            resized = self._resized
            self._resizing = False
            self._resize_start_scene = None
            self._size_start = None
            self._resized = False
            if resized and hasattr(self.parent_widget, 'on_modified') and self.parent_widget.on_modified:
                self.parent_widget.on_modified()
            event.accept()

    def _update_position(self):
        self.move(self.parent_widget.width() - 16, self.parent_widget.height() - 16)
