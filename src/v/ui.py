"""
PyQt6 기반 메인 UI
- 플러그인 시스템으로 다양한 보드 타입 지원
"""
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QDialog, QListWidget,
    QListWidgetItem, QInputDialog, QMessageBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon

import sys
import tomllib
from pathlib import Path

from q import t
from v.app import App
from v.theme import Theme


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

    def _setup_menubar(self):
        """메뉴바 설정"""
        menubar = self.menuBar()

        # 파일 메뉴
        file_menu = menubar.addMenu(t("menu.file"))

        action_new = file_menu.addAction(t("menu.new_board"))
        action_new.setShortcut("Ctrl+N")
        action_new.triggered.connect(self._new_board)

        # 라운드 테이블 (실험적 기능)
        self.action_round_table = file_menu.addAction(t("menu.new_round_table"))
        self.action_round_table.setShortcut("Ctrl+Shift+R")
        self.action_round_table.triggered.connect(self._open_round_table)
        self.action_round_table.setVisible(is_experimental_mode())

        file_menu.addSeparator()

        action_save = file_menu.addAction(t("menu.save"))
        action_save.setShortcut("Ctrl+S")
        action_save.triggered.connect(self._save_board)

        action_save_as = file_menu.addAction(t("menu.save_as"))
        action_save_as.setShortcut("Ctrl+Shift+S")
        action_save_as.triggered.connect(self._save_board_as)

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
        """초기 환영 화면"""
        from v.board import BoardManager

        welcome = QWidget()
        welcome.setStyleSheet(f"background-color: {Theme.BG_PRIMARY};")
        layout = QVBoxLayout(welcome)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(t("app.title"))
        title.setStyleSheet(f"font-size: 48px; font-weight: bold; color: {Theme.TEXT_DISABLED};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(t("welcome.instructions"))
        subtitle.setStyleSheet(f"font-size: 14px; color: {Theme.TEXT_TERTIARY}; margin-top: 20px;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        # 최근 보드 목록
        boards = BoardManager.list_boards()
        if boards:
            recent_label = QLabel(t("welcome.recent_boards"))
            recent_label.setStyleSheet(f"font-size: 13px; color: #777; margin-top: 30px;")
            recent_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(recent_label)

            for name in boards[:get_recent_boards_count()]:
                btn = QPushButton(name)
                btn.setFixedWidth(300)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {Theme.BG_SECONDARY};
                        color: #ccc;
                        border: 1px solid {Theme.BG_HOVER};
                        border-radius: 8px;
                        padding: 10px 16px;
                        font-size: 13px;
                        text-align: left;
                    }}
                    QPushButton:hover {{
                        background-color: {Theme.BG_HOVER};
                        border-color: {Theme.ACCENT_PRIMARY};
                        color: #fff;
                    }}
                """)
                btn.clicked.connect(lambda checked, n=name: self._load_board_by_name(n))
                layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

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
        from v.board import BoardManager
        import os

        board_data = self.current_plugin.collect_data()
        name = os.path.splitext(os.path.basename(filepath))[0]

        try:
            BoardManager.save(name, board_data)
            self._current_filepath = filepath
            self._modified = False
            self._update_title()
            self._refresh_recent_boards()
        except Exception as e:
            QMessageBox.critical(self, t("error.save_failed"), str(e))

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
        if self._modified and self.current_plugin:
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
                if self._modified:
                    event.ignore()
                    return
            elif reply == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
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
        """보드 저장 폴더를 OS 파일 탐색기에서 열기"""
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        from v.board import BoardManager
        boards_dir = BoardManager.get_boards_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(boards_dir)))

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
    """앱 실행"""
    import sys
    from pathlib import Path
    import q
    from v.settings import get_language
    q.load(get_language())

    qapp = QApplication(sys.argv)
    qapp.setStyle("Fusion")

    # 아이콘 설정
    if getattr(sys, 'frozen', False):
        icon_path = Path(sys._MEIPASS) / 'icon.ico'
    else:
        icon_path = Path(__file__).parent.parent.parent / 'icon.ico'
    if icon_path.exists():
        qapp.setWindowIcon(QIcon(str(icon_path)))

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
