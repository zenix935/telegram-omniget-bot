"""SQLite-backed persistent data usage and stats tracker."""

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class StatsTracker:
    """Tracks downloaded bytes and counts per user and chat across time windows."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS download_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    is_group INTEGER NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_download_logs_created_at ON download_logs(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_download_logs_user_id ON download_logs(user_id)"
            )
            conn.commit()

    def _record_sync(
        self,
        user_id: int,
        chat_id: int,
        is_group: bool,
        file_size_bytes: int,
        media_type: str,
        quality: str,
        timestamp: float,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO download_logs (user_id, chat_id, is_group, file_size_bytes, media_type, quality, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    chat_id,
                    1 if is_group else 0,
                    file_size_bytes,
                    media_type,
                    quality,
                    timestamp,
                ),
            )
            conn.commit()

    async def record_download(
        self,
        user_id: int,
        chat_id: int,
        is_group: bool,
        file_size_bytes: int,
        media_type: str = "video",
        quality: str = "best",
    ) -> None:
        """Asynchronously record a completed download in the database."""
        timestamp = time.time()
        await asyncio.to_thread(
            self._record_sync,
            user_id,
            chat_id,
            is_group,
            file_size_bytes,
            media_type,
            quality,
            timestamp,
        )

    def _get_usage_for_period_sync(self, since_timestamp: Optional[float] = None) -> Dict[str, int]:
        with self._get_connection() as conn:
            if since_timestamp is not None:
                cursor = conn.execute(
                    """
                    SELECT 
                        COALESCE(SUM(file_size_bytes), 0) AS total_bytes,
                        COUNT(id) AS total_count,
                        COUNT(DISTINCT user_id) AS unique_users,
                        COUNT(DISTINCT chat_id) AS unique_chats
                    FROM download_logs
                    WHERE created_at >= ?
                    """,
                    (since_timestamp,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT 
                        COALESCE(SUM(file_size_bytes), 0) AS total_bytes,
                        COUNT(id) AS total_count,
                        COUNT(DISTINCT user_id) AS unique_users,
                        COUNT(DISTINCT chat_id) AS unique_chats
                    FROM download_logs
                    """
                )
            row = cursor.fetchone()
            return {
                "total_bytes": int(row["total_bytes"]),
                "total_count": int(row["total_count"]),
                "unique_users": int(row["unique_users"]),
                "unique_chats": int(row["unique_chats"]),
            }

    def _get_all_stats_sync(self) -> Dict[str, Dict[str, int]]:
        now = time.time()
        periods = {
            "1d": now - (24 * 3600),
            "7d": now - (7 * 24 * 3600),
            "30d": now - (30 * 24 * 3600),
            "all": None,
        }
        stats = {}
        for key, since_ts in periods.items():
            stats[key] = self._get_usage_for_period_sync(since_ts)
        return stats

    async def get_all_stats(self) -> Dict[str, Dict[str, int]]:
        """Fetch aggregated bandwidth usage and counts for 1d, 7d, 30d, and all time."""
        return await asyncio.to_thread(self._get_all_stats_sync)

    def _get_top_users_sync(self, since_timestamp: Optional[float] = None, limit: int = 5) -> List[Tuple[int, int, int]]:
        with self._get_connection() as conn:
            query = """
                SELECT user_id, COALESCE(SUM(file_size_bytes), 0) AS total_bytes, COUNT(id) AS download_count
                FROM download_logs
            """
            params = []
            if since_timestamp is not None:
                query += " WHERE created_at >= ?"
                params.append(since_timestamp)
            query += " GROUP BY user_id ORDER BY total_bytes DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, tuple(params))
            return [(int(r["user_id"]), int(r["total_bytes"]), int(r["download_count"])) for r in cursor.fetchall()]

    async def get_top_users(self, since_timestamp: Optional[float] = None, limit: int = 5) -> List[Tuple[int, int, int]]:
        """Fetch top bandwidth consumers for a time window."""
        return await asyncio.to_thread(self._get_top_users_sync, since_timestamp, limit)
