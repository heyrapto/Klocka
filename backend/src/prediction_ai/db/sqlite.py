import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import structlog

from prediction_ai.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class SQLiteStore:
    """Production zero-daemon embedded database store powered by Python sqlite3."""

    def __init__(self, db_path: str | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = db_path or self.settings.SQLITE_DB_PATH
        self._initialized = False

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        # Enable write-ahead logging (WAL) for high concurrency and fast reads/writes
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        """Create tables and indexes if they do not exist."""
        # Ensure directory exists
        parent_dir = Path(self.db_path).parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        with self._get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS analyses (
                analysis_id TEXT PRIMARY KEY,
                user_id TEXT,
                status TEXT NOT NULL,
                hint TEXT,
                screenshot_asset_id TEXT,
                object_path TEXT,
                extraction_result TEXT,
                verification_result TEXT,
                forecast_id TEXT,
                forecast_data TEXT,
                specialist_context TEXT,
                image_description TEXT,
                error_code TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS analysis_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                stage TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_analysis ON analysis_events(analysis_id, sequence_number);

            CREATE TABLE IF NOT EXISTS markets (
                market_id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                platform_market_id TEXT,
                title TEXT NOT NULL,
                slug TEXT,
                category TEXT,
                market_type TEXT,
                raw_data TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forecasts (
                forecast_id TEXT PRIMARY KEY,
                analysis_id TEXT NOT NULL,
                user_id TEXT,
                market_id TEXT,
                probability_yes REAL NOT NULL,
                probability_no REAL NOT NULL,
                uncertainty_interval TEXT,
                recommendation TEXT NOT NULL,
                decision_metrics TEXT,
                explanation TEXT,
                evidence TEXT,
                specialist_context TEXT,
                image_description TEXT,
                market_data TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_forecasts_analysis ON forecasts(analysis_id);

            CREATE TABLE IF NOT EXISTS feedback (
                feedback_id TEXT PRIMARY KEY,
                analysis_id TEXT NOT NULL,
                user_id TEXT,
                category TEXT,
                market_title TEXT,
                recommendation TEXT,
                actual_outcome TEXT NOT NULL,
                was_correct INTEGER NOT NULL,
                settled_price REAL,
                user_notes TEXT,
                postmortem_analysis TEXT,
                key_learnings TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_category ON feedback(category, created_at);
            """)
        self._initialized = True
        logger.info("SQLite database initialized successfully", db_path=self.db_path)

    # ------------------------------------------------------------------
    # Analyses
    # ------------------------------------------------------------------
    def _create_analysis_sync(self, doc: dict[str, Any]) -> dict[str, Any]:
        analysis_id = str(doc.get("analysis_id") or uuid.uuid4())
        doc["analysis_id"] = analysis_id
        now = datetime.now(UTC).isoformat()
        doc.setdefault("created_at", now)
        doc.setdefault("updated_at", now)
        doc.setdefault("events", [])

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO analyses (
                    analysis_id, user_id, status, hint, screenshot_asset_id,
                    object_path, extraction_result, verification_result,
                    forecast_id, forecast_data, specialist_context, image_description,
                    error_code, error_message, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    str(doc.get("user_id") or ""),
                    str(doc.get("status") or "RECEIVED"),
                    doc.get("hint"),
                    doc.get("screenshot_asset_id"),
                    doc.get("object_path"),
                    json.dumps(doc.get("extraction_result")) if doc.get("extraction_result") else None,
                    json.dumps(doc.get("verification_result")) if doc.get("verification_result") else None,
                    doc.get("forecast_id"),
                    json.dumps(doc.get("forecast")) if doc.get("forecast") else None,
                    json.dumps(doc.get("specialist_context")) if doc.get("specialist_context") else None,
                    doc.get("image_description"),
                    doc.get("error_code"),
                    doc.get("error_message"),
                    doc["created_at"],
                    doc["updated_at"],
                    doc.get("completed_at"),
                ),
            )
        return doc

    async def create_analysis(self, doc: dict[str, Any]) -> dict[str, Any]:
        return await anyio.to_thread.run_sync(self._create_analysis_sync, doc)

    def _get_analysis_sync(self, analysis_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,))
            row = cursor.fetchone()
            if not row:
                return None

            result = dict(row)
            # Parse JSON fields
            for f in ("extraction_result", "verification_result", "forecast_data", "specialist_context"):
                if result.get(f):
                    try:
                        result[f] = json.loads(result[f])
                    except Exception:
                        pass
            if result.get("forecast_data"):
                result["forecast"] = result["forecast_data"]

            # Load attached events
            ev_cursor = conn.execute(
                "SELECT sequence_number, event_type, stage, payload, created_at FROM analysis_events WHERE analysis_id = ? ORDER BY sequence_number ASC",
                (analysis_id,),
            )
            events = []
            for ev_row in ev_cursor.fetchall():
                ev_dict = dict(ev_row)
                if ev_dict.get("payload"):
                    try:
                        ev_dict["payload"] = json.loads(ev_dict["payload"])
                    except Exception:
                        pass
                events.append(ev_dict)
            result["events"] = events
            return result

    async def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        return await anyio.to_thread.run_sync(self._get_analysis_sync, analysis_id)

    def _update_analysis_sync(self, analysis_id: str, updates: dict[str, Any]) -> None:
        updates["updated_at"] = datetime.now(UTC).isoformat()
        fields = []
        values = []

        mapping = {
            "status": "status",
            "error_code": "error_code",
            "error_message": "error_message",
            "completed_at": "completed_at",
            "updated_at": "updated_at",
            "forecast_id": "forecast_id",
            "image_description": "image_description",
            "extraction_result": "extraction_result",
            "verification_result": "verification_result",
            "forecast": "forecast_data",
            "specialist_context": "specialist_context",
        }

        for k, col in mapping.items():
            if k in updates:
                val = updates[k]
                if isinstance(val, (dict, list)):
                    val = json.dumps(val)
                fields.append(f"{col} = ?")
                values.append(val)

        if not fields:
            return

        values.append(analysis_id)
        sql = f"UPDATE analyses SET {', '.join(fields)} WHERE analysis_id = ?"
        with self._get_connection() as conn:
            conn.execute(sql, tuple(values))

    async def update_analysis(self, analysis_id: str, updates: dict[str, Any]) -> None:
        await anyio.to_thread.run_sync(self._update_analysis_sync, analysis_id, updates)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _append_analysis_event_sync(self, analysis_id: str, event_doc: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        event_doc.setdefault("created_at", now)
        seq = int(event_doc.get("sequence_number") or 1)
        event_type = str(event_doc.get("event_type") or "analysis.stage")
        stage = str(event_doc.get("stage") or "STAGE")
        payload = json.dumps(event_doc.get("payload") or {})

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO analysis_events (analysis_id, sequence_number, event_type, stage, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (analysis_id, seq, event_type, stage, payload, event_doc["created_at"]),
            )
            conn.execute("UPDATE analyses SET updated_at = ? WHERE analysis_id = ?", (now, analysis_id))

    async def append_analysis_event(self, analysis_id: str, event_doc: dict[str, Any]) -> None:
        await anyio.to_thread.run_sync(self._append_analysis_event_sync, analysis_id, event_doc)

    def _get_events_sync(self, analysis_id: str, since_seq: int = 0) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT sequence_number, event_type, stage, payload, created_at FROM analysis_events WHERE analysis_id = ? AND sequence_number > ? ORDER BY sequence_number ASC",
                (analysis_id, since_seq),
            )
            rows = []
            for r in cursor.fetchall():
                d = dict(r)
                if d.get("payload"):
                    try:
                        d["payload"] = json.loads(d["payload"])
                    except Exception:
                        pass
                rows.append(d)
            return rows

    async def get_analysis_events(self, analysis_id: str, since_seq: int = 0) -> list[dict[str, Any]]:
        return await anyio.to_thread.run_sync(self._get_events_sync, analysis_id, since_seq)

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------
    def _upsert_market_sync(self, doc: dict[str, Any]) -> dict[str, Any]:
        market_id = str(doc.get("platform_market_id") or doc.get("id") or uuid.uuid4())
        doc["market_id"] = market_id
        now = datetime.now(UTC).isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO markets (market_id, platform, platform_market_id, title, slug, category, market_type, raw_data, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_id) DO UPDATE SET
                    title = excluded.title,
                    raw_data = excluded.raw_data,
                    updated_at = excluded.updated_at
                """,
                (
                    market_id,
                    str(doc.get("platform") or "polymarket"),
                    str(doc.get("platform_market_id") or market_id),
                    str(doc.get("title") or "Prediction Contract"),
                    doc.get("slug"),
                    doc.get("category"),
                    doc.get("market_type"),
                    json.dumps(doc),
                    now,
                ),
            )
        return doc

    async def upsert_market(self, doc: dict[str, Any]) -> dict[str, Any]:
        return await anyio.to_thread.run_sync(self._upsert_market_sync, doc)

    # ------------------------------------------------------------------
    # Forecasts
    # ------------------------------------------------------------------
    def _save_forecast_sync(self, doc: dict[str, Any]) -> dict[str, Any]:
        forecast_id = str(doc.get("id") or doc.get("forecast_id") or uuid.uuid4())
        doc["forecast_id"] = forecast_id
        now = datetime.now(UTC).isoformat()
        doc.setdefault("created_at", now)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO forecasts (
                    forecast_id, analysis_id, user_id, market_id,
                    probability_yes, probability_no, uncertainty_interval,
                    recommendation, decision_metrics, explanation,
                    evidence, specialist_context, image_description,
                    market_data, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(forecast_id) DO UPDATE SET
                    probability_yes = excluded.probability_yes,
                    probability_no = excluded.probability_no,
                    recommendation = excluded.recommendation
                """,
                (
                    forecast_id,
                    str(doc.get("analysis_id")),
                    str(doc.get("user_id")),
                    str(doc.get("market_id")),
                    float(doc.get("probability_yes", 0.5)),
                    float(doc.get("probability_no", 0.5)),
                    json.dumps(doc.get("uncertainty_interval", [])),
                    str(doc.get("recommendation", "NO_TRADE")),
                    json.dumps(doc.get("decision_metrics", {})),
                    json.dumps(doc.get("explanation", {})),
                    json.dumps(doc.get("evidence", [])),
                    json.dumps(doc.get("specialist_context", {})),
                    doc.get("image_description"),
                    json.dumps(doc.get("market", {})),
                    doc["created_at"],
                ),
            )
        return doc

    async def save_forecast(self, doc: dict[str, Any]) -> dict[str, Any]:
        return await anyio.to_thread.run_sync(self._save_forecast_sync, doc)

    def _get_forecast_sync(self, forecast_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM forecasts WHERE forecast_id = ?", (forecast_id,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            for f in ("uncertainty_interval", "decision_metrics", "explanation", "evidence", "specialist_context", "market_data"):
                if res.get(f):
                    try:
                        res[f] = json.loads(res[f])
                    except Exception:
                        pass
            if res.get("market_data"):
                res["market"] = res["market_data"]
            return res

    async def get_forecast(self, forecast_id: str) -> dict[str, Any] | None:
        return await anyio.to_thread.run_sync(self._get_forecast_sync, forecast_id)

    # ------------------------------------------------------------------
    # Feedback & Continuous Learning Loop
    # ------------------------------------------------------------------
    def _record_feedback_sync(self, doc: dict[str, Any]) -> dict[str, Any]:
        feedback_id = str(doc.get("id") or doc.get("feedback_id") or uuid.uuid4())
        doc["feedback_id"] = feedback_id
        now = datetime.now(UTC).isoformat()
        doc.setdefault("created_at", now)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO feedback (
                    feedback_id, analysis_id, user_id, category, market_title,
                    recommendation, actual_outcome, was_correct, settled_price,
                    user_notes, postmortem_analysis, key_learnings, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    str(doc.get("analysis_id")),
                    str(doc.get("user_id")),
                    doc.get("category"),
                    doc.get("market_title"),
                    doc.get("recommendation"),
                    str(doc.get("actual_outcome")),
                    1 if doc.get("was_correct") else 0,
                    float(doc.get("settled_price")) if doc.get("settled_price") is not None else None,
                    doc.get("user_notes"),
                    doc.get("postmortem_analysis"),
                    json.dumps(doc.get("key_learnings", [])),
                    doc["created_at"],
                ),
            )
        return doc

    async def record_feedback(self, doc: dict[str, Any]) -> dict[str, Any]:
        return await anyio.to_thread.run_sync(self._record_feedback_sync, doc)

    def _get_recent_learnings_sync(self, category: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            if category:
                cursor = conn.execute(
                    "SELECT * FROM feedback WHERE category = ? ORDER BY created_at DESC LIMIT ?",
                    (category, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            rows = []
            for r in cursor.fetchall():
                d = dict(r)
                d["was_correct"] = bool(d["was_correct"])
                if d.get("key_learnings"):
                    try:
                        d["key_learnings"] = json.loads(d["key_learnings"])
                    except Exception:
                        pass
                rows.append(d)
            return rows

    async def get_recent_learnings(self, category: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        return await anyio.to_thread.run_sync(self._get_recent_learnings_sync, category, limit)


_sqlite_store: SQLiteStore | None = None


def get_sqlite_db() -> SQLiteStore:
    """Singleton getter for SQLite storage engine."""
    global _sqlite_store
    if _sqlite_store is None:
        _sqlite_store = SQLiteStore()
        _sqlite_store.initialize()
    return _sqlite_store
