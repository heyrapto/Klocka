import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from prediction_ai.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class MongoDBStore:
    """Production MongoDB document store with transparent in-memory dev fallback."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: AsyncIOMotorClient | None = None
        self._db: AsyncIOMotorDatabase | None = None
        self._is_connected = False

        # In-memory collections fallback for development / offline testing
        self._memory_analyses: dict[str, dict[str, Any]] = {}
        self._memory_markets: dict[str, dict[str, Any]] = {}
        self._memory_forecasts: dict[str, dict[str, Any]] = {}
        self._memory_feedback: dict[str, dict[str, Any]] = {}

    async def connect(self) -> None:
        """Attempt connection to MongoDB."""
        try:
            self._client = AsyncIOMotorClient(
                self.settings.MONGODB_URL,
                serverSelectionTimeoutMS=self.settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
            )
            # Ping database to verify connection
            await self._client.admin.command("ping")
            self._db = self._client[self.settings.MONGODB_DB_NAME]
            self._is_connected = True
            logger.info("Connected to MongoDB", url=self.settings.MONGODB_URL, db=self.settings.MONGODB_DB_NAME)
        except (ConnectionFailure, ServerSelectionTimeoutError, Exception) as err:
            logger.warning(
                "MongoDB not reachable; operating in resilient in-memory mode",
                error=str(err),
            )
            self._is_connected = False

    async def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    # ------------------------------------------------------------------
    # Analyses Collection
    # ------------------------------------------------------------------
    async def create_analysis(self, doc: dict[str, Any]) -> dict[str, Any]:
        analysis_id = str(doc.get("analysis_id") or uuid.uuid4())
        doc["_id"] = analysis_id
        doc["analysis_id"] = analysis_id
        doc.setdefault("created_at", datetime.now(UTC).isoformat())
        doc.setdefault("updated_at", datetime.now(UTC).isoformat())
        doc.setdefault("events", [])

        if self._is_connected and self._db is not None:
            await self._db.analyses.insert_one(doc)
        else:
            self._memory_analyses[analysis_id] = dict(doc)
        return doc

    async def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        if self._is_connected and self._db is not None:
            return await self._db.analyses.find_one({"_id": analysis_id})
        return self._memory_analyses.get(analysis_id)

    async def update_analysis(self, analysis_id: str, updates: dict[str, Any]) -> None:
        updates["updated_at"] = datetime.now(UTC).isoformat()
        if self._is_connected and self._db is not None:
            await self._db.analyses.update_one({"_id": analysis_id}, {"$set": updates})
        else:
            if analysis_id in self._memory_analyses:
                self._memory_analyses[analysis_id].update(updates)

    async def append_analysis_event(self, analysis_id: str, event_doc: dict[str, Any]) -> None:
        event_doc.setdefault("timestamp", datetime.now(UTC).isoformat())
        if self._is_connected and self._db is not None:
            await self._db.analyses.update_one(
                {"_id": analysis_id},
                {
                    "$push": {"events": event_doc},
                    "$set": {"updated_at": datetime.now(UTC).isoformat()},
                },
            )
        else:
            if analysis_id in self._memory_analyses:
                self._memory_analyses[analysis_id].setdefault("events", []).append(event_doc)

    # ------------------------------------------------------------------
    # Markets Collection
    # ------------------------------------------------------------------
    async def upsert_market(self, doc: dict[str, Any]) -> dict[str, Any]:
        market_id = str(doc.get("platform_market_id") or doc.get("id"))
        doc["_id"] = market_id
        doc["updated_at"] = datetime.now(UTC).isoformat()

        if self._is_connected and self._db is not None:
            await self._db.markets.update_one({"_id": market_id}, {"$set": doc}, upsert=True)
        else:
            self._memory_markets[market_id] = dict(doc)
        return doc

    async def get_market(self, market_id: str) -> dict[str, Any] | None:
        if self._is_connected and self._db is not None:
            return await self._db.markets.find_one({"_id": market_id})
        return self._memory_markets.get(market_id)

    # ------------------------------------------------------------------
    # Forecasts Collection
    # ------------------------------------------------------------------
    async def save_forecast(self, doc: dict[str, Any]) -> dict[str, Any]:
        forecast_id = str(doc.get("id") or doc.get("forecast_id") or uuid.uuid4())
        doc["_id"] = forecast_id
        doc["forecast_id"] = forecast_id
        doc.setdefault("created_at", datetime.now(UTC).isoformat())

        if self._is_connected and self._db is not None:
            await self._db.forecasts.insert_one(doc)
        else:
            self._memory_forecasts[forecast_id] = dict(doc)
        return doc

    async def get_forecast(self, forecast_id: str) -> dict[str, Any] | None:
        if self._is_connected and self._db is not None:
            return await self._db.forecasts.find_one({"_id": forecast_id})
        return self._memory_forecasts.get(forecast_id)

    # ------------------------------------------------------------------
    # Feedback & Self-Improvement Collection
    # ------------------------------------------------------------------
    async def record_feedback(self, doc: dict[str, Any]) -> dict[str, Any]:
        feedback_id = str(doc.get("id") or uuid.uuid4())
        doc["_id"] = feedback_id
        doc.setdefault("created_at", datetime.now(UTC).isoformat())

        if self._is_connected and self._db is not None:
            await self._db.feedback.insert_one(doc)
        else:
            self._memory_feedback[feedback_id] = dict(doc)
        return doc

    async def get_recent_learnings(self, category: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve recent feedback and postmortem lessons to inform future prompts."""
        if self._is_connected and self._db is not None:
            query = {"category": category} if category else {}
            cursor = self._db.feedback.find(query).sort("created_at", -1).limit(limit)
            return await cursor.to_list(length=limit)

        items = list(self._memory_feedback.values())
        if category:
            items = [item for item in items if item.get("category") == category]
        return sorted(items, key=lambda x: str(x.get("created_at")), reverse=True)[:limit]


_mongo_store: MongoDBStore | None = None


def get_mongo_db() -> MongoDBStore:
    """Singleton getter for MongoDB store."""
    global _mongo_store
    if _mongo_store is None:
        _mongo_store = MongoDBStore()
    return _mongo_store
