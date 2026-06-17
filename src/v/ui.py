"""
PyQt6 기반 메인 UI
- 플러그인 시스템으로 다양한 보드 타입 지원
"""
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QDialog, QListWidget,
    QListWidgetItem, QInputDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QIcon

import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from pathlib import Path

from q import t
from v.app import App
from v.theme import Theme


class _SaveWorker(QThread):
    """BoardManager.save()를 백그라운드 스레드에서 실행해 UI 블로킹을 방지한다."""

    done = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, name, board_data):
        super().__init__()
        self._name = name
        self._data = board_data

    def run(self):
        try:
            from v.board import BoardManager
            BoardManager.save(self._name, self._data)
            self.done.emit()
        except Exception as e:
            self.error.emit(str(e))


def _get_version() -> str:
    if getattr(sys, 'frozen', False):
        toml_path = Path(sys._MEIPASS) / "build.toml"
    else:
        toml_path = Path(__file__).resolve().parent.parent.parent / "build.toml"
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("app", {}).get("version", "")
    except Exception:
        return ""
from v.boards import discover_plugins, get_plugin, get_plugin_list, get_plugin_by_type
from v.boards.base import BoardPlugin
from v.settings import is_developer_mode, is_experimental_mode, get_recent_boards_count


class BoardTypeDialog(QDialog):
    """보드 타입 선택 다이얼로그"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_type = None

        self.setWindowTitle(t("dialog.new_board_title"))
        self.setMinimumSize(400, 300)
        self.setModal(True)

        layout = QVBoxLayout(self)

        label = QLabel(t("dialog.new_board_prompt"))
        label.setStyleSheet("font-size: 14px; margin-bottom: 10px;")
        layout.addWidget(label)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(f"""
            QListWidget {{
                background-color: {Theme.BG_SECONDARY};
                border: 1px solid #444;
                border-radius: 8px;
                padding: 8px;
            }}
            QListWidget::item {{
                padding: 12px;
                border-radius: 6px;
                margin: 4px;
            }}
            QListWidget::item:selected {{
                background-color: {Theme.ACCENT_PRIMARY};
            }}
            QListWidget::item:hover {{
                background-color: {Theme.BG_HOVER};
            }}
        """)

        plugins = get_plugin_list()
        for plugin in plugins:
            item = QListWidgetItem(f"{plugin['icon']} {plugin['name']}")
            item.setData(Qt.ItemDataRole.UserRole, plugin['id'])
            item.setToolTip(plugin['description'])
            self.list_widget.addItem(item)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

        self.list_widget.itemDoubleClicked.connect(self._select)
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton(t("button.cancel"))
        btn_cancel.setStyleSheet("padding: 10px 24px;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_layout.addStretch()

        btn_create = QPushButton(t("button.create"))
        btn_create.setStyleSheet(f"padding: 10px 24px; background-color: {Theme.ACCENT_PRIMARY}; font-weight: bold;")
        btn_create.clicked.connect(self._select)
        btn_layout.addWidget(btn_create)

        layout.addLayout(btn_layout)

    def _select(self):
        item = self.list_widget.currentItem()
        if item:
            self.selected_type = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


class MainWindow(QMainWindow):
    """메인 윈도우"""

    def __init__(self, app: App):
        super().__init__()

        # 로거 초기화 (최우선)
        from v.logger import setup_logger
        try:
            setup_logger()
        except Exception:
            pass  # 로거 초기화 실패 시 조용히 무시

        # API 키 마이그레이션 (평문 → 암호화)
        from v.settings import migrate_plaintext_api_key
        try:
            migrate_plaintext_api_key()
        except Exception:
            pass  # 마이그레이션 실패 시 조용히 무시

        # 오래된 임시 파일 정리 (백그라운드)
        from v.temp_file_manager import TempFileManager
        try:
            TempFileManager.cleanup_old_files(days=7)
        except Exception:
            pass  # 정리 실패 시 조용히 무시

        # 모델 플러그인 로드
        from v.model_plugin import PluginRegistry
        try:
            PluginRegistry.instance().load_all()
        except Exception:
            pass  # 플러그인 로드 실패 시 조용히 무시

        self.app = app
        self.current_plugin: BoardPlugin | None = None
        self._current_filepath: str | None = None
        self._modified = False
        self._save_worker: _SaveWorker | None = None
        self.dev_window = None
        self.data_viewer_window = None

        self.setWindowTitle(t("app.title"))
        self.setGeometry(100, 100, 1200, 800)
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {Theme.BG_PRIMARY}; }}
            QMenuBar {{
                background-color: {Theme.BG_SECONDARY};
                color: {Theme.TEXT_PRIMARY};
                padding: 4px;
                font-size: 12px;
            }}
            QMenuBar::item {{
                padding: 6px 12px;
                border-radius: 4px;
            }}
            QMenuBar::item:selected {{
                background-color: #3d3d3d;
            }}
            QMenu {{
                background-color: {Theme.BG_SECONDARY};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid #444;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 8px 30px;
            }}
            QMenu::item:selected {{
                background-color: {Theme.ACCENT_PRIMARY};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: #444;
                margin: 4px 10px;
            }}
        """)

        # 메뉴바 설정
        self._setup_menubar()

        # 초기 화면 (보드 선택 전)
        self._show_welcome()

        # 버전 첫 실행 시 기본 보드 로드
        self._check_default_board()

        # 개발자 모드이면 개발자 창 열기
        if is_developer_mode():
            self._open_dev_window()

        # qonvo presence 하트비트(merri 프로필 있으면)
        self._start_presence_reporter()

    def _start_presence_reporter(self):
        """앱 켜진 동안 내 qonvo 상태를 허브에 하트비트한다(merri 프로필 필요)."""
        try:
            from v.boards.whiteboard import profile as _profile
            if not _profile.get_profile():
                return
            from v.boards.whiteboard.qonvo_presence import PresenceReporter
            self._presence = PresenceReporter(self._presence_state, self)
            self._presence.start()
        except Exception:
            pass

    def _presence_state(self):
        """현재 상태: 협업 보드 안이면 (working, 보드명), 아니면 (online, "")."""
        if getattr(self, "_server_board", "") and getattr(self, "_server_client", None):
            return ("working", self._server_board or "")
        return ("online", "")

    def _setup_menubar(self):
        """메뉴바 설정"""
        menubar = self.menuBar()

        # 파일 메뉴
        file_menu = menubar.addMenu(t("menu.file"))

        self.action_home = file_menu.addAction("메인 화면으로")
        self.action_home.setShortcut("Ctrl+Shift+H")
        self.action_home.triggered.connect(self._go_home)
        file_menu.addSeparator()

        action_new = file_menu.addAction(t("menu.new_board"))
        action_new.setShortcut("Ctrl+N")
        action_new.triggered.connect(self._new_board)

        # 라운드 테이블 (실험적 기능)
        self.action_round_table = file_menu.addAction(t("menu.new_round_table"))
        self.action_round_table.setShortcut("Ctrl+Shift+R")
        self.action_round_table.triggered.connect(self._open_round_table)
        self.action_round_table.setVisible(is_experimental_mode())

        file_menu.addSeparator()

        self.action_save = file_menu.addAction(t("menu.save"))
        self.action_save.setShortcut("Ctrl+S")
        self.action_save.triggered.connect(self._save_board)

        self.action_save_as = file_menu.addAction(t("menu.save_as"))
        self.action_save_as.setShortcut("Ctrl+Shift+S")
        self.action_save_as.triggered.connect(self._save_board_as)

        action_load = file_menu.addAction(t("menu.load"))
        action_load.setShortcut("Ctrl+O")
        action_load.triggered.connect(self._load_board)

        file_menu.addSeparator()

        self.recent_menu = file_menu.addMenu(t("menu.recent_boards"))
        self._refresh_recent_boards()

        file_menu.addSeparator()

        action_open_folder = file_menu.addAction(t("menu.open_boards_folder"))
        action_open_folder.triggered.connect(self._open_boards_folder)

        file_menu.addSeparator()

        self.action_export_folder = file_menu.addAction(t("menu.export_folder"))
        self.action_export_folder.triggered.connect(self._export_to_folder)
        self.action_export_folder.setEnabled(False)

        action_import_folder = file_menu.addAction(t("menu.import_folder"))
        action_import_folder.triggered.connect(self._import_from_folder)

        file_menu.addSeparator()

        self.action_connect = file_menu.addAction(t("menu.connect_server"))
        self.action_connect.setShortcut("Ctrl+Shift+C")
        self.action_connect.triggered.connect(self._connect_to_server)

        # 인앱 호스팅("딸깍 초대") — 지금 보드를 내 PC에서 서빙
        self.action_host = file_menu.addAction("보드 호스팅 (초대)")
        self.action_host.triggered.connect(self._host_current_board)
        self.action_invite_join = file_menu.addAction("초대 링크로 접속…")
        self.action_invite_join.triggered.connect(self._join_by_invite)

        self.action_disconnect = file_menu.addAction(t("menu.disconnect_server"))
        self.action_disconnect.triggered.connect(self._disconnect_from_server)
        self.action_disconnect.setVisible(False)

        file_menu.addSeparator()

        action_exit = file_menu.addAction(t("menu.exit"))
        action_exit.setShortcut("Alt+F4")
        action_exit.triggered.connect(self.close)

        # 보기 메뉴
        view_menu = menubar.addMenu(t("menu.view"))

        self.action_reset_view = view_menu.addAction(t("menu.reset_view"))
        self.action_reset_view.setShortcut("Home")
        self.action_reset_view.triggered.connect(self._center_view)
        self.action_reset_view.setEnabled(False)

        self.action_reset_zoom = view_menu.addAction(t("menu.reset_zoom"))
        self.action_reset_zoom.setShortcut("Ctrl+0")
        self.action_reset_zoom.triggered.connect(self._reset_zoom)
        self.action_reset_zoom.setEnabled(False)

        self.action_search_history = view_menu.addAction(t("menu.search_history"))
        self.action_search_history.setShortcut("Ctrl+Shift+H")
        self.action_search_history.triggered.connect(self._open_history_search)
        self.action_search_history.setEnabled(False)

        # 노드 메뉴 (보드 로드 후 활성화)
        self.node_menu = menubar.addMenu(t("menu.node"))

        self.action_add_node = self.node_menu.addAction(t("menu.add_node"))
        self.action_add_node.setShortcut("Ctrl+Shift+N")
        self.action_add_node.triggered.connect(self._add_node)
        self.action_add_node.setEnabled(False)

        # 설정 메뉴
        settings_menu = menubar.addMenu(t("menu.settings"))

        action_settings = settings_menu.addAction(t("menu.preferences"))
        action_settings.setShortcut("Ctrl+,")
        action_settings.triggered.connect(self._open_settings)

        settings_menu.addSeparator()

        action_data_viewer = settings_menu.addAction(t("data_viewer.menu_item"))
        action_data_viewer.setShortcut("Ctrl+Shift+D")
        action_data_viewer.triggered.connect(self._toggle_data_viewer)

        settings_menu.addSeparator()

        self.action_dev_window = settings_menu.addAction(t("menu.dev_window"))
        self.action_dev_window.setShortcut("F12")
        self.action_dev_window.triggered.connect(self._toggle_dev_window)
        self.action_dev_window.setVisible(is_developer_mode())

        # People — 상단 우측 코너 버튼(동료 연락처/DM/초대). 메뉴 분류와 별개로 항상 보임
        from PyQt6.QtWidgets import QPushButton
        people_btn = QPushButton("👥 People")
        people_btn.setStyleSheet(
            "QPushButton{color:#ddd;background:transparent;border:none;padding:4px 12px;font-weight:bold;}"
            "QPushButton:hover{color:#fff;background:#0d6efd;border-radius:6px;}")
        people_btn.setShortcut("Ctrl+Shift+P")
        people_btn.clicked.connect(self._toggle_people_panel)
        menubar.setCornerWidget(people_btn, Qt.Corner.TopRightCorner)

    def _open_settings(self):
        """설정 다이얼로그 열기"""
        from v.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self)
        dialog.developer_mode_changed.connect(self._on_developer_mode_changed)
        dialog.experimental_mode_changed.connect(self._on_experimental_mode_changed)
        dialog.api_keys_changed.connect(self._on_api_keys_changed)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # 플러그인 레지스트리 재로드 (활성화 변경 반영)
            from v.model_plugin import PluginRegistry
            try:
                PluginRegistry.instance().load_all()
            except Exception:
                pass
            # provider 재생성 (플러그인/키 변경 반영)
            if hasattr(self, 'current_plugin') and self.current_plugin:
                if hasattr(self.current_plugin, '_invalidate_provider'):
                    self.current_plugin._invalidate_provider()
            # 최근 보드 메뉴 갱신 (개수 변경 반영)
            self._refresh_recent_boards()

    def _on_api_keys_changed(self):
        """API 키 변경 → provider 재생성"""
        if hasattr(self, 'current_plugin') and self.current_plugin:
            if hasattr(self.current_plugin, '_invalidate_provider'):
                self.current_plugin._invalidate_provider()

    def _on_developer_mode_changed(self, enabled: bool):
        """개발자 모드 토글"""
        self.action_dev_window.setVisible(enabled)
        if enabled:
            self._open_dev_window()
        elif self.dev_window:
            self.dev_window.destroy_streams()
            self.dev_window.close()
            self.dev_window = None

    def _open_dev_window(self):
        """개발자 창 열기"""
        from v.dev_window import DevWindow

        if not self.dev_window:
            self.dev_window = DevWindow(main_window=self)
        self.dev_window.show()
        self.dev_window.raise_()
        self.dev_window.activateWindow()

    def _toggle_dev_window(self):
        """개발자 창 토글"""
        if self.dev_window and self.dev_window.isVisible():
            self.dev_window.hide()
        else:
            self._open_dev_window()

    def _open_data_viewer(self):
        """데이터 뷰어 창 열기"""
        from v.data_viewer import DataViewerWindow

        if not self.data_viewer_window:
            self.data_viewer_window = DataViewerWindow(main_window=self)
        self.data_viewer_window.show()
        self.data_viewer_window.raise_()
        self.data_viewer_window.activateWindow()

    def _toggle_data_viewer(self):
        """데이터 뷰어 창 토글"""
        if self.data_viewer_window and self.data_viewer_window.isVisible():
            self.data_viewer_window.hide()
        else:
            self._open_data_viewer()

    def _on_experimental_mode_changed(self, enabled: bool):
        """실험적 기능 모드 토글"""
        self.action_round_table.setVisible(enabled)

    def _open_round_table(self):
        """라운드 테이블 열기"""
        from v.round_table import RoundTableView

        self.round_table_view = RoundTableView()
        self.round_table_view.showMaximized()

    def _show_welcome(self):
        from v.board import BoardManager
        import time, datetime

        welcome = QWidget()
        welcome.setStyleSheet(f"background-color: {Theme.BG_PRIMARY};")

        root = QHBoxLayout(welcome)
        root.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left.setFixedWidth(340)
        left.setStyleSheet(f"background-color: {Theme.BG_SECONDARY};")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(40, 60, 40, 40)

        title = QLabel(t("app.title"))
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: #eee;")
        left_layout.addWidget(title)

        ver = _get_version()
        if ver:
            ver_label = QLabel(ver)
            ver_label.setStyleSheet(f"font-size: 12px; color: {Theme.TEXT_TERTIARY}; margin-bottom: 20px;")
            left_layout.addWidget(ver_label)

        left_layout.addSpacing(20)

        start_label = QLabel(t("welcome.start"))
        start_label.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {Theme.TEXT_TERTIARY}; margin-bottom: 8px;")
        left_layout.addWidget(start_label)

        action_style = f"""
            QPushButton {{
                background-color: transparent;
                color: {Theme.ACCENT_PRIMARY};
                border: none;
                padding: 10px 0px;
                font-size: 14px;
                text-align: left;
            }}
            QPushButton:hover {{
                color: #5a9cff;
            }}
        """

        btn_new = QPushButton(f"  +   {t('welcome.new_board')}")
        btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_new.setStyleSheet(action_style)
        btn_new.clicked.connect(self._new_board)
        left_layout.addWidget(btn_new)

        btn_open = QPushButton(f"      {t('welcome.open_board')}")
        btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open.setStyleSheet(action_style)
        btn_open.clicked.connect(self._load_board)
        left_layout.addWidget(btn_open)

        btn_server = QPushButton("  \U0001F5A7   서버 접속")
        btn_server.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_server.setStyleSheet(action_style)
        btn_server.clicked.connect(self._show_server_browser)
        left_layout.addWidget(btn_server)

        left_layout.addStretch()
        root.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(40, 60, 40, 40)

        recent_label = QLabel(t("welcome.recent_boards"))
        recent_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #eee; margin-bottom: 16px;")
        right_layout.addWidget(recent_label)

        boards = BoardManager.list_boards_with_mtime()
        now = time.time()
        today = datetime.date.today()

        if boards:
            for name, mtime in boards[:get_recent_boards_count()]:
                dt = datetime.datetime.fromtimestamp(mtime)
                d = dt.date()
                if d == today:
                    when = t("welcome.today")
                elif d == today - datetime.timedelta(days=1):
                    when = t("welcome.yesterday")
                else:
                    days = (today - d).days
                    when = t("welcome.days_ago").format(days)
                time_str = dt.strftime("%H:%M")

                row = QWidget()
                row.setCursor(Qt.CursorShape.PointingHandCursor)
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(12, 10, 12, 10)

                name_label = QLabel(name)
                name_label.setStyleSheet("font-size: 14px; color: #ddd;")
                row_layout.addWidget(name_label)

                row_layout.addStretch()

                date_label = QLabel(f"{when}  {time_str}")
                date_label.setStyleSheet(f"font-size: 12px; color: {Theme.TEXT_TERTIARY};")
                row_layout.addWidget(date_label)

                row.setStyleSheet(f"""
                    QWidget {{
                        border-radius: 6px;
                    }}
                    QWidget:hover {{
                        background-color: {Theme.BG_HOVER};
                    }}
                """)
                row.mousePressEvent = lambda e, n=name: self._load_board_by_name(n)
                right_layout.addWidget(row)
        else:
            empty = QLabel(t("welcome.instructions"))
            empty.setStyleSheet(f"font-size: 13px; color: {Theme.TEXT_TERTIARY};")
            right_layout.addWidget(empty)

        right_layout.addStretch()
        root.addWidget(right, 1)

        self.setCentralWidget(welcome)

    def _check_default_board(self):
        """해당 버전 첫 실행이면 default_qonvo URL에서 보드 다운로드"""
        from v.board import _get_app_version, _get_default_qonvo_url, BoardManager
        from v.settings import get_setting, set_setting

        current = _get_app_version()
        if not current:
            return

        last = get_setting("last_version", "")
        if last == current:
            return

        # 버전 기록 업데이트
        set_setting("last_version", current)

        url = _get_default_qonvo_url()
        if not url:
            return

        try:
            filepath = BoardManager.fetch_default(url)
            self._load_board_file(filepath)
        except Exception:
            pass  # 실패 시 무시 (환영 화면 유지)

    def _new_board(self):
        """새 보드 생성"""
        dialog = BoardTypeDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_type:
            plugin_class = get_plugin(dialog.selected_type)
            if plugin_class:
                self._load_plugin(plugin_class)

    def _load_plugin(self, plugin_class):
        """플러그인 로드 및 뷰 설정"""
        self.app.clear()
        self.current_plugin = plugin_class(self.app)
        self._current_filepath = None
        self._modified = False

        view = self.current_plugin.create_view()
        self.setCentralWidget(view)
        self.current_plugin.on_modified = self.mark_modified

        self.action_reset_view.setEnabled(True)
        self.action_reset_zoom.setEnabled(True)
        self.action_add_node.setEnabled(True)
        self.action_search_history.setEnabled(True)
        self.action_export_folder.setEnabled(True)
        self._update_title()

    def _add_node(self):
        if not self.current_plugin:
            QMessageBox.information(
                self,
                t("info.no_board_title"),
                t("info.no_board_message")
            )
            return

        if hasattr(self.current_plugin, 'add_node'):
            self.current_plugin.add_node()

    def _open_history_search(self):
        if self.current_plugin and hasattr(self.current_plugin, 'open_history_search'):
            self.current_plugin.open_history_search()

    def _center_view(self):
        if not self.current_plugin:
            QMessageBox.information(
                self,
                t("info.no_board_title"),
                t("info.no_board_message")
            )
            return

        if hasattr(self.current_plugin, 'center_on_origin'):
            self.current_plugin.center_on_origin()

    def _reset_zoom(self):
        if not self.current_plugin:
            QMessageBox.information(
                self,
                t("info.no_board_title"),
                t("info.no_board_message")
            )
            return

        if hasattr(self.current_plugin, 'reset_zoom'):
            self.current_plugin.reset_zoom()

    def _refresh_recent_boards(self):
        """최근 보드 메뉴 갱신"""
        from v.board import BoardManager

        self.recent_menu.clear()
        boards = BoardManager.list_boards()

        if not boards:
            action = self.recent_menu.addAction(t("menu.no_recent"))
            action.setEnabled(False)
        else:
            for name in boards[:get_recent_boards_count()]:
                action = self.recent_menu.addAction(name)
                action.triggered.connect(lambda checked, n=name: self._load_board_by_name(n))

    def _load_board_by_name(self, name):
        from v.board import BoardManager
        filepath = BoardManager.get_boards_dir() / f"{name}.qonvo"
        if filepath.exists():
            self._load_board_file(str(filepath))

    def _save_board(self):
        if not self.current_plugin:
            QMessageBox.warning(self, t("error.save_failed"), t("error.save_no_board"))
            return
        if self._current_filepath:
            self._save_to_file(self._current_filepath)
        else:
            self._save_board_as()

    def _save_board_as(self):
        if not self.current_plugin:
            QMessageBox.warning(self, t("error.save_failed"), t("error.save_no_board"))
            return

        from v.board import BoardManager

        name, ok = QInputDialog.getText(
            self, t("dialog.save_board_title"), t("dialog.save_board_prompt"),
            text=f"board_{len(BoardManager.list_boards()) + 1}"
        )
        if not ok or not name.strip():
            return

        filepath = str(BoardManager.get_boards_dir() / f"{name.strip()}.qonvo")
        self._save_to_file(filepath)

    def _save_to_file(self, filepath):
        import os

        if self._save_worker and self._save_worker.isRunning():
            self._save_worker.wait()

        board_data = self.current_plugin.collect_data()
        name = os.path.splitext(os.path.basename(filepath))[0]

        self._save_worker = _SaveWorker(name, board_data)
        self._save_worker.done.connect(lambda: self._on_save_done(filepath))
        self._save_worker.error.connect(self._on_save_error)
        self._save_worker.start()

    def _on_save_done(self, filepath):
        """저장 완료 후 파일 경로와 UI 상태를 갱신한다."""
        self._current_filepath = filepath
        self._modified = False
        self._update_title()
        self._refresh_recent_boards()

    def _on_save_error(self, msg):
        """저장 실패 시 에러 메시지를 표시한다."""
        QMessageBox.critical(self, t("error.save_failed"), msg)

    def _update_title(self):
        import os
        version = _get_version()
        app_title = f'{t("app.title")} {version}' if version else t("app.title")
        parts = [app_title]
        if self.current_plugin:
            parts.append(self.current_plugin.NAME)
        if self._current_filepath:
            name = os.path.splitext(os.path.basename(self._current_filepath))[0]
            parts.append(name)
        title = " - ".join(parts)
        if self._modified:
            title = "● " + title
        self.setWindowTitle(title)

    def mark_modified(self):
        if not self._modified:
            self._modified = True
            self._update_title()

    def closeEvent(self, event):
        # 서버 모드는 서버가 실시간 자동저장 → 로컬 저장 프롬프트 생략
        _in_server = bool(getattr(self, '_server_client', None)
                          and self._server_client.is_connected)
        if self._modified and self.current_plugin and not _in_server:
            reply = QMessageBox.question(
                self, t("app.title"),
                t("dialog.unsaved_message"),
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save
            )
            if reply == QMessageBox.StandardButton.Save:
                self._save_board()
                if self._save_worker and self._save_worker.isRunning():
                    self._save_worker.wait()
            elif reply == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
        # presence 하트비트 정지(즉시 오프라인 통보)
        try:
            if getattr(self, "_presence", None):
                self._presence.stop()
        except Exception:
            pass

        # 임시 파일 정리
        from v.temp_file_manager import TempFileManager
        try:
            TempFileManager().cleanup_session()
        except Exception:
            pass  # 정리 실패 시 조용히 무시

        # 만료된 batch job 정리 (48시간 초과)
        try:
            from v.batch_queue import BatchQueueManager
            BatchQueueManager().cleanup_stale()
        except Exception:
            pass

        if self.data_viewer_window:
            self.data_viewer_window.close()
            self.data_viewer_window = None
        if self.dev_window:
            self.dev_window.destroy_streams()
            self.dev_window.close()
            self.dev_window = None
        event.accept()

    def _open_boards_folder(self):
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        from v.board import BoardManager
        boards_dir = BoardManager.get_boards_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(boards_dir)))

    def _run_board_task(self, title, fn, on_done):
        """fn(progress)를 BoardTaskWorker로 백그라운드 실행하고 QProgressDialog를 띄운다.

        on_done(result_dict)은 정상 완료 시 메인 스레드에서 호출된다.
        """
        from PyQt6.QtWidgets import QProgressDialog
        from PyQt6.QtCore import Qt
        from v.export_worker import BoardTaskWorker

        dlg = QProgressDialog(self)
        dlg.setWindowTitle(title)
        dlg.setLabelText("준비 중…")
        dlg.setRange(0, 0)  # 처음엔 busy(불확정)
        dlg.setMinimumDuration(0)
        dlg.setMinimumWidth(420)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)

        worker = BoardTaskWorker(fn, self)
        self._board_task_worker = worker  # GC 방지 참조 유지

        def on_prog(done, total, label):
            if total <= 0:
                dlg.setRange(0, 0)
            else:
                dlg.setRange(0, total)
                dlg.setValue(done)
            if label:
                dlg.setLabelText(label)

        def finish_ok(result):
            dlg.close()
            self._board_task_worker = None
            on_done(result)

        def finish_err(msg):
            dlg.close()
            self._board_task_worker = None
            QMessageBox.critical(self, title, f"실패:\n{msg}")

        def finish_cancel():
            dlg.close()
            self._board_task_worker = None

        worker.progress.connect(on_prog)
        worker.done.connect(finish_ok)
        worker.failed.connect(finish_err)
        worker.cancelled.connect(finish_cancel)
        worker.finished.connect(worker.deleteLater)
        dlg.canceled.connect(worker.cancel)

        worker.start()
        dlg.show()

    def _export_to_folder(self):
        """현재 보드를 디스크에 저장한 뒤, .qonvo를 사람이 읽는 폴더로 내보낸다.

        무거운 저장/추출/렌더는 워커 스레드에서 → 메인 스레드 프리즈 없음.
        """
        import os
        import shutil
        from pathlib import Path
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from v.board import BoardManager
        from v import board_export

        if not self.current_plugin:
            QMessageBox.warning(self, t("error.save_failed"), t("error.save_no_board"))
            return

        if self._save_worker and self._save_worker.isRunning():
            self._save_worker.wait()

        if self._current_filepath:
            name = os.path.splitext(os.path.basename(self._current_filepath))[0]
        else:
            name, ok = QInputDialog.getText(
                self, t("dialog.save_board_title"), t("dialog.save_board_prompt"),
                text=f"board_{len(BoardManager.list_boards()) + 1}")
            if not ok or not name.strip():
                return
            name = name.strip()

        # collect_data()는 Qt 위젯을 만지므로 반드시 메인 스레드에서.
        try:
            data = self.current_plugin.collect_data()
        except Exception as e:
            QMessageBox.critical(self, t("error.save_failed"), str(e))
            return

        parent = QFileDialog.getExistingDirectory(
            self, t("menu.export_folder"), str(Path.home()))
        if not parent:
            return
        out_dir = Path(parent) / f"{name}_export"

        if out_dir.exists() and any(out_dir.iterdir()):
            reply = QMessageBox.question(
                self, t("menu.export_folder"),
                f"'{out_dir.name}' 폴더가 이미 있고 비어있지 않습니다.\n비우고 진행할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                shutil.rmtree(out_dir)
            except OSError as e:
                QMessageBox.critical(self, t("menu.export_folder"), str(e))
                return

        def task(progress):
            # 저장(첨부 재기록)도 워커 안에서. BoardManager.save는 io_lock으로 thread-safe.
            progress(0, 0, "보드 저장 중…")
            filepath = BoardManager.save(name, data)
            result = board_export.export_qonvo_to_folder(filepath, out_dir, progress=progress)
            result["filepath"] = filepath
            return result

        def on_done(result):
            if result.get("filepath"):
                self._on_save_done(result["filepath"])
            counts = result.get("counts", {})
            summary = ", ".join(f"{k}: {v}" for k, v in counts.items()) or "(내용 없음)"
            reply = QMessageBox.question(
                self, t("menu.export_folder"),
                f"폴더로 내보냈습니다:\n{result.get('out_dir', out_dir)}\n\n{summary}\n\n폴더를 열까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_dir)))

        self._run_board_task(t("menu.export_folder"), task, on_done)

    def _import_from_folder(self):
        """export로 만든 폴더(board.json + attachments/)를 .qonvo로 재패키징 후 연다."""
        from pathlib import Path
        from v.board import BoardManager
        from v import board_export

        folder = QFileDialog.getExistingDirectory(
            self, t("menu.import_folder"), str(Path.home()))
        if not folder:
            return
        folder = Path(folder)
        if not (folder / "board.json").exists():
            QMessageBox.warning(
                self, t("menu.import_folder"),
                "선택한 폴더에 board.json 이 없습니다.\nexport로 만든 폴더를 선택하세요.")
            return

        default_name = folder.name
        if default_name.endswith("_export"):
            default_name = default_name[:-len("_export")]
        name, ok = QInputDialog.getText(
            self, t("dialog.save_board_title"), t("dialog.save_board_prompt"),
            text=default_name)
        if not ok or not name.strip():
            return
        name = name.strip()

        dest = BoardManager.get_boards_dir() / f"{name}.qonvo"
        if dest.exists():
            reply = QMessageBox.question(
                self, t("menu.import_folder"),
                f"'{name}' 보드가 이미 있습니다. 덮어쓸까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        def task(progress):
            board_export.import_folder_to_qonvo(folder, dest, progress=progress)
            return {"dest": str(dest)}

        def on_done(result):
            self._load_board_file(result["dest"])

        self._run_board_task(t("menu.import_folder"), task, on_done)

    def _connect_to_server(self):
        # 메뉴/툴바에서 호출 — 마크식 서버 목록 화면으로 전환
        self._show_server_browser()

    def _show_server_browser(self):
        """welcome 좌측 '서버 접속' → 중앙을 서버 목록 화면으로 전환(마인크래프트식)."""
        from v.boards.whiteboard.server_browser import ServerBrowserWidget
        w = ServerBrowserWidget(self, show_back=True)
        w.back_requested.connect(self._show_welcome)
        w.connect_requested.connect(self._connect_with_info)
        self.setCentralWidget(w)

    def _connect_with_info(self, info):
        # 서버 목록에서 선택한 서버로 실제 접속(보드 목록 → 열기)
        from v.boards.whiteboard.server_browser import ServerBoardListDialog
        from v.boards.whiteboard.server_client import ServerClient

        if not info:
            return

        if not hasattr(self, '_server_client') or self._server_client is None:
            self._server_client = ServerClient(self)

        client = self._server_client

        def cleanup():
            for sig, fn in ((client.auth_ok, on_auth_ok),
                            (client.auth_fail, on_auth_fail),
                            (client.disconnected, on_conn_fail)):
                try:
                    sig.disconnect(fn)
                except Exception:
                    pass

        def on_auth_ok(level, boards):
            cleanup()
            board_dlg = ServerBoardListDialog(client, self)
            if board_dlg.exec() != QDialog.DialogCode.Accepted:
                # 보드 선택 취소 → 연결 종료
                client.disconnect_from_server()
                self.setWindowTitle("Qonvo")
                return
            board_id = board_dlg.get_board_id()
            if not board_id:
                client.disconnect_from_server()
                self.setWindowTitle("Qonvo")
                return
            # 서버 보드는 화이트보드 — 종류 선택 다이얼로그 없이 바로 로드
            from v.boards import get_plugin
            wb = get_plugin("whiteboard")
            if wb:
                self._load_plugin(wb)
            if self.current_plugin:
                self.current_plugin.set_server_client(client)
            # 스냅샷 prime 캐시 비활성 — 항상 서버 full sync 로 정확하게 받음.
            # (prime 은 일부 노드가 비는 문제가 있어 끔. 첨부 이미지 캐시는 유지)
            client._last_seq = 0
            client.join_board(board_id)
            self._enter_server_mode(info["username"], board_id)

        def on_auth_fail(reason):
            cleanup()
            self.setWindowTitle("Qonvo")
            QMessageBox.warning(self, "접속 실패", reason)

        def on_conn_fail(reason):
            # 연결 단계 실패(DNS/서버다운/포트/네트워크) — 멈추지 말고 에러 표시
            cleanup()
            self.setWindowTitle("Qonvo")
            QMessageBox.warning(
                self, "접속 실패",
                f"서버에 연결할 수 없습니다.\n{reason}\n\n"
                "주소·포트·네트워크를 확인하세요. (도메인이 방금 안 잡히면 잠시 후 재시도)")

        client.auth_ok.connect(on_auth_ok)
        client.auth_fail.connect(on_auth_fail)
        client.disconnected.connect(on_conn_fail)

        self.setWindowTitle("Qonvo — 접속 중…")
        client.connect_to_server(
            info["host"], info["port"],
            info["username"], info["password"],
            secure=info.get("secure", False),
            merri=info.get("merri", False),
        )

    # ---- 인앱 호스팅("딸깍 초대") -------------------------------------
    def _lan_ip(self) -> str:
        """LAN 접속용 로컬 IP (UPnP 실패 시 안내용). 패킷은 보내지 않는다."""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return "127.0.0.1"

    def _host_current_board(self):
        """지금 연 보드를 내 PC 임베드 서버에서 서빙하고 초대 링크를 만든다."""
        import os
        if self.current_plugin is None or not hasattr(self.current_plugin, "collect_data"):
            QMessageBox.information(self, "호스팅", "먼저 보드를 여세요."); self._fire_invite_cb(None); return
        if getattr(self, "_embedded_host", None) and self._embedded_host.is_running():
            QMessageBox.information(self, "호스팅", "이미 호스팅 중입니다."); self._fire_invite_cb(None); return
        # 첨부 경로 해석을 위해 보드는 자기 이름으로 저장돼 있어야 한다.
        if not getattr(self, "_current_filepath", None):
            QMessageBox.information(self, "호스팅", "먼저 보드를 저장하세요 (Ctrl+S).\n저장된 보드만 호스팅할 수 있어요.")
            self._fire_invite_cb(None); return

        from v.boards.whiteboard.invite import generate_id
        from v.boards.whiteboard.embedded_host import EmbeddedHost
        from v.boards.whiteboard import profile as _profile
        from v.board import BoardManager

        board_id = generate_id()
        self._host_board_id = board_id
        prof = _profile.get_profile()
        self._host_username = (prof["username"] if prof else "") or "호스트"
        name = os.path.splitext(os.path.basename(self._current_filepath))[0]
        self._host_board_name = name   # 호스팅 종료 시 이 이름으로 로컬 저장(편집 영속)
        data = self.current_plugin.collect_data()

        self.setWindowTitle("Qonvo — 호스팅 준비 중…")
        self._host_save_worker = _SaveWorker(name, data)

        def _seed_and_host():
            filepath = str(BoardManager.get_boards_dir() / f"{name}.qonvo")
            self._embedded_host = EmbeddedHost(self)
            self._embedded_host.ready.connect(self._on_host_ready)
            self._embedded_host.failed.connect(self._on_host_failed)
            self._embedded_host.start(board_id, qonvo_path=filepath,
                                      host_username=self._host_username,
                                      relay_url=self._relay_ws_base())   # 릴레이 폴백

        self._host_save_worker.done.connect(_seed_and_host)
        self._host_save_worker.error.connect(
            lambda m: (self.setWindowTitle("Qonvo"), QMessageBox.critical(self, "저장 실패", m)))
        self._host_save_worker.start()

    def _on_host_failed(self, reason: str):
        self.setWindowTitle("Qonvo")
        QMessageBox.warning(self, "호스팅 실패", reason)
        self._fire_invite_cb(None)

    def _on_host_ready(self, port: int, connect_host: str):
        """임베드 서버 기동 완료 → 호스트 자가접속 + 초대 spec(직접후보들+릴레이)."""
        from v.boards.whiteboard.invite import format_invite
        # 직접 접속 후보: LAN IP들 + (UPnP되면)공인주소
        hosts = [{"host": ip, "port": port, "secure": False} for ip in self._lan_ips()]
        if connect_host:
            hosts.append({"host": connect_host, "port": port, "secure": False})
        relay = self._relay_ws_base()
        primary_host = connect_host or (hosts[0]["host"] if hosts else "127.0.0.1")
        primary = format_invite(self._host_board_id, primary_host, port, secure=False)
        spec = {"board_id": self._host_board_id, "port": port, "hosts": hosts,
                "relay": relay, "primary": primary}
        self._host_invite_spec = spec
        self._host_invite_link = primary
        # 호스트도 클라로 자기 서버에 접속(localhost 직접)
        self._connect_and_join(
            [{"kind": "direct", "host": "127.0.0.1", "port": port, "secure": False}],
            self._host_username, "", self._host_board_id, merri=False)
        # People 초대 요청 때문에 시작된 거면 → 카드 전송 콜백(spec), 팝업 생략
        if getattr(self, "_pending_invite_cb", None):
            self._fire_invite_cb(spec)
            return
        # 메뉴에서 직접 호스팅한 경우: 링크 안내 + 클립보드 복사
        QApplication.clipboard().setText(primary)
        note = ("" if relay else
                "\n\n⚠ 릴레이를 못 찾았어요 — 같은 LAN에서만 접속됩니다.")
        box = QMessageBox(self)
        box.setWindowTitle("초대 링크")
        box.setText(f"친구에게 이 링크를 보내세요 (클립보드에 복사됨):\n\n{primary}{note}"
                    "\n\n(People에서 '보드 초대'로 보내면 어디서든 자동 접속됩니다)")
        box.exec()

    def _current_invite_link(self):
        """호스팅 중이면 현재 초대 링크, 아니면 None."""
        if getattr(self, "_embedded_host", None) and self._embedded_host.is_running():
            return getattr(self, "_host_invite_link", "") or None
        return None

    def _fire_invite_cb(self, spec):
        """대기 중인 People 초대 콜백을 1회 호출한다(spec dict or None)."""
        cb = getattr(self, "_pending_invite_cb", None)
        self._pending_invite_cb = None
        if cb:
            cb(spec)

    def _request_invite_link(self, cb):
        """초대 링크를 보장해 콜백에 넘긴다 — 호스팅 중이면 즉시, 아니면 자동 호스팅.

        People 패널의 '보드 초대'가 사용. 호스팅을 못 시작하면 cb(None).
        """
        # 이미 호스팅 중이면 저장된 spec 즉시 반환
        if self._current_invite_link() and getattr(self, "_host_invite_spec", None):
            cb(self._host_invite_spec); return
        self._pending_invite_cb = cb
        self._host_current_board()   # 끝나면 _on_host_ready/_failed 가 cb(spec) 발화

    def _join_invite_link(self, link: str):
        """초대 링크 문자열 → spec 으로 변환해 접속(직접+릴레이 폴백)."""
        from v.boards.whiteboard.invite import parse_invite
        inv = parse_invite(link or "")
        if not inv or not inv.board_id:
            QMessageBox.warning(self, "참여", "올바른 초대 링크가 아닙니다."); return
        spec = {"board_id": inv.board_id, "primary": link, "relay": self._relay_ws_base(),
                "hosts": [{"host": inv.host, "port": inv.port, "secure": inv.secure}]}
        self._join_invite_spec(spec)

    def _add_image_to_board(self, qimage):
        """People DM 이미지를 현재 보드에 이미지카드로 추가한다(보드↔DM 브릿지)."""
        from PyQt6.QtWidgets import QMessageBox
        if self.current_plugin is None or not hasattr(self.current_plugin, "add_image_card"):
            QMessageBox.information(self, "보드에 추가", "먼저 보드를 여세요.")
            return
        try:
            import os, uuid, tempfile
            tmp = os.path.join(tempfile.gettempdir(), f"qonvo_dm_{uuid.uuid4().hex}.png")
            qimage.save(tmp, "PNG")
            from PyQt6.QtCore import QPointF
            pos = None
            view = getattr(self.current_plugin, "view", None)
            if view is not None:
                pos = view.mapToScene(view.viewport().rect().center())
            self.current_plugin.add_image_card(tmp, pos or QPointF(0, 0))
            self.mark_modified()
            QMessageBox.information(self, "보드에 추가", "이미지를 보드에 추가했어요.")
        except Exception as e:
            QMessageBox.warning(self, "보드에 추가", f"추가 실패: {e}")

    def _stop_hosting(self):
        """호스팅을 중지한다(임베드 서버 닫기 + 자가접속 해제). People 카드 '중지'용."""
        if getattr(self, "_embedded_host", None) and self._embedded_host.is_running():
            self._disconnect_from_server()   # 임베드 서버 stop + 서버모드 종료 포함
            self._host_invite_link = ""

    def _toggle_people_panel(self):
        """People 창(merri 동료/DM/초대)을 별도 창으로 띄운다."""
        from v.boards.whiteboard.people_panel import PeopleWindow

        win = getattr(self, "_people_window", None)
        if win is None:
            win = PeopleWindow(invite_requester=self._request_invite_link,
                               invite_joiner=self._join_invite_spec,
                               host_status=self._current_invite_link,
                               invite_stopper=self._stop_hosting,
                               board_image_adder=self._add_image_to_board, parent=self)
            self._people_window = win
        if win.isVisible():
            win.raise_(); win.activateWindow()
        else:
            win.reload()
            win.show(); win.raise_(); win.activateWindow()

    def _join_by_invite(self):
        """초대 링크를 붙여넣어 접속한다(게스트 표시이름)."""
        from v.boards.whiteboard.invite import parse_invite
        text, ok = QInputDialog.getText(
            self, "초대 링크로 접속", "초대 링크를 붙여넣으세요:\n(예: clever-otter-734@host:9700)")
        if not ok or not text.strip():
            return
        inv = parse_invite(text)
        if not inv or not inv.board_id:
            QMessageBox.warning(self, "초대 링크",
                                "올바른 초대 링크가 아닙니다.\n예: clever-otter-734@host:9700")
            return
        name, ok2 = QInputDialog.getText(self, "표시 이름", "사용할 이름:")
        if not ok2 or not name.strip():
            return
        attempts = [{"kind": "direct", "host": inv.host, "port": inv.port, "secure": inv.secure}]
        rb = self._relay_ws_base()
        if rb:
            attempts.append({"kind": "relay", "relay": rb, "session": inv.board_id})
        self._connect_and_join(attempts, name.strip(), "", inv.board_id, merri=False)

    # ---- 네트워크 후보 -------------------------------------------------
    def _lan_ips(self):
        """이 PC 의 로컬 IPv4 들(직접 접속 후보)."""
        import socket
        ips = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0]); s.close()
        except Exception:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
        return ips

    def _relay_ws_base(self):
        """릴레이(merri 광고 qonvo 서버 = 집서버)의 ws base. 없으면 ""."""
        try:
            from v.boards.whiteboard.qonvo_presence import hub_base
            b = hub_base()
            if b:
                return b.replace("https://", "wss://").replace("http://", "ws://")
        except Exception:
            pass
        return ""

    def _join_invite_spec(self, spec):
        """People 카드 '참여하기' — 직접 후보들 → 릴레이 순으로 시도."""
        from v.boards.whiteboard import profile as _profile
        board_id = (spec or {}).get("board_id", "")
        if not board_id:
            QMessageBox.warning(self, "참여", "올바른 초대가 아닙니다.")
            return
        prof = _profile.get_profile()
        name = (prof["username"] if prof else "") or "guest"
        attempts = []
        for h in spec.get("hosts", []):
            attempts.append({"kind": "direct", "host": h.get("host", ""),
                             "port": int(h.get("port", 9700)), "secure": bool(h.get("secure"))})
        if spec.get("relay"):
            attempts.append({"kind": "relay", "relay": spec["relay"], "session": board_id})
        if not attempts:
            QMessageBox.warning(self, "참여", "접속 주소가 없습니다.")
            return
        self._connect_and_join(attempts, name, "", board_id, merri=False)

    def _connect_and_join(self, attempts, username, password, board_id, merri=False):
        """접속 시도 목록을 순서대로 시도(직접→릴레이), 첫 성공에서 board_id join.

        attempts: [{"kind":"direct","host","port","secure"} | {"kind":"relay","relay","session"}]
        각 시도는 타임아웃(9s) 안에 auth_ok 없으면 다음으로 넘어간다.
        """
        from v.boards.whiteboard.server_client import ServerClient
        if not getattr(self, "_server_client", None):
            self._server_client = ServerClient(self)
        self._ja = list(attempts)
        self._ja_ctx = (username, password, board_id, merri)
        self._try_next_attempt()

    def _try_next_attempt(self):
        from v.boards import get_plugin
        client = self._server_client
        if not getattr(self, "_ja", None):
            self.setWindowTitle("Qonvo")
            QMessageBox.warning(self, "접속 실패",
                                "모든 경로로 접속할 수 없습니다.\n호스트가 켜져 있는지 확인하세요.")
            return
        a = self._ja.pop(0)
        username, password, board_id, merri = self._ja_ctx
        timer = QTimer(self); timer.setSingleShot(True); timer.setInterval(9000)

        def cleanup():
            timer.stop()
            for sig, fn in ((client.auth_ok, on_ok), (client.auth_fail, on_authfail),
                            (client.disconnected, on_fail)):
                try:
                    sig.disconnect(fn)
                except Exception:
                    pass

        def on_ok(level, boards):
            cleanup()
            wb = get_plugin("whiteboard")
            if wb:
                self._load_plugin(wb)
            if self.current_plugin:
                self.current_plugin.set_server_client(client)
            client._last_seq = 0
            client.join_board(board_id)
            self._enter_server_mode(username, board_id)

        def on_authfail(reason):
            cleanup()
            try:
                client.disconnect_from_server()
            except Exception:
                pass
            self.setWindowTitle("Qonvo")
            QMessageBox.warning(self, "접속 실패", reason)   # 인증 실패는 경로 바꿔도 동일 → 중단

        def on_fail(reason=""):
            cleanup()
            try:
                client.disconnect_from_server()
            except Exception:
                pass
            self._try_next_attempt()   # 다음 경로

        timer.timeout.connect(lambda: on_fail("timeout"))
        client.auth_ok.connect(on_ok)
        client.auth_fail.connect(on_authfail)
        client.disconnected.connect(on_fail)

        label = "직접" if a.get("kind") == "direct" else "릴레이"
        self.setWindowTitle(f"Qonvo — 접속 중… ({label})")
        timer.start()
        if a.get("kind") == "direct":
            client.connect_to_server(a.get("host", ""), int(a.get("port", 9700)),
                                     username, password, secure=a.get("secure", False), merri=merri)
        else:
            ws = (a.get("relay", "").rstrip("/")
                  + "/relay/c?session=" + a.get("session", ""))
            client.connect_to_server("", 0, username, password, merri=merri, ws_url=ws)

    def _enter_server_mode(self, username: str, board_id: str):
        # 접속 단계 피드백: 접속 중… → 로드 중… → 접속!(보드명)
        self._server_user = username
        self._server_board = board_id
        self.setWindowTitle(f"Qonvo — 로드 중… ({board_id})")
        self.action_connect.setVisible(False)
        if hasattr(self, "action_host"):
            self.action_host.setVisible(False)
            self.action_invite_join.setVisible(False)
        self.action_disconnect.setVisible(True)
        # 온라인 모드는 서버가 실시간 자동저장 → 로컬 저장 비활성
        self.action_save.setEnabled(False)
        self.action_save_as.setEnabled(False)

        client = self._server_client
        client.user_joined.connect(self._on_server_user_joined)
        client.user_left.connect(self._on_server_user_left)
        client.disconnected.connect(self._on_server_disconnected)
        client.server_message.connect(self._on_server_message)
        client.error_received.connect(self._on_server_error)
        client.sync_received.connect(self._on_server_loaded)
        client.presence_received.connect(self._on_server_presence)
        client.ping_updated.connect(self._on_server_ping)
        client.chat_received.connect(self._on_chat_received)
        self._setup_server_overlays(username, board_id)
        self._setup_chat_dock(client)

    def _setup_server_overlays(self, username, board_id):
        """F1(사용자목록)/F12(디버그) 오버레이 생성·단축키 등록."""
        from v.boards.whiteboard.server_overlays import DebugOverlay, UserListOverlay
        from PyQt6.QtGui import QShortcut, QKeySequence

        if not hasattr(self, '_dbg_overlay') or self._dbg_overlay is None:
            self._dbg_overlay = DebugOverlay(self)
            self._user_overlay = UserListOverlay(self)
            self._sc_f12 = QShortcut(QKeySequence("F12"), self)
            self._sc_f12.activated.connect(self._dbg_overlay.toggle)
            self._sc_f1 = QShortcut(QKeySequence("F1"), self)
            self._sc_f1.activated.connect(self._user_overlay.toggle)
            self._user_overlay.user_clicked.connect(self._follow_user)
            self._dbg_timer = QTimer(self)
            self._dbg_timer.setInterval(1000)
            self._dbg_timer.timeout.connect(self._update_debug_overlay)
        host = ""
        try:
            host = self._server_client.http_base.split("://", 1)[-1]
        except Exception:
            pass
        self._dbg_overlay.set(server=f"{username}@{host}", board=board_id)
        self._dbg_timer.start()
        self._position_server_overlays()

    def _position_server_overlays(self):
        if getattr(self, '_dbg_overlay', None) is None:
            return
        m = 12
        self._dbg_overlay.move(self.width() - self._dbg_overlay.width() - m, m)
        self._user_overlay.move(m, m)
        for ov in (self._dbg_overlay, self._user_overlay):
            if ov.isVisible():
                ov.raise_()

    def _update_debug_overlay(self):
        if getattr(self, '_dbg_overlay', None) is None:
            return
        nodes = 0
        try:
            nodes = len(getattr(self.app, 'nodes', {}))
        except Exception:
            pass
        self._dbg_overlay.set(nodes=nodes)
        self._position_server_overlays()

    def _on_server_presence(self, users):
        if getattr(self, '_user_overlay', None) is not None:
            self._user_overlay.set_users(users)
            self._dbg_overlay.set(users=len(users))
            self._position_server_overlays()

    def _follow_user(self, username: str):
        """F1 목록에서 사용자 클릭 → 그 사람 커서 위치로 화면 이동(따라가기)."""
        from PyQt6.QtCore import QPointF
        plg = self.current_plugin
        cl = getattr(plg, "_cursor_layer", None)
        view = getattr(plg, "view", None)
        if cl is None or view is None:
            return
        c = cl._cursors.get(username)
        if not c:
            self.statusBar().showMessage(f"{username} 님의 커서를 아직 못 봤어요", 3000)
            return
        x, y = c["cur"]
        view.centerOn(QPointF(float(x), float(y)))
        self.statusBar().showMessage(f"{username} 님 위치로 이동", 2500)

    def _on_server_ping(self, ms):
        if getattr(self, '_dbg_overlay', None) is not None:
            self._dbg_overlay.set(ping=ms)

    def _setup_chat_dock(self, client):
        """서버 채팅 패널(우측 도크) 생성·표시."""
        from PyQt6.QtWidgets import QDockWidget
        from v.boards.whiteboard.chat_panel import ChatPanel

        if getattr(self, '_chat_dock', None) is None:
            self._chat_panel = ChatPanel(self)
            self._chat_dock = QDockWidget("채팅", self)
            self._chat_dock.setWidget(self._chat_panel)
            self._chat_dock.setObjectName("server_chat")
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._chat_dock)
            self._chat_panel.send_message.connect(self._send_chat)
            self._chat_dock.hide()  # 히스토리용 — 기본 숨김(말풍선이 메인)

    def _send_chat(self, text):
        if getattr(self, '_server_client', None) is not None:
            self._server_client.send_chat(text)

    def _on_chat_received(self, msg: dict):
        if getattr(self, '_chat_panel', None) is not None:
            self._chat_panel.add_message(msg.get("user", "?"), msg.get("color", "#888"),
                                         msg.get("text", ""), msg.get("ts", 0))

    def _on_server_loaded(self, _snapshot):
        """첫 sync(보드 데이터) 도착 = 구조 로드 완료. 첨부 다운로드는 이어서 진행."""
        # 첨부가 없으면 바로 완료 표시, 있으면 % 진행이 이어받음
        self.setWindowTitle(f"Qonvo — 로드 중… ({self._server_board})")

    def set_server_loading_progress(self, done: int, total: int):
        """첨부 다운로드 진행률을 타이틀에 % 로 표시(플러그인이 호출)."""
        if total > 0:
            pct = int(done * 100 / total)
            self.setWindowTitle(f"Qonvo — 로드 중… {pct}%  ({self._server_board})")

    def set_server_loaded(self):
        """로드 완료 → 최종 타이틀 + '접속!'."""
        self.setWindowTitle(f"Qonvo - {self._server_board} ({self._server_user}@Server)")
        try:
            self.statusBar().showMessage("접속!", 3000)
        except Exception:
            pass

    def _exit_server_mode(self):
        self.setWindowTitle("Qonvo")
        self._server_board = ""      # presence: 협업 종료 → online 로
        self.action_connect.setVisible(True)
        if hasattr(self, "action_host"):
            self.action_host.setVisible(True)
            self.action_invite_join.setVisible(True)
        self.action_disconnect.setVisible(False)
        self.action_save.setEnabled(True)
        self.action_save_as.setEnabled(True)

        if self._server_client:
            try:
                self._server_client.user_joined.disconnect(self._on_server_user_joined)
                self._server_client.user_left.disconnect(self._on_server_user_left)
                self._server_client.disconnected.disconnect(self._on_server_disconnected)
                self._server_client.server_message.disconnect(self._on_server_message)
                self._server_client.error_received.disconnect(self._on_server_error)
                self._server_client.sync_received.disconnect(self._on_server_loaded)
                self._server_client.presence_received.disconnect(self._on_server_presence)
                self._server_client.ping_updated.disconnect(self._on_server_ping)
                self._server_client.chat_received.disconnect(self._on_chat_received)
            except Exception:
                pass
        # 오버레이/채팅 정리
        if getattr(self, '_dbg_timer', None) is not None:
            self._dbg_timer.stop()
        for ov in ('_dbg_overlay', '_user_overlay'):
            w = getattr(self, ov, None)
            if w is not None:
                w.hide()
        if getattr(self, '_chat_dock', None) is not None:
            self._chat_dock.hide()

    def _persist_host_board(self):
        """내가 호스트였다면, 세션 중 편집된 라이브 보드를 원본 .qonvo 로 저장한다.

        호스트의 plugin 은 임베드 서버와 동기화된 현재 상태를 그대로 들고 있으므로
        collect_data() 가 최신본이다. 종료 시 _reset_to_welcome 가 보드를 폐기하기 전에
        호출해야 편집이 유실되지 않는다(= '다시 열어보면 저장 안 됨' 버그 수정).
        """
        name = getattr(self, "_host_board_name", "")
        if not name or self.current_plugin is None or not hasattr(self.current_plugin, "collect_data"):
            return
        try:
            from v.board import BoardManager
            data = self.current_plugin.collect_data()
            BoardManager.save(name, data)   # 동기 저장(종료 중이라 블로킹 허용)
            self.statusBar().showMessage(f"'{name}' 보드 저장됨", 4000)
        except Exception as e:
            QMessageBox.warning(self, "저장", f"호스팅 보드 저장 실패: {e}")
        finally:
            self._host_board_name = ""

    def _disconnect_from_server(self):
        # 호스트면 라이브 보드를 먼저 로컬 저장(폐기 전에)
        if getattr(self, "_embedded_host", None) and self._embedded_host.is_running():
            self._persist_host_board()
        if hasattr(self, '_server_client') and self._server_client:
            if self.current_plugin and hasattr(self.current_plugin, 'detach_server_client'):
                self.current_plugin.detach_server_client()
            self._server_client.disconnect_from_server()
        # 내가 호스트였다면 임베드 서버도 종료
        if getattr(self, "_embedded_host", None) and self._embedded_host.is_running():
            self._embedded_host.stop()
        self._exit_server_mode()
        self._reset_to_welcome()   # 서버 끊으면 메인으로

    def _go_home(self):
        """현재 보드를 닫고 메인(웰컴) 화면으로 간다."""
        # 서버 모드면 끊기(끊으면 메인까지 감)
        if getattr(self, '_server_client', None) and getattr(self._server_client, 'is_connected', False):
            self._disconnect_from_server()
            return
        if self.current_plugin is None:
            return  # 이미 메인
        # 로컬 보드 미저장 변경 확인
        if self._modified and self.current_plugin:
            reply = QMessageBox.question(
                self, t("app.title"), t("dialog.unsaved_message"),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
            if reply == QMessageBox.StandardButton.Save:
                self._save_board()
                if self._save_worker and self._save_worker.isRunning():
                    self._save_worker.wait()
            elif reply == QMessageBox.StandardButton.Cancel:
                return
        self._reset_to_welcome()

    def _reset_to_welcome(self):
        """보드 언로드 + 메인(웰컴) 화면 표시 + 보드 전용 액션 비활성."""
        try:
            self.app.clear()
        except Exception:
            pass
        self.current_plugin = None
        self._current_filepath = None
        self._modified = False
        for act in ('action_reset_view', 'action_reset_zoom', 'action_add_node',
                    'action_search_history', 'action_export_folder'):
            a = getattr(self, act, None)
            if a is not None:
                a.setEnabled(False)
        self._show_welcome()
        self._update_title()

    def _on_server_user_joined(self, user: str, level: int):
        self.statusBar().showMessage(f"{user} joined", 5000)

    def _on_server_user_left(self, user: str):
        self.statusBar().showMessage(f"{user} left", 5000)

    def _on_server_disconnected(self, reason: str):
        from PyQt6.QtWidgets import QMessageBox
        if getattr(self, "_embedded_host", None) and self._embedded_host.is_running():
            self._persist_host_board()   # 호스트면 라이브 보드 로컬 저장(폐기 전)
        if self.current_plugin and hasattr(self.current_plugin, 'detach_server_client'):
            self.current_plugin.detach_server_client()
        if getattr(self, "_embedded_host", None) and self._embedded_host.is_running():
            self._embedded_host.stop()
        self._exit_server_mode()
        self._reset_to_welcome()   # 연결 끊기면 메인으로
        QMessageBox.warning(self, "연결 끊김", f"서버 연결이 끊어졌어요: {reason}")

    def _on_server_message(self, text: str):
        self.statusBar().showMessage(f"[Server] {text}", 8000)

    def _on_server_error(self, code: str, message: str):
        self.statusBar().showMessage(f"Error: {code} - {message}", 8000)

    def _load_board(self):
        from v.board import BoardManager

        filepath, _ = QFileDialog.getOpenFileName(
            self, t("dialog.load_board_title"),
            str(BoardManager.get_boards_dir()),
            t("dialog.file_filter")
        )
        if filepath:
            self._load_board_file(filepath)

    def _load_board_file(self, filepath):
        from v.board import BoardManager, _get_app_version

        try:
            board_data = BoardManager.load(filepath)

            # 버전 체크
            file_version = board_data.get("version", "")
            app_version = _get_app_version()
            if app_version and file_version != app_version:
                reply = QMessageBox.warning(
                    self, t("dialog.version_mismatch_title"),
                    t("dialog.version_mismatch", file_version=file_version, app_version=app_version),
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Ok
                )
                if reply == QMessageBox.StandardButton.Cancel:
                    return

            board_type = board_data.get("type", "WhiteBoard")

            plugin_class = get_plugin_by_type(board_type)
            if not plugin_class:
                QMessageBox.critical(self, t("error.load_failed"), f"Unsupported board type: {board_type}")
                return

            self._load_plugin(plugin_class)
            self.current_plugin._board_name = Path(filepath).stem
            self.current_plugin.restore_data(board_data)

            self._current_filepath = filepath
            self._modified = False
            self._update_title()

        except Exception as e:
            QMessageBox.critical(self, t("error.load_failed"), str(e))
            # 로드 실패 시에도 메뉴 활성화 복원
            has_plugin = self.current_plugin is not None
            self.action_reset_view.setEnabled(has_plugin)
            self.action_reset_zoom.setEnabled(has_plugin)
            self.action_add_node.setEnabled(has_plugin)


def run_app(app: App):
    import sys
    from pathlib import Path
    import q
    from v.settings import get_language
    q.load(get_language())

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mokabun.qonvo")

    qapp = QApplication(sys.argv)
    qapp.setStyle("Fusion")

    # 아이콘 설정 — 리눅스/GNOME 호환 위해 PNG 우선(.ico 폴백)
    base = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) \
        else Path(__file__).parent.parent.parent
    for _name in ('icon.png', 'icon.ico'):
        _p = base / _name
        if _p.exists():
            qapp.setWindowIcon(QIcon(str(_p)))
            break
    # GNOME 등에서 독/작업표시줄 아이콘을 .desktop 으로 매칭하게 함
    try:
        qapp.setDesktopFileName("qonvo")
    except Exception:
        pass

    # 다크 테마
    from PyQt6.QtGui import QPalette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(43, 43, 43))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
    qapp.setPalette(palette)

    window = MainWindow(app)
    window.show()
    sys.exit(qapp.exec())


if __name__ == "__main__":
    app = App()
    run_app(app)
