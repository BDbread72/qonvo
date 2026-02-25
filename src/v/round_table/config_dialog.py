"""
라운드 테이블 세션 설정 다이얼로그
- 참가자(페르소나) 선택
- 중재자 설정
- 토론 스텝 구성
"""

from dataclasses import dataclass, field
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QLineEdit, QTextEdit,
    QGroupBox, QComboBox, QCheckBox, QSpinBox, QTabWidget,
    QWidget, QScrollArea, QFrame, QColorDialog, QSplitter,
    QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from m.gemini import GeminiProvider
from v.round_table.personas import Persona, get_persona_manager


@dataclass
class DiscussionStep:
    """토론 스텝"""
    name: str
    prompt: str
    max_rounds: int = 1


@dataclass
class RoundTableConfig:
    """라운드 테이블 세션 설정"""
    topic: str = ""
    participants: list[Persona] = field(default_factory=list)
    moderator: Persona | None = None
    moderator_enabled: bool = True
    moderator_after_each_round: bool = True
    steps: list[DiscussionStep] = field(default_factory=list)
    default_model: str = ""

    def __post_init__(self):
        if not self.steps:
            self.steps = [
                DiscussionStep("브레인스토밍", "자유롭게 아이디어를 제시하세요.", 1),
                DiscussionStep("상호검토", "다른 참가자의 의견을 검토하고 피드백하세요.", 2),
                DiscussionStep("최종합의", "논의를 종합하여 결론을 도출하세요.", 1),
            ]


class PersonaListItem(QListWidgetItem):
    """페르소나 리스트 아이템"""

    def __init__(self, persona: Persona):
        super().__init__(f"{persona.icon} {persona.name}")
        self.persona = persona
        self.setForeground(QColor(persona.color))


class PersonaEditDialog(QDialog):
    """페르소나 편집 다이얼로그"""

    def __init__(self, parent=None, persona: Persona = None):
        super().__init__(parent)
        self.persona = persona
        self.selected_color = persona.color if persona else "#3498db"
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("페르소나 편집" if self.persona else "새 페르소나")
        self.setMinimumSize(400, 400)
        layout = QVBoxLayout(self)

        # 이름
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("이름:"))
        self.name_edit = QLineEdit(self.persona.name if self.persona else "")
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        # 아이콘
        icon_layout = QHBoxLayout()
        icon_layout.addWidget(QLabel("아이콘:"))
        self.icon_edit = QLineEdit(self.persona.icon if self.persona else "🤖")
        self.icon_edit.setMaximumWidth(60)
        icon_layout.addWidget(self.icon_edit)
        icon_layout.addStretch()
        layout.addLayout(icon_layout)

        # 색상
        color_layout = QHBoxLayout()
        color_layout.addWidget(QLabel("색상:"))
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(40, 25)
        self._update_color_btn()
        self.color_btn.clicked.connect(self._pick_color)
        color_layout.addWidget(self.color_btn)
        color_layout.addStretch()
        layout.addLayout(color_layout)

        # 모델 (선택사항)
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("모델:"))
        self.model_combo = QComboBox()
        self.model_combo.addItem("(기본 모델 사용)", "")
        for model in GeminiProvider.get_available_models():
            self.model_combo.addItem(model, model)
        if self.persona and self.persona.model:
            idx = self.model_combo.findData(self.persona.model)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        model_layout.addWidget(self.model_combo)
        layout.addLayout(model_layout)

        # 시스템 프롬프트
        layout.addWidget(QLabel("시스템 프롬프트:"))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlainText(self.persona.system_prompt if self.persona else "")
        self.prompt_edit.setPlaceholderText("이 페르소나의 성격과 역할을 설명하세요...")
        layout.addWidget(self.prompt_edit)

        # 버튼
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("저장")
        save_btn.clicked.connect(self._save)
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

    def _update_color_btn(self):
        self.color_btn.setStyleSheet(f"background-color: {self.selected_color}; border: 1px solid #555;")

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self.selected_color), self)
        if color.isValid():
            self.selected_color = color.name()
            self._update_color_btn()

    def _save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "오류", "이름을 입력하세요.")
            return

        icon = self.icon_edit.text().strip() or "🤖"
        prompt = self.prompt_edit.toPlainText().strip()
        model = self.model_combo.currentData()

        if self.persona:
            # 기존 페르소나 업데이트
            self.persona.name = name
            self.persona.icon = icon
            self.persona.color = self.selected_color
            self.persona.system_prompt = prompt
            self.persona.model = model
            self.result_persona = self.persona
        else:
            # 새 페르소나
            self.result_persona = Persona(
                id="",
                name=name,
                icon=icon,
                system_prompt=prompt,
                color=self.selected_color,
                model=model
            )

        self.accept()


