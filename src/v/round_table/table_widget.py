"""
라운드 테이블 시각화 위젯
- 중앙 테이블 + 주변 참가자 배치
- 현재 발언자 하이라이트
- 중재자 상단 표시
"""

import math
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QPainterPath, QFontMetrics

from v.round_table.personas import Persona


class ParticipantNode:
    """참가자 노드 (렌더링용)"""

    def __init__(self, persona: Persona, index: int, total: int, center: QPointF, radius: float):
        self.persona = persona
        self.index = index
        self.is_speaking = False
        self.is_done = False

        # 위치 계산 (원형 배치)
        angle = (2 * math.pi * index / total) - math.pi / 2  # 12시 방향부터 시작
        self.x = center.x() + radius * math.cos(angle)
        self.y = center.y() + radius * math.sin(angle)
        self.size = 60


class RoundTableWidget(QWidget):
    """라운드 테이블 시각화"""

    participant_clicked = pyqtSignal(int)  # 참가자 클릭 시그널

    def __init__(self, parent=None):
        super().__init__(parent)
        self.participants: list[ParticipantNode] = []
        self.moderator: Persona | None = None
        self.current_speaker_index = -1
        self.table_radius = 50
        self.participant_radius = 150

        self.setMinimumSize(400, 400)

    def set_participants(self, participants: list[Persona], moderator: Persona = None):
        """참가자 설정"""
        self.moderator = moderator
        self.participants = []

        center = QPointF(self.width() / 2, self.height() / 2)
        for i, p in enumerate(participants):
            node = ParticipantNode(p, i, len(participants), center, self.participant_radius)
            self.participants.append(node)

        self.update()

    def set_current_speaker(self, index: int):
        """현재 발언자 설정"""
        self.current_speaker_index = index
        for i, node in enumerate(self.participants):
            node.is_speaking = (i == index)
        self.update()

    def mark_done(self, index: int):
        """참가자 완료 표시"""
        if 0 <= index < len(self.participants):
            self.participants[index].is_done = True
            self.update()

    def reset_all(self):
        """모든 상태 초기화"""
        self.current_speaker_index = -1
        for node in self.participants:
            node.is_speaking = False
            node.is_done = False
        self.update()

    def resizeEvent(self, event):
        """리사이즈 시 위치 재계산"""
        super().resizeEvent(event)
        center = QPointF(self.width() / 2, self.height() / 2)
        self.participant_radius = min(self.width(), self.height()) / 2 - 80

        for i, node in enumerate(self.participants):
            angle = (2 * math.pi * i / len(self.participants)) - math.pi / 2
            node.x = center.x() + self.participant_radius * math.cos(angle)
            node.y = center.y() + self.participant_radius * math.sin(angle)

    def paintEvent(self, event):
        """그리기"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center = QPointF(self.width() / 2, self.height() / 2)

        # 배경
        painter.fillRect(self.rect(), QColor("#1a1a2e"))

        # 중앙 테이블
        self._draw_table(painter, center)

        # 연결선 (테이블 → 참가자)
        self._draw_connections(painter, center)

        # 참가자 노드
        for node in self.participants:
            self._draw_participant(painter, node)

        # 중재자 (상단 중앙)
        if self.moderator:
            self._draw_moderator(painter)

    def _draw_table(self, painter: QPainter, center: QPointF):
        """중앙 테이블 그리기"""
        table_size = self.table_radius * 2

        # 그림자
        shadow_offset = 5
        painter.setBrush(QBrush(QColor(0, 0, 0, 50)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(
            QRectF(center.x() - self.table_radius + shadow_offset,
                   center.y() - self.table_radius + shadow_offset,
                   table_size, table_size)
        )

        # 테이블 본체
        gradient_center = QColor("#2d2d44")
        painter.setBrush(QBrush(gradient_center))
        painter.setPen(QPen(QColor("#4a4a6a"), 3))
        painter.drawEllipse(
            QRectF(center.x() - self.table_radius,
                   center.y() - self.table_radius,
                   table_size, table_size)
        )

        # 테이블 레이블
        painter.setPen(QColor("#888"))
        painter.setFont(QFont("맑은 고딕", 12))
        painter.drawText(
            QRectF(center.x() - 50, center.y() - 10, 100, 20),
            Qt.AlignmentFlag.AlignCenter,
            "ROUND TABLE"
        )

    def _draw_connections(self, painter: QPainter, center: QPointF):
        """테이블과 참가자 연결선"""
        for node in self.participants:
            if node.is_speaking:
                # 현재 발언자는 강조
                pen = QPen(QColor(node.persona.color), 3)
                pen.setStyle(Qt.PenStyle.SolidLine)
            else:
                pen = QPen(QColor("#444"), 1)
                pen.setStyle(Qt.PenStyle.DashLine)

            painter.setPen(pen)
            painter.drawLine(center, QPointF(node.x, node.y))

    def _draw_participant(self, painter: QPainter, node: ParticipantNode):
        """참가자 노드 그리기"""
        x, y = node.x, node.y
        size = node.size

        # 현재 발언자 강조 효과
        if node.is_speaking:
            # 글로우 효과
            for i in range(3):
                alpha = 80 - i * 25
                glow_size = size + 10 + i * 8
                painter.setBrush(QBrush(QColor(node.persona.color).lighter(150)))
                painter.setOpacity(alpha / 255)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(
                    QRectF(x - glow_size/2, y - glow_size/2, glow_size, glow_size)
                )
            painter.setOpacity(1.0)

        # 노드 배경
        if node.is_done:
            bg_color = QColor("#2d5a2d")  # 완료: 녹색
            border_color = QColor("#4a8a4a")
        elif node.is_speaking:
            bg_color = QColor(node.persona.color).darker(150)
            border_color = QColor(node.persona.color)
        else:
            bg_color = QColor("#2d2d2d")
            border_color = QColor("#555")

        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 3 if node.is_speaking else 2))
        painter.drawEllipse(QRectF(x - size/2, y - size/2, size, size))

        # 아이콘
        painter.setPen(QColor("white"))
        painter.setFont(QFont("Segoe UI Emoji", 24))
        painter.drawText(
            QRectF(x - size/2, y - size/2, size, size),
            Qt.AlignmentFlag.AlignCenter,
            node.persona.icon
        )

        # 이름 (아래)
        painter.setFont(QFont("맑은 고딕", 10, QFont.Weight.Bold))
        painter.setPen(QColor(node.persona.color))
        name_rect = QRectF(x - 60, y + size/2 + 5, 120, 20)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, node.persona.name)

        # 완료 체크 표시
        if node.is_done:
            painter.setFont(QFont("Segoe UI Emoji", 16))
            painter.setPen(QColor("#4ade80"))
            painter.drawText(
                QRectF(x + size/2 - 10, y - size/2 - 5, 30, 30),
                Qt.AlignmentFlag.AlignCenter,
                "✓"
            )

    def _draw_moderator(self, painter: QPainter):
        """중재자 그리기 (상단 중앙)"""
        x = self.width() / 2
        y = 50
        size = 50

        # 배경
        painter.setBrush(QBrush(QColor(self.moderator.color).darker(200)))
        painter.setPen(QPen(QColor(self.moderator.color), 2))
        painter.drawRoundedRect(
            QRectF(x - 80, y - 25, 160, 50),
            10, 10
        )

        # 아이콘
        painter.setFont(QFont("Segoe UI Emoji", 20))
        painter.setPen(QColor("white"))
        painter.drawText(
            QRectF(x - 75, y - 20, 40, 40),
            Qt.AlignmentFlag.AlignCenter,
            self.moderator.icon
        )

        # 이름
        painter.setFont(QFont("맑은 고딕", 12, QFont.Weight.Bold))
        painter.setPen(QColor(self.moderator.color))
        painter.drawText(
            QRectF(x - 30, y - 10, 100, 20),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.moderator.name
        )

    def mousePressEvent(self, event):
        """마우스 클릭 - 참가자 선택"""
        pos = event.position()
        for i, node in enumerate(self.participants):
            dist = math.sqrt((pos.x() - node.x)**2 + (pos.y() - node.y)**2)
            if dist <= node.size / 2:
                self.participant_clicked.emit(i)
                return
        super().mousePressEvent(event)
