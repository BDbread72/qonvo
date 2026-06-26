"""
설정 다이얼로그 — 좌측 카테고리 내비 + 우측 페이지(스택) 구조
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QComboBox, QSpinBox, QCheckBox,
    QPushButton, QFrame, QListWidget, QListWidgetItem,
    QMessageBox, QInputDialog, QWidget, QScrollArea, QStackedWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal

from v.theme import Theme
from q import t
from v.model_plugin import get_all_models, get_all_model_ids
from v.settings import (
    get_api_key, save_api_key, get_api_keys, save_api_keys,
    get_default_model, set_default_model,
    get_recent_boards_count, set_recent_boards_count,
    get_board_size, set_board_size,
    is_developer_mode, set_developer_mode,
    get_language, set_language,
    is_experimental_mode, set_experimental_mode,
    set_enabled_plugins,
    get_plugin_api_keys, save_plugin_api_keys,
    get_setting, set_setting,
)


# lang/ 디렉토리에서 사용 가능한 언어 목록 자동 검출
def _available_languages() -> list[tuple[str, str]]:
    """사용 가능한 언어 [(code, display_name), ...]"""
    from q import _base_path
    lang_dir = _base_path()
    langs = []
    for f in sorted(lang_dir.glob("*.toml")):
        code = f.stem
        langs.append((code, code))
    return langs if langs else [("KR", "KR")]


class SettingsDialog(QDialog):
    """환경 설정 다이얼로그 (사이드바 카테고리 + 페이지)"""

    developer_mode_changed = pyqtSignal(bool)
    experimental_mode_changed = pyqtSignal(bool)
    api_keys_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings.title"))
        self.setMinimumSize(760, 560)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; }
            QLabel { color: #ddd; }
        """)

        # 페이지 빌드 전에 상태 초기화
        self._raw_keys: list[str] = get_api_keys()
        self._plugin_checks: dict[str, QCheckBox] = {}
        self._plugin_key_data: dict[str, list[str]] = {}
        self._plugin_key_widgets: dict[str, dict] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # ── 좌측: 카테고리 내비 ──
        self._nav = QListWidget()
        self._nav.setFixedWidth(184)
        self._nav.setStyleSheet("""
            QListWidget {
                background-color: #181818; border: none; outline: none;
                padding: 12px 8px;
            }
            QListWidget::item {
                color: #bbb; padding: 11px 14px; border-radius: 8px;
                margin: 2px 0; font-size: 13px;
            }
            QListWidget::item:hover { background-color: #262626; color: #fff; }
            QListWidget::item:selected {
                background-color: #0d6efd; color: #fff; font-weight: bold;
            }
        """)
        body.addWidget(self._nav)

        vsep = QFrame()
        vsep.setFrameShape(QFrame.Shape.VLine)
        vsep.setStyleSheet("color: #2a2a2a;")
        body.addWidget(vsep)

        # ── 우측: 페이지 스택 ──
        self._stack = QStackedWidget()
        body.addWidget(self._stack, 1)

        outer.addLayout(body, 1)

        # 페이지 등록 (아이콘 + 제목)
        self._add_page("⚙  일반", self._build_general_page())
        self._add_page("🔑  API 키", self._build_api_page())
        self._add_page("🧩  플러그인", self._build_plugins_page())
        self._add_page("👥  협업", self._build_collab_page())
        self._add_page("🛠  고급", self._build_advanced_page())

        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._nav.setCurrentRow(0)

        # ── 하단 버튼 바 (고정) ──
        btn_bar = QFrame()
        btn_bar.setStyleSheet("background-color: #1a1a1a; border-top: 1px solid #2a2a2a;")
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(16, 10, 16, 10)
        btn_cancel = QPushButton(t("button.cancel"))
        btn_cancel.setStyleSheet("padding: 9px 22px; border-radius: 6px;")
        btn_cancel.clicked.connect(self.reject)
        btn_cancel.setAutoDefault(False); btn_cancel.setDefault(False)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addStretch()
        btn_save = QPushButton(t("button.save"))
        btn_save.setStyleSheet(
            "padding: 9px 24px; background-color: #0d6efd;"
            " font-weight: bold; border-radius: 6px;")
        btn_save.clicked.connect(self._save)
        btn_save.setDefault(True); btn_save.setAutoDefault(True)   # Enter = 저장
        btn_layout.addWidget(btn_save)
        outer.addWidget(btn_bar)

    # ====================================================================
    # 페이지 구성
    # ====================================================================
    def _add_page(self, title: str, widget: QWidget):
        self._nav.addItem(QListWidgetItem(title))
        self._stack.addWidget(widget)

    def _new_page(self):
        """스크롤되는 빈 페이지 → (scroll_widget, content_layout) 반환."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea { background-color: #1e1e1e; border: none; }
            QWidget#settings_page { background-color: #1e1e1e; }
            QScrollBar:vertical { background: #1e1e1e; width: 8px; }
            QScrollBar::handle:vertical {
                background: #444; border-radius: 4px; min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        content = QWidget()
        content.setObjectName("settings_page")
        lay = QVBoxLayout(content)
        lay.setSpacing(12)
        lay.setContentsMargins(24, 22, 24, 22)
        scroll.setWidget(content)
        return scroll, lay

    def _build_general_page(self) -> QWidget:
        page, layout = self._new_page()
        layout.addWidget(self._page_title("일반"))

        layout.addWidget(self._section_label(t("settings.default_model")))
        layout.addWidget(self._hint_label(t("settings.default_model_hint")))
        self.model_combo = QComboBox()
        self.model_combo.setStyleSheet(self._combo_style())
        all_models = get_all_models()
        all_model_ids = get_all_model_ids()
        for model_id in all_model_ids:
            self.model_combo.addItem(all_models[model_id], model_id)
        default_model = get_default_model()
        if default_model and default_model in all_model_ids:
            self.model_combo.setCurrentIndex(all_model_ids.index(default_model))
        layout.addWidget(self.model_combo)

        layout.addWidget(self._separator())

        layout.addWidget(self._section_label(t("settings.recent_count")))
        layout.addWidget(self._hint_label(t("settings.recent_count_hint")))
        self.recent_spin = QSpinBox()
        self.recent_spin.setRange(1, 20)
        self.recent_spin.setValue(get_recent_boards_count())
        self.recent_spin.setStyleSheet(self._input_style())
        layout.addWidget(self.recent_spin)

        layout.addWidget(self._separator())

        layout.addWidget(self._section_label(t("settings.board_size")))
        layout.addWidget(self._hint_label(t("settings.board_size_hint")))
        self.board_size_spin = QSpinBox()
        self.board_size_spin.setRange(5000, 100000)
        self.board_size_spin.setSingleStep(5000)
        self.board_size_spin.setValue(get_board_size())
        self.board_size_spin.setStyleSheet(self._input_style())
        layout.addWidget(self.board_size_spin)

        layout.addWidget(self._separator())

        layout.addWidget(self._section_label(t("settings.language")))
        layout.addWidget(self._hint_label(t("settings.language_hint")))
        self.lang_combo = QComboBox()
        self.lang_combo.setStyleSheet(self._combo_style())
        current_lang = get_language()
        for code, display in _available_languages():
            self.lang_combo.addItem(display, code)
            if code == current_lang:
                self.lang_combo.setCurrentIndex(self.lang_combo.count() - 1)
        layout.addWidget(self.lang_combo)

        layout.addStretch()
        return page

    def _build_api_page(self) -> QWidget:
        page, layout = self._new_page()
        layout.addWidget(self._page_title("API 키"))
        layout.addWidget(self._section_label(t("settings.api_key")))
        layout.addWidget(self._hint_label("여러 키를 추가하면 요청이 자동 분산됩니다"))

        self.key_list = QListWidget()
        self.key_list.setFixedHeight(160)
        self.key_list.setStyleSheet("""
            QListWidget {
                background-color: #2d2d2d; color: #ddd;
                border: 1px solid #444; border-radius: 6px;
                padding: 4px; font-size: 12px;
            }
            QListWidget::item { padding: 4px 8px; }
            QListWidget::item:selected { background-color: #0d6efd; }
        """)
        self._key_count_label = QLabel("")
        self._key_count_label.setStyleSheet("color: #888; font-size: 11px;")
        self._refresh_key_list()
        layout.addWidget(self.key_list)

        key_btn_row = QHBoxLayout()
        key_btn_row.setSpacing(8)
        btn_add_key = QPushButton("+ 키 추가")
        btn_add_key.setStyleSheet("padding: 6px 14px; border-radius: 6px; font-size: 12px;")
        btn_add_key.clicked.connect(self._add_api_key)
        key_btn_row.addWidget(btn_add_key)
        btn_remove_key = QPushButton("- 키 삭제")
        btn_remove_key.setStyleSheet("padding: 6px 14px; border-radius: 6px; font-size: 12px;")
        btn_remove_key.clicked.connect(self._remove_api_key)
        key_btn_row.addWidget(btn_remove_key)
        key_btn_row.addStretch()
        key_btn_row.addWidget(self._key_count_label)
        layout.addLayout(key_btn_row)

        layout.addStretch()
        return page

    def _build_plugins_page(self) -> QWidget:
        page, layout = self._new_page()
        layout.addWidget(self._page_title("플러그인"))
        layout.addWidget(self._section_label(t("plugin.section_title")))
        layout.addWidget(self._hint_label(t("plugin.hint")))

        from v.model_plugin import PluginRegistry, get_plugins_dir
        discovered = PluginRegistry.instance().get_discovered_plugins()

        if discovered:
            for info in discovered:
                models_str = ", ".join(info["models"].values()) if info["models"] else "no models"
                cb = QCheckBox(f'{info["name"]} v{info["version"]}  ({models_str})')
                cb.setChecked(info["enabled"])
                cb.setStyleSheet(self._check_style())
                layout.addWidget(cb)
                self._plugin_checks[info["id"]] = cb

                pid = info["id"]
                self._plugin_key_data[pid] = get_plugin_api_keys(pid)

                key_container = QWidget()
                key_layout = QHBoxLayout(key_container)
                key_layout.setContentsMargins(24, 0, 0, 0)
                key_layout.setSpacing(6)
                key_label = QLabel(t("plugin.api_keys") + ":")
                key_label.setStyleSheet("color: #888; font-size: 11px;")
                key_layout.addWidget(key_label)
                count_label = QLabel("")
                count_label.setStyleSheet("color: #888; font-size: 11px;")
                key_layout.addWidget(count_label)
                btn_add = QPushButton(t("plugin.add_key"))
                btn_add.setStyleSheet("padding: 3px 10px; border-radius: 4px; font-size: 11px;")
                btn_add.clicked.connect(lambda checked, p=pid: self._add_plugin_key(p))
                key_layout.addWidget(btn_add)
                btn_remove = QPushButton(t("plugin.remove_key"))
                btn_remove.setStyleSheet("padding: 3px 10px; border-radius: 4px; font-size: 11px;")
                btn_remove.clicked.connect(lambda checked, p=pid: self._remove_plugin_key(p))
                key_layout.addWidget(btn_remove)
                key_layout.addStretch()

                self._plugin_key_widgets[pid] = {"count_label": count_label}
                self._refresh_plugin_key_count(pid)
                layout.addWidget(key_container)
        else:
            layout.addWidget(self._hint_label(t("plugin.no_plugins")))

        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        btn_open_plugins = QPushButton(t("plugin.open_folder"))
        btn_open_plugins.setStyleSheet("padding: 6px 14px; border-radius: 6px; font-size: 12px;")
        btn_open_plugins.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_plugins_dir()))))
        layout.addSpacing(6)
        layout.addWidget(btn_open_plugins)

        layout.addStretch()
        return page

    def _build_collab_page(self) -> QWidget:
        page, layout = self._new_page()
        layout.addWidget(self._page_title("협업 (서버 모드)"))

        layout.addWidget(self._section_label("채팅"))
        self.chat_stay_check = QCheckBox("연속 채팅 유지 (Enter 후 입력창 유지)")
        self.chat_stay_check.setChecked(bool(get_setting("chat_stay_open", False)))
        self.chat_stay_check.setStyleSheet(self._check_style())
        layout.addWidget(self.chat_stay_check)
        layout.addWidget(self._hint_label(
            "ON: Enter로 보내도 채팅창이 닫히지 않고 계속 입력 가능 (Esc로 닫기)"))

        layout.addWidget(self._separator())

        layout.addWidget(self._section_label("다른 사용자 커서"))
        self.cursor_show_check = QCheckBox("다른 사용자 커서 표시")
        self.cursor_show_check.setChecked(bool(get_setting("cursor_show", True)))
        self.cursor_show_check.setStyleSheet(self._check_style())
        layout.addWidget(self.cursor_show_check)

        layout.addWidget(self._hint_label("커서 크기 (%)"))
        self.cursor_scale_spin = QSpinBox()
        self.cursor_scale_spin.setRange(50, 250)
        self.cursor_scale_spin.setSingleStep(10)
        self.cursor_scale_spin.setValue(int(round(float(get_setting("cursor_scale", 1.0) or 1.0) * 100)))
        self.cursor_scale_spin.setStyleSheet(self._input_style())
        layout.addWidget(self.cursor_scale_spin)

        layout.addWidget(self._hint_label("커서 불투명도 (%)"))
        self.cursor_opacity_spin = QSpinBox()
        self.cursor_opacity_spin.setRange(20, 100)
        self.cursor_opacity_spin.setSingleStep(5)
        self.cursor_opacity_spin.setValue(int(round(float(get_setting("cursor_opacity", 1.0) or 1.0) * 100)))
        self.cursor_opacity_spin.setStyleSheet(self._input_style())
        layout.addWidget(self.cursor_opacity_spin)

        layout.addStretch()
        return page

    def _build_advanced_page(self) -> QWidget:
        page, layout = self._new_page()
        layout.addWidget(self._page_title("고급"))

        layout.addWidget(self._section_label("입력"))
        self.toggle_mode_check = QCheckBox("토글 모드 (터치패드용)")
        self.toggle_mode_check.setChecked(bool(get_setting("toggle_mode", False)))
        self.toggle_mode_check.setStyleSheet(self._check_style())
        layout.addWidget(self.toggle_mode_check)
        layout.addWidget(self._hint_label(
            "OFF: TAB/Space 홀드 방식 (마우스용)\n"
            "ON: TAB/Space 토글 방식 (터치패드용)"))

        layout.addWidget(self._separator())

        layout.addWidget(self._section_label("진단"))
        self.crash_report_check = QCheckBox("크래시·오류 보고를 서버로 전송")
        self.crash_report_check.setChecked(bool(get_setting("crash_report_enabled", True)))
        self.crash_report_check.setStyleSheet(self._check_style())
        layout.addWidget(self.crash_report_check)
        layout.addWidget(self._hint_label(
            "앱이 크래시하거나 처리되지 않은 오류가 나면 그 스택트레이스를\n"
            "접속한 서버로 보내 운영자가 원인을 파악하게 돕습니다(서버 미접속 시 다음 접속 때 전송)."))

        layout.addWidget(self._separator())

        self.dev_check = QCheckBox(t("settings.developer_mode"))
        self.dev_check.setChecked(is_developer_mode())
        self.dev_check.setStyleSheet("""
            QCheckBox { color: #ddd; font-size: 14px; font-weight: bold; spacing: 8px; }
            QCheckBox::indicator {
                width: 20px; height: 20px;
                border: 2px solid #555; border-radius: 4px; background-color: #2d2d2d;
            }
            QCheckBox::indicator:checked { background-color: #0d6efd; border-color: #3d8bfd; }
        """)
        layout.addWidget(self.dev_check)
        layout.addWidget(self._hint_label(t("settings.developer_mode_hint")))

        layout.addWidget(self._separator())

        self.exp_check = QCheckBox(t("settings.experimental_mode"))
        self.exp_check.setChecked(is_experimental_mode())
        self.exp_check.setStyleSheet("""
            QCheckBox { color: #f0ad4e; font-size: 14px; font-weight: bold; spacing: 8px; }
            QCheckBox::indicator {
                width: 20px; height: 20px;
                border: 2px solid #555; border-radius: 4px; background-color: #2d2d2d;
            }
            QCheckBox::indicator:checked { background-color: #f0ad4e; border-color: #f5c36e; }
        """)
        layout.addWidget(self.exp_check)
        layout.addWidget(self._hint_label(t("settings.experimental_mode_hint")))

        layout.addStretch()
        return page

    # ====================================================================
    # 저장
    # ====================================================================
    def _save(self):
        """설정 저장"""
        if self._raw_keys:
            save_api_keys(self._raw_keys)
            self.api_keys_changed.emit()

        set_default_model(self.model_combo.currentData())
        set_recent_boards_count(self.recent_spin.value())
        set_board_size(self.board_size_spin.value())
        set_language(self.lang_combo.currentData())

        new_dev = self.dev_check.isChecked()
        old_dev = is_developer_mode()
        set_developer_mode(new_dev)
        if new_dev != old_dev:
            self.developer_mode_changed.emit(new_dev)

        new_exp = self.exp_check.isChecked()
        old_exp = is_experimental_mode()
        set_experimental_mode(new_exp)
        if new_exp != old_exp:
            self.experimental_mode_changed.emit(new_exp)

        set_setting("toggle_mode", self.toggle_mode_check.isChecked())

        # 크래시·오류 보고 활성여부 → 리포터에 즉시 반영
        crash_on = self.crash_report_check.isChecked()
        set_setting("crash_report_enabled", crash_on)
        try:
            from v import crash_reporter
            crash_reporter.set_context(enabled=crash_on)
        except Exception:
            pass

        # 협업(서버 모드) 설정
        set_setting("chat_stay_open", self.chat_stay_check.isChecked())
        set_setting("cursor_show", self.cursor_show_check.isChecked())
        set_setting("cursor_scale", self.cursor_scale_spin.value() / 100.0)
        set_setting("cursor_opacity", self.cursor_opacity_spin.value() / 100.0)

        # 플러그인 활성 상태 저장
        enabled = [pid for pid, cb in self._plugin_checks.items() if cb.isChecked()]
        set_enabled_plugins(enabled)

        # 플러그인별 API 키 저장
        for pid, keys in self._plugin_key_data.items():
            save_plugin_api_keys(pid, keys)

        self.accept()

    # ====================================================================
    # API 키 / 플러그인 키 관리
    # ====================================================================
    def _refresh_key_list(self):
        """키 리스트 UI 갱신"""
        self.key_list.clear()
        for key in self._raw_keys:
            if len(key) > 16:
                masked = f"{key[:8]}...{key[-4:]}"
            elif len(key) > 8:
                masked = f"{key[:4]}...{key[-2:]}"
            else:
                masked = f"{key[:2]}{'*' * max(0, len(key) - 2)}"
            self.key_list.addItem(masked)
        self._key_count_label.setText(f"{len(self._raw_keys)}개")

    def _add_api_key(self):
        """키 추가 다이얼로그"""
        key, ok = QInputDialog.getText(
            self, "API Key", "Gemini API Key:",
            QLineEdit.EchoMode.Normal,
        )
        if not ok or not key.strip():
            return
        key = key.strip()
        if not self._validate_api_key(key):
            QMessageBox.warning(self, "Error", t("error.invalid_api_key_message"))
            return
        if key in self._raw_keys:
            QMessageBox.warning(self, "Error", t("plugin.duplicate_key"))
            return
        self._raw_keys.append(key)
        self._refresh_key_list()

    def _remove_api_key(self):
        """선택된 키 삭제"""
        row = self.key_list.currentRow()
        if row < 0:
            return
        self._raw_keys.pop(row)
        self._refresh_key_list()

    def _refresh_plugin_key_count(self, plugin_id: str):
        """플러그인 키 개수 라벨 갱신"""
        widgets = self._plugin_key_widgets.get(plugin_id)
        if not widgets:
            return
        count = len(self._plugin_key_data.get(plugin_id, []))
        widgets["count_label"].setText(
            t("plugin.key_count").replace("{count}", str(count))
        )

    def _add_plugin_key(self, plugin_id: str):
        """플러그인 API 키 추가"""
        key, ok = QInputDialog.getText(
            self, "API Key", t("plugin.add_key_prompt"),
            QLineEdit.EchoMode.Normal,
        )
        if not ok or not key.strip():
            return
        key = key.strip()
        keys = self._plugin_key_data.get(plugin_id, [])
        if key in keys:
            QMessageBox.warning(self, "Error", t("plugin.duplicate_key"))
            return
        keys.append(key)
        self._plugin_key_data[plugin_id] = keys
        self._refresh_plugin_key_count(plugin_id)

    def _remove_plugin_key(self, plugin_id: str):
        """플러그인 API 키 삭제 (마지막 키 삭제)"""
        keys = self._plugin_key_data.get(plugin_id, [])
        if not keys:
            return
        keys.pop()
        self._plugin_key_data[plugin_id] = keys
        self._refresh_plugin_key_count(plugin_id)

    @staticmethod
    def _validate_api_key(key: str) -> bool:
        """API 키 형식 검증"""
        # Gemini API 키는 "AIza"로 시작, 최소 39자
        if not key.startswith("AIza"):
            return False
        if len(key) < 39:
            return False
        import re
        if not re.match(r'^[A-Za-z0-9_-]+$', key):
            return False
        return True

    # ====================================================================
    # 스타일 헬퍼
    # ====================================================================
    @staticmethod
    def _page_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            "color: #fff; font-size: 19px; font-weight: bold; margin-bottom: 6px;")
        return label

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #fff; font-size: 14px; font-weight: bold;")
        return label

    @staticmethod
    def _hint_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 4px;")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #333;")
        return line

    @staticmethod
    def _check_style() -> str:
        return """
            QCheckBox { color: #ddd; font-size: 13px; spacing: 8px; }
            QCheckBox::indicator {
                width: 20px; height: 20px;
                border: 2px solid #555; border-radius: 4px;
                background-color: #2d2d2d;
            }
            QCheckBox::indicator:checked {
                background-color: #0d6efd; border-color: #4a9eff;
            }
        """

    @staticmethod
    def _input_style() -> str:
        return """
            QLineEdit, QSpinBox {
                background-color: #2d2d2d;
                color: #ddd;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
            }
        """

    @staticmethod
    def _combo_style() -> str:
        return """
            QComboBox {
                background-color: #2d2d2d;
                color: #ddd;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
            }
            QComboBox::drop-down {
                border: none;
                padding-right: 10px;
            }
            QComboBox QAbstractItemView {
                background-color: #2d2d2d;
                color: #ddd;
                border: 1px solid #444;
                selection-background-color: #0d6efd;
            }
        """