class RoundTableConfigDialog(QDialog):
    """라운드 테이블 세션 설정 다이얼로그"""

    def __init__(self, parent=None, config: RoundTableConfig = None):
        super().__init__(parent)
        self.config = config or RoundTableConfig()
        self.persona_manager = get_persona_manager()
        self._init_ui()
        self._load_config()

    def _init_ui(self):
        self.setWindowTitle("라운드 테이블 설정")
        self.setMinimumSize(800, 600)
        layout = QVBoxLayout(self)

        # 탭 위젯
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # 탭 1: 참가자 설정
        tabs.addTab(self._create_participants_tab(), "참가자")

        # 탭 2: 중재자 설정
        tabs.addTab(self._create_moderator_tab(), "중재자")

        # 탭 3: 토론 스텝
        tabs.addTab(self._create_steps_tab(), "토론 스텝")

        # 주제 입력
        topic_layout = QHBoxLayout()
        topic_layout.addWidget(QLabel("토론 주제:"))
        self.topic_edit = QLineEdit()
        self.topic_edit.setPlaceholderText("토론할 주제를 입력하세요...")
        topic_layout.addWidget(self.topic_edit)
        layout.addLayout(topic_layout)

        # 버튼
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        start_btn = QPushButton("토론 시작")
        start_btn.setStyleSheet("background-color: #0d6efd; color: white; font-weight: bold; padding: 8px 16px;")
        start_btn.clicked.connect(self._start)
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(start_btn)
        layout.addLayout(btn_layout)

    def _create_participants_tab(self) -> QWidget:
        """참가자 설정 탭"""
        widget = QWidget()
        layout = QHBoxLayout(widget)

        # 좌측: 사용 가능한 페르소나
        left_group = QGroupBox("사용 가능한 페르소나")
        left_layout = QVBoxLayout(left_group)

        self.available_list = QListWidget()
        self.available_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        left_layout.addWidget(self.available_list)

        # 페르소나 관리 버튼
        persona_btn_layout = QHBoxLayout()
        add_persona_btn = QPushButton("+ 새 페르소나")
        add_persona_btn.clicked.connect(self._add_new_persona)
        edit_persona_btn = QPushButton("편집")
        edit_persona_btn.clicked.connect(self._edit_persona)
        delete_persona_btn = QPushButton("삭제")
        delete_persona_btn.clicked.connect(self._delete_persona)
        persona_btn_layout.addWidget(add_persona_btn)
        persona_btn_layout.addWidget(edit_persona_btn)
        persona_btn_layout.addWidget(delete_persona_btn)
        left_layout.addLayout(persona_btn_layout)

        layout.addWidget(left_group)

        # 중앙: 추가/제거 버튼
        center_layout = QVBoxLayout()
        center_layout.addStretch()
        add_btn = QPushButton("→")
        add_btn.setFixedWidth(40)
        add_btn.clicked.connect(self._add_participant)
        remove_btn = QPushButton("←")
        remove_btn.setFixedWidth(40)
        remove_btn.clicked.connect(self._remove_participant)
        center_layout.addWidget(add_btn)
        center_layout.addWidget(remove_btn)
        center_layout.addStretch()
        layout.addLayout(center_layout)

        # 우측: 선택된 참가자
        right_group = QGroupBox("토론 참가자")
        right_layout = QVBoxLayout(right_group)

        self.selected_list = QListWidget()
        self.selected_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        right_layout.addWidget(self.selected_list)

        # 순서 변경 버튼
        order_btn_layout = QHBoxLayout()
        up_btn = QPushButton("↑")
        up_btn.setFixedWidth(40)
        up_btn.clicked.connect(self._move_up)
        down_btn = QPushButton("↓")
        down_btn.setFixedWidth(40)
        down_btn.clicked.connect(self._move_down)
        order_btn_layout.addWidget(up_btn)
        order_btn_layout.addWidget(down_btn)
        order_btn_layout.addStretch()
        right_layout.addLayout(order_btn_layout)

        layout.addWidget(right_group)

        return widget

    def _create_moderator_tab(self) -> QWidget:
        """중재자 설정 탭"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 중재자 활성화
        self.moderator_enabled = QCheckBox("중재자 사용")
        self.moderator_enabled.stateChanged.connect(self._on_moderator_toggle)
        layout.addWidget(self.moderator_enabled)

        # 중재자 설정 그룹
        self.moderator_group = QGroupBox("중재자 설정")
        mod_layout = QVBoxLayout(self.moderator_group)

        # 중재자 페르소나 선택
        persona_layout = QHBoxLayout()
        persona_layout.addWidget(QLabel("중재자 페르소나:"))
        self.moderator_combo = QComboBox()
        persona_layout.addWidget(self.moderator_combo)
        mod_layout.addLayout(persona_layout)

        # 모델 선택
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("모델:"))
        self.moderator_model = QComboBox()
        self.moderator_model.addItem("(기본 모델)", "")
        for model in GeminiProvider.get_available_models():
            self.moderator_model.addItem(model, model)
        model_layout.addWidget(self.moderator_model)
        mod_layout.addLayout(model_layout)

        # 요약 옵션
        self.after_each_round = QCheckBox("매 라운드 후 요약")
        mod_layout.addWidget(self.after_each_round)

        layout.addWidget(self.moderator_group)
        layout.addStretch()

        return widget

    def _create_steps_tab(self) -> QWidget:
        """토론 스텝 설정 탭"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 스텝 리스트
        self.steps_list = QListWidget()
        self.steps_list.currentRowChanged.connect(self._on_step_selected)
        layout.addWidget(self.steps_list)

        # 스텝 관리 버튼
        step_btn_layout = QHBoxLayout()
        add_step_btn = QPushButton("+ 스텝 추가")
        add_step_btn.clicked.connect(self._add_step)
        remove_step_btn = QPushButton("스텝 삭제")
        remove_step_btn.clicked.connect(self._remove_step)
        step_btn_layout.addWidget(add_step_btn)
        step_btn_layout.addWidget(remove_step_btn)
        step_btn_layout.addStretch()
        layout.addLayout(step_btn_layout)

        # 스텝 편집 영역
        edit_group = QGroupBox("스텝 편집")
        edit_layout = QVBoxLayout(edit_group)

        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("스텝 이름:"))
        self.step_name = QLineEdit()
        self.step_name.textChanged.connect(self._on_step_name_changed)
        name_layout.addWidget(self.step_name)
        edit_layout.addLayout(name_layout)

        rounds_layout = QHBoxLayout()
        rounds_layout.addWidget(QLabel("반복 횟수:"))
        self.step_rounds = QSpinBox()
        self.step_rounds.setRange(1, 10)
        self.step_rounds.valueChanged.connect(self._on_step_rounds_changed)
        rounds_layout.addWidget(self.step_rounds)
        rounds_layout.addStretch()
        edit_layout.addLayout(rounds_layout)

        edit_layout.addWidget(QLabel("프롬프트:"))
        self.step_prompt = QTextEdit()
        self.step_prompt.textChanged.connect(self._on_step_prompt_changed)
        self.step_prompt.setPlaceholderText("이 스텝에서 참가자에게 전달될 지시사항...")
        edit_layout.addWidget(self.step_prompt)

        layout.addWidget(edit_group)

        return widget

    def _load_config(self):
        """설정 로드"""
        # 사용 가능한 페르소나 로드
        self._refresh_available_list()

        # 중재자 콤보박스 채우기
        default_mod = self.persona_manager.get_default_moderator()
        self.moderator_combo.addItem(f"{default_mod.icon} {default_mod.name}", default_mod.id)
        for p in self.persona_manager.get_all_personas():
            self.moderator_combo.addItem(f"{p.icon} {p.name}", p.id)

        # 기존 설정 적용
        self.topic_edit.setText(self.config.topic)

        for p in self.config.participants:
            self.selected_list.addItem(PersonaListItem(p))

        self.moderator_enabled.setChecked(self.config.moderator_enabled)
        self.after_each_round.setChecked(self.config.moderator_after_each_round)
        self._on_moderator_toggle(self.config.moderator_enabled)

        # 스텝 로드
        for step in self.config.steps:
            self.steps_list.addItem(step.name)

    def _refresh_available_list(self):
        """사용 가능한 페르소나 리스트 새로고침"""
        self.available_list.clear()
        for p in self.persona_manager.get_all_personas():
            self.available_list.addItem(PersonaListItem(p))

    def _add_new_persona(self):
        """새 페르소나 추가"""
        dialog = PersonaEditDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.persona_manager.add_custom_persona(dialog.result_persona)
            self._refresh_available_list()

    def _edit_persona(self):
        """페르소나 편집"""
        item = self.available_list.currentItem()
        if not item or not isinstance(item, PersonaListItem):
            return
        if item.persona.is_builtin:
            QMessageBox.warning(self, "편집 불가", "기본 페르소나는 편집할 수 없습니다.")
            return

        dialog = PersonaEditDialog(self, item.persona)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.persona_manager.update_custom_persona(dialog.result_persona)
            self._refresh_available_list()

    def _delete_persona(self):
        """페르소나 삭제"""
        item = self.available_list.currentItem()
        if not item or not isinstance(item, PersonaListItem):
            return
        if item.persona.is_builtin:
            QMessageBox.warning(self, "삭제 불가", "기본 페르소나는 삭제할 수 없습니다.")
            return

        if QMessageBox.question(self, "삭제 확인", f"'{item.persona.name}' 페르소나를 삭제하시겠습니까?") == QMessageBox.StandardButton.Yes:
            self.persona_manager.delete_custom_persona(item.persona.id)
            self._refresh_available_list()

    def _add_participant(self):
        """참가자 추가"""
        for item in self.available_list.selectedItems():
            if isinstance(item, PersonaListItem):
                self.selected_list.addItem(PersonaListItem(item.persona))

    def _remove_participant(self):
        """참가자 제거"""
        for item in self.selected_list.selectedItems():
            self.selected_list.takeItem(self.selected_list.row(item))

    def _move_up(self):
        """참가자 위로 이동"""
        row = self.selected_list.currentRow()
        if row > 0:
            item = self.selected_list.takeItem(row)
            self.selected_list.insertItem(row - 1, item)
            self.selected_list.setCurrentRow(row - 1)

    def _move_down(self):
        """참가자 아래로 이동"""
        row = self.selected_list.currentRow()
        if row < self.selected_list.count() - 1:
            item = self.selected_list.takeItem(row)
            self.selected_list.insertItem(row + 1, item)
            self.selected_list.setCurrentRow(row + 1)

    def _on_moderator_toggle(self, state):
        """중재자 토글"""
        self.moderator_group.setEnabled(bool(state))

    def _on_step_selected(self, row):
        """스텝 선택"""
        if 0 <= row < len(self.config.steps):
            step = self.config.steps[row]
            self.step_name.blockSignals(True)
            self.step_rounds.blockSignals(True)
            self.step_prompt.blockSignals(True)

            self.step_name.setText(step.name)
            self.step_rounds.setValue(step.max_rounds)
            self.step_prompt.setPlainText(step.prompt)

            self.step_name.blockSignals(False)
            self.step_rounds.blockSignals(False)
            self.step_prompt.blockSignals(False)

    def _on_step_name_changed(self, text):
        """스텝 이름 변경"""
        row = self.steps_list.currentRow()
        if 0 <= row < len(self.config.steps):
            self.config.steps[row].name = text
            self.steps_list.item(row).setText(text)

    def _on_step_rounds_changed(self, value):
        """스텝 라운드 수 변경"""
        row = self.steps_list.currentRow()
        if 0 <= row < len(self.config.steps):
            self.config.steps[row].max_rounds = value

    def _on_step_prompt_changed(self):
        """스텝 프롬프트 변경"""
        row = self.steps_list.currentRow()
        if 0 <= row < len(self.config.steps):
            self.config.steps[row].prompt = self.step_prompt.toPlainText()

    def _add_step(self):
        """스텝 추가"""
        step = DiscussionStep(f"스텝 {len(self.config.steps) + 1}", "", 1)
        self.config.steps.append(step)
        self.steps_list.addItem(step.name)
        self.steps_list.setCurrentRow(len(self.config.steps) - 1)

    def _remove_step(self):
        """스텝 삭제"""
        row = self.steps_list.currentRow()
        if 0 <= row < len(self.config.steps):
            del self.config.steps[row]
            self.steps_list.takeItem(row)

    def _start(self):
        """토론 시작"""
        # 유효성 검사
        topic = self.topic_edit.text().strip()
        if not topic:
            QMessageBox.warning(self, "오류", "토론 주제를 입력하세요.")
            return

        if self.selected_list.count() < 2:
            QMessageBox.warning(self, "오류", "최소 2명 이상의 참가자가 필요합니다.")
            return

        if not self.config.steps:
            QMessageBox.warning(self, "오류", "최소 1개 이상의 토론 스텝이 필요합니다.")
            return

        # 설정 수집
        self.config.topic = topic
        self.config.participants = []
        for i in range(self.selected_list.count()):
            item = self.selected_list.item(i)
            if isinstance(item, PersonaListItem):
                self.config.participants.append(item.persona)

        self.config.moderator_enabled = self.moderator_enabled.isChecked()
        self.config.moderator_after_each_round = self.after_each_round.isChecked()

        if self.config.moderator_enabled:
            mod_id = self.moderator_combo.currentData()
            if mod_id == "moderator":
                self.config.moderator = self.persona_manager.get_default_moderator()
            else:
                self.config.moderator = self.persona_manager.get_persona_by_id(mod_id)
            # 모델 오버라이드
            if self.config.moderator and self.moderator_model.currentData():
                self.config.moderator.model = self.moderator_model.currentData()

        self.accept()

    def get_config(self) -> RoundTableConfig:
        """설정 반환"""
        return self.config
