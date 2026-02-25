"""
보드별 AI 요청/응답 타임라인 로그
SQLite 기반 저장
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
import os


@dataclass
class TimelineEntry:
    """타임라인 항목"""
    id: int = None
    board_name: str = ""
    node_id: int = 0
    timestamp: str = ""
    model: str = ""
    # 입력
    user_message: str = ""
    attachments: List[str] = None  # JSON으로 저장
    # 출력
    ai_response: str = ""
    # 메타데이터
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    error: str = None


class TimelineDB:
    """타임라인 데이터베이스"""

    def __init__(self):
        self.db_path = self._get_db_path()
        self._init_db()

    def _get_db_path(self) -> Path:
        """DB 파일 경로"""
        from v.settings import get_app_data_path
        return get_app_data_path() / 'timeline.db'

    def _init_db(self):
        """테이블 생성"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS timeline (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    board_name TEXT NOT NULL,
                    node_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    model TEXT NOT NULL,
                    user_message TEXT,
                    attachments TEXT,
                    ai_response TEXT,
                    tokens_in INTEGER DEFAULT 0,
                    tokens_out INTEGER DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0,
                    error TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_board_name
                ON timeline(board_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON timeline(timestamp DESC)
            """)
            conn.commit()

    def add_entry(self, entry: TimelineEntry) -> int:
        """항목 추가"""
        entry.timestamp = datetime.now().isoformat()
        attachments_json = json.dumps(entry.attachments or [])

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO timeline
                (board_name, node_id, timestamp, model, user_message,
                 attachments, ai_response, tokens_in, tokens_out, duration_ms, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.board_name,
                entry.node_id,
                entry.timestamp,
                entry.model,
                entry.user_message,
                attachments_json,
                entry.ai_response,
                entry.tokens_in,
                entry.tokens_out,
                entry.duration_ms,
                entry.error
            ))
            conn.commit()
            return cursor.lastrowid

    def update_response(
        self,
        entry_id: int,
        ai_response: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        duration_ms: int = 0,
        error: str = None
    ):
        """응답 업데이트 (스트리밍 완료 후)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE timeline
                SET ai_response = ?, tokens_in = ?, tokens_out = ?,
                    duration_ms = ?, error = ?
                WHERE id = ?
            """, (ai_response, tokens_in, tokens_out, duration_ms, error, entry_id))
            conn.commit()

    def get_board_timeline(
        self,
        board_name: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[TimelineEntry]:
        """보드별 타임라인 조회"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM timeline
                WHERE board_name = ?
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """, (board_name, limit, offset))

            entries = []
            for row in cursor.fetchall():
                entry = TimelineEntry(
                    id=row['id'],
                    board_name=row['board_name'],
                    node_id=row['node_id'],
                    timestamp=row['timestamp'],
                    model=row['model'],
                    user_message=row['user_message'],
                    attachments=json.loads(row['attachments'] or '[]'),
                    ai_response=row['ai_response'],
                    tokens_in=row['tokens_in'],
                    tokens_out=row['tokens_out'],
                    duration_ms=row['duration_ms'],
                    error=row['error']
                )
                entries.append(entry)
            return entries

    def get_node_history(
        self,
        board_name: str,
        node_id: int
    ) -> List[TimelineEntry]:
        """특정 노드의 히스토리"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM timeline
                WHERE board_name = ? AND node_id = ?
                ORDER BY timestamp ASC
            """, (board_name, node_id))

            entries = []
            for row in cursor.fetchall():
                entry = TimelineEntry(
                    id=row['id'],
                    board_name=row['board_name'],
                    node_id=row['node_id'],
                    timestamp=row['timestamp'],
                    model=row['model'],
                    user_message=row['user_message'],
                    attachments=json.loads(row['attachments'] or '[]'),
                    ai_response=row['ai_response'],
                    tokens_in=row['tokens_in'],
                    tokens_out=row['tokens_out'],
                    duration_ms=row['duration_ms'],
                    error=row['error']
                )
                entries.append(entry)
            return entries

    def get_recent(self, limit: int = 50) -> List[TimelineEntry]:
        """최근 항목 (전체)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM timeline
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))

            entries = []
            for row in cursor.fetchall():
                entry = TimelineEntry(
                    id=row['id'],
                    board_name=row['board_name'],
                    node_id=row['node_id'],
                    timestamp=row['timestamp'],
                    model=row['model'],
                    user_message=row['user_message'],
                    attachments=json.loads(row['attachments'] or '[]'),
                    ai_response=row['ai_response'],
                    tokens_in=row['tokens_in'],
                    tokens_out=row['tokens_out'],
                    duration_ms=row['duration_ms'],
                    error=row['error']
                )
                entries.append(entry)
            return entries

    def get_stats(self, board_name: str = None) -> Dict[str, Any]:
        """통계 조회"""
        with sqlite3.connect(self.db_path) as conn:
            if board_name:
                where = "WHERE board_name = ?"
                params = (board_name,)
            else:
                where = ""
                params = ()

            # 총 요청 수
            total = conn.execute(
                f"SELECT COUNT(*) FROM timeline {where}", params
            ).fetchone()[0]

            # 모델별 사용량
            cursor = conn.execute(f"""
                SELECT model, COUNT(*) as count,
                       SUM(tokens_in) as total_in,
                       SUM(tokens_out) as total_out
                FROM timeline {where}
                GROUP BY model
            """, params)

            by_model = {}
            for row in cursor.fetchall():
                by_model[row[0]] = {
                    'count': row[1],
                    'tokens_in': row[2] or 0,
                    'tokens_out': row[3] or 0
                }

            # 에러 수
            error_count = conn.execute(
                f"SELECT COUNT(*) FROM timeline {where} {'AND' if where else 'WHERE'} error IS NOT NULL",
                params
            ).fetchone()[0]

            return {
                'total_requests': total,
                'by_model': by_model,
                'error_count': error_count
            }

    def delete_board_entries(self, board_name: str) -> int:
        """보드 항목 삭제"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM timeline WHERE board_name = ?",
                (board_name,)
            )
            conn.commit()
            return cursor.rowcount

    def clear_all(self):
        """전체 삭제"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM timeline")
            conn.commit()


# 싱글톤 인스턴스
_db: TimelineDB = None


def get_timeline_db() -> TimelineDB:
    """타임라인 DB 인스턴스 반환"""
    global _db
    if _db is None:
        _db = TimelineDB()
    return _db
