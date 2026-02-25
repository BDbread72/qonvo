"""
토론 실행 워커
- 순차적 AI 호출
- 스트리밍 지원
- 중재자 요약
"""

from dataclasses import dataclass
from PyQt6.QtCore import QThread, pyqtSignal

from m.gemini import GeminiProvider
from v.round_table.personas import Persona
from v.round_table.config_dialog import RoundTableConfig, DiscussionStep
from v.settings import get_setting


@dataclass
class TurnInfo:
    """턴 정보"""
    step_index: int
    step_name: str
    round_num: int
    participant_index: int
    persona: Persona
    is_moderator: bool = False


class DiscussionWorker(QThread):
    """토론 실행 워커"""

    # 시그널
    turn_started = pyqtSignal(object)  # TurnInfo
    token_received = pyqtSignal(str)   # 스트리밍 토큰
    turn_finished = pyqtSignal(str)    # 완료된 응답
    step_changed = pyqtSignal(str, int)  # (step_name, round_num)
    discussion_finished = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, config: RoundTableConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._stop_requested = False
        self._pause_requested = False
        self._skip_to_next_step = False

        # 대화 히스토리 (전체 컨텍스트)
        self.conversation_history: list[dict] = []

        # API 키
        self.api_key = get_setting("api_key", "")
        self.default_model = config.default_model or get_setting("default_model", "gemini-2.0-flash")

    def stop(self):
        """중단 요청"""
        self._stop_requested = True

    def pause(self):
        """일시정지"""
        self._pause_requested = True

    def resume(self):
        """재개"""
        self._pause_requested = False

    def skip_to_next_step(self):
        """다음 스텝으로 건너뛰기"""
        self._skip_to_next_step = True

    def run(self):
        """토론 실행"""
        try:
            self._run_discussion()
        except Exception as e:
            self.error_occurred.emit(str(e))

    def _run_discussion(self):
        """토론 메인 루프"""
        # 초기 컨텍스트 설정
        topic_context = f"토론 주제: {self.config.topic}\n\n참가자: " + ", ".join(
            f"{p.icon} {p.name}" for p in self.config.participants
        )
        self.conversation_history.append({
            "role": "user",
            "content": topic_context
        })

        # 각 스텝 실행
        for step_idx, step in enumerate(self.config.steps):
            if self._stop_requested:
                break

            self._run_step(step_idx, step)

        # 최종 중재자 요약
        if self.config.moderator_enabled and self.config.moderator and not self._stop_requested:
            self._run_moderator_summary("최종 요약", final=True)

        self.discussion_finished.emit()

    def _run_step(self, step_idx: int, step: DiscussionStep):
        """단일 스텝 실행"""
        for round_num in range(1, step.max_rounds + 1):
            if self._stop_requested:
                break
            if self._skip_to_next_step:
                self._skip_to_next_step = False
                break

            self.step_changed.emit(step.name, round_num)

            # 스텝 프롬프트 추가
            step_prompt = f"\n\n[{step.name} - Round {round_num}]\n{step.prompt}"
            self.conversation_history.append({
                "role": "user",
                "content": step_prompt
            })

            # 각 참가자 순차 실행
            for p_idx, participant in enumerate(self.config.participants):
                if self._stop_requested:
                    break

                # 일시정지 대기
                while self._pause_requested and not self._stop_requested:
                    self.msleep(100)

                self._run_participant_turn(step_idx, step, round_num, p_idx, participant)

            # 라운드 후 중재자 요약
            if self.config.moderator_enabled and self.config.moderator_after_each_round and self.config.moderator:
                if not self._stop_requested:
                    self._run_moderator_summary(f"{step.name} R{round_num} 요약")

    def _run_participant_turn(self, step_idx: int, step: DiscussionStep,
                               round_num: int, p_idx: int, participant: Persona):
        """참가자 턴 실행"""
        turn_info = TurnInfo(
            step_index=step_idx,
            step_name=step.name,
            round_num=round_num,
            participant_index=p_idx,
            persona=participant,
            is_moderator=False
        )
        self.turn_started.emit(turn_info)

        # 시스템 프롬프트 + 대화 히스토리
        model = participant.model or self.default_model
        response = self._call_api(participant.system_prompt, model)

        if response:
            # 히스토리에 추가
            self.conversation_history.append({
                "role": "model",
                "content": f"[{participant.name}]: {response}"
            })
            self.turn_finished.emit(response)

    def _run_moderator_summary(self, label: str, final: bool = False):
        """중재자 요약 실행"""
        moderator = self.config.moderator
        turn_info = TurnInfo(
            step_index=-1,
            step_name=label,
            round_num=0,
            participant_index=-1,
            persona=moderator,
            is_moderator=True
        )
        self.turn_started.emit(turn_info)

        # 요약 프롬프트
        if final:
            summary_prompt = "지금까지의 전체 토론을 종합하여 최종 결론, 주요 합의점, 남은 이견을 정리해주세요."
        else:
            summary_prompt = "지금까지의 토론을 간단히 요약하고, 주요 합의점과 이견을 정리해주세요."

        self.conversation_history.append({
            "role": "user",
            "content": f"\n\n[중재자 요청] {summary_prompt}"
        })

        model = moderator.model or self.default_model
        response = self._call_api(moderator.system_prompt, model)

        if response:
            self.conversation_history.append({
                "role": "model",
                "content": f"[{moderator.name} - 중재자]: {response}"
            })
            self.turn_finished.emit(response)

    def _call_api(self, system_prompt: str, model: str) -> str:
        """API 호출 (스트리밍)"""
        if not self.api_key:
            self.error_occurred.emit("API 키가 설정되지 않았습니다.")
            return ""

        try:
            provider = GeminiProvider(self.api_key)

            # 대화 히스토리 변환
            messages = []
            for msg in self.conversation_history:
                messages.append({
                    "role": msg["role"],
                    "parts": [{"text": msg["content"]}]
                })

            # 스트리밍 호출
            full_response = ""
            for chunk in provider.stream_chat(
                messages=messages,
                model=model,
                system_prompt=system_prompt
            ):
                if self._stop_requested:
                    break
                full_response += chunk
                self.token_received.emit(chunk)

            return full_response

        except Exception as e:
            self.error_occurred.emit(f"API 오류: {str(e)}")
            return ""
