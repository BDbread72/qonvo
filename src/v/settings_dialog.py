"""
설정 다이얼로그
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QComboBox, QSpinBox, QCheckBox,
    QPushButton, QFrame, QListWidget, QListWidgetItem,
    QMessageBox, QInputDialog, QWidget, QScrollArea,
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
    """환경 설정 다이얼로그"""

    developer_mode_changed = pyqtSignal(bool)
    experimental_mode_changed = pyqtSignal(bool)
    api_keys_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings.title"))
        self.setMinimumWidth(500)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; }
            QLabel { color: #ddd; }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 스크롤 영역
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea { background-color: #1e1e1e; border: none; }
            QScrollBar:vertical {
                background: #1e1e1e; width: 8px;
            }
            QScrollBar::handle:vertical {
                background: #444; border-radius: 4px; min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        # === API 키 관리 ===
        layout.addWidget(self._section_label(t("settings.api_key")))
        layout.addWidget(self._hint_label(
            "여러 키를 추가하면 요청이 자동 분산됩니다"
        ))

        self._raw_keys: list[str] = get_api_keys()

        self.key_list = QListWidget()
        self.key_list.setFixedHeight(100)
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
        btn_add_key.setStyleSheet(
            "padding: 6px 14px; border-radius: 6px; font-size: 12px;"
        )
        btn_add_key.clicked.connect(self._add_api_key)
        key_btn_row.addWidget(btn_add_key)

        btn_remove_key = QPushButton("- 키 삭제")
        btn_remove_key.setStyleSheet(
            "padding: 6px 14px; border-radius: 6px; font-size: 12px;"
        )
        btn_remove_key.clicked.connect(self._remove_api_key)
        key_btn_row.addWidget(btn_remove_key)

        key_btn_row.addStretch()
        key_btn_row.addWidget(self._key_count_label)

        layout.addLayout(key_btn_row)

        layout.addWidget(self._separator())

        # === 기본 모델 ===
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

        # === 최근 보드 개수 ===
        layout.addWidget(self._section_label(t("settings.recent_count")))
        layout.addWidget(self._hint_label(t("settings.recent_count_hint")))

        self.recent_spin = QSpinBox()
        self.recent_spin.setRange(1, 20)
        self.recent_spin.setValue(get_recent_boards_count())
        self.recent_spin.setStyleSheet(self._input_style())
        layout.addWidget(self.recent_spin)

        layout.addWidget(self._separator())

        # === 보드 크기 ===
        layout.addWidget(self._section_label(t("settings.board_size")))
        layout.addWidget(self._hint_label(t("settings.board_size_hint")))

        self.board_size_spin = QSpinBox()
        self.board_size_spin.setRange(5000, 100000)
        self.board_size_spin.setSingleStep(5000)
        self.board_size_spin.setValue(get_board_size())
        self.board_size_spin.setStyleSheet(self._input_style())
        layout.addWidget(self.board_size_spin)

        layout.addWidget(self._separator())

        # === 언어 ===
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

        layout.addWidget(self._separator())

        # === 개발자 모드 ===
        self.dev_check = QCheckBox(t("settings.developer_mode"))
        self.dev_check.setChecked(is_developer_mode())
        self.dev_check.setStyleSheet("""
            QCheckBox {
                color: #ddd; font-size: 14px; font-weight: bold;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 20px; height: 20px;
                border: 2px solid #555; border-radius: 4px;
                background-color: #2d2d2d;
            }
            QCheckBox::indicator:checked {
                background-color: #0d6efd; border-color: #3d8bfd;
            }
        """)
        layout.addWidget(self.dev_check)
        layout.addWidget(self._hint_label(t("settings.developer_mode_hint")))

        layout.addWidget(self._separator())

        # === 실험적 기능 ===
        self.exp_check = QCheckBox(t("settings.experimental_mode"))
        self.exp_check.setChecked(is_experimental_mode())
        self.exp_check.setStyleSheet("""
            QCheckBox {
                color: #f0ad4e; font-size: 14px; font-weight: bold;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 20px; height: 20px;
                border: 2px solid #555; border-radius: 4px;
                background-color: #2d2d2d;
            }
            QCheckBox::indicator:checked {
                background-color: #f0ad4e; border-color: #f5c36e;
            }
        """)
        layout.addWidget(self.exp_check)
        layout.addWidget(self._hint_label(t("settings.experimental_mode_hint")))

        layout.addWidget(self._separator())

        # === 플러그인 ===
        layout.addWidget(self._section_label(t("plugin.section_title")))
        layout.addWidget(self._hint_label(t("plugin.hint")))

        from v.model_plugin import PluginRegistry, get_plugins_dir
        discovered = PluginRegistry.instance().get_discovered_plugins()

        self._plugin_checks: dict[str, QCheckBox] = {}
        self._plugin_key_data: dict[str, list[str]] = {}  # plugin_id -> [raw keys]
        self._plugin_key_widgets: dict[str, dict] = {}    # plugin_id -> {list, count_label}

        if discovered:
            for info in discovered:
                models_str = ", ".join(info["models"].values()) if info["models"] else "no models"
                cb = QCheckBox(f'{info["name"]} v{info["version"]}  ({models_str})')
                cb.setChecked(info["enabled"])
                cb.setStyleSheet("""
                    QCheckBox { color: #ddd; font-size: 12px; spacing: 8px; }
                    QCheckBox::indicator {
                        width: 18px; height: 18px;
                        border: 2px solid #555; border-radius: 4px;
                        background-color: #2d2d2d;
                    }
                    QCheckBox::indicator:checked {
                        background-color: #0d6efd; border-color: #3d8bfd;
                    }
                """)
                layout.addWidget(cb)
                self._plugin_checks[info["id"]] = cb

                # 플러그인 API 키 UI
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
                btn_add.setStyleSheet(
                    "padding: 3px 10px; border-radius: 4px; font-size: 11px;"
                )
                btn_add.clicked.connect(
                    lambda checked, p=pid: self._add_plugin_key(p)
                )
                key_layout.addWidget(btn_add)

                btn_remove = QPushButton(t("plugin.remove_key"))
                btn_remove.setStyleSheet(
                    "padding: 3px 10px; border-radius: 4px; font-size: 11px;"
                )
                btn_remove.clicked.connect(
                    lambda checked, p=pid: self._remove_plugin_key(p)
                )
                key_layout.addWidget(btn_remove)

                key_layout.addStretch()

                self._plugin_key_widgets[pid] = {
                    "count_label": count_label,
                }
                self._refresh_plugin_key_count(pid)

                layout.addWidget(key_container)
        else:
            layout.addWidget(self._hint_label(t("plugin.no_plugins")))

        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        btn_open_plugins = QPushButton(t("plugin.open_folder"))
        btn_open_plugins.setStyleSheet(
            "padding: 6px 14px; border-radius: 6px; font-size: 12px;"
        )
        btn_open_plugins.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(get_plugins_dir()))
            )
        )
        layout.addWidget(btn_open_plugins)

        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # === 버튼 (스크롤 영역 밖, 하단 고정) ===
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(16, 8, 16, 12)
        btn_cancel = QPushButton(t("button.cancel"))
        btn_cancel.setStyleSheet("padding: 10px 24px;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_layout.addStretch()

        btn_save = QPushButton(t("button.save"))
        btn_save.setStyleSheet(
            "padding: 10px 24px; background-color: #0d6efd; font-weight: bold;"
        )
        btn_save.clicked.connect(self._save)
        btn_layout.addWidget(btn_save)
        outer.addLayout(btn_layout)

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

        # 플러그인 활성 상태 저장
        enabled = [pid for pid, cb in self._plugin_checks.items() if cb.isChecked()]
        set_enabled_plugins(enabled)

        # 플러그인별 API 키 저장
        for pid, keys in self._plugin_key_data.items():
            save_plugin_api_keys(pid, keys)

        self.accept()

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
        # 영숫자, 하이픈, 언더스코어만 허용
        import re
        if not re.match(r'^[A-Za-z0-9_-]+$', key):
            return False
        return True

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #fff; font-size: 14px; font-weight: bold;")
        return label

    @staticmethod
    def _hint_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 4px;")
        return label

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #333;")
        return line

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
