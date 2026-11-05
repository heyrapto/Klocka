import json
from datetime import UTC, datetime
from uuid import UUID

import structlog

from prediction_ai.db.sqlite import get_sqlite_db

logger = structlog.get_logger(__name__)


class EventRecorder:
    """Records durable workflow events to SQLite and serializes SSE streams."""

    def __init__(self, session: object | None, analysis_id: UUID) -> None:
        self.session = session
        self.analysis_id = analysis_id
        self._sequence = 0

    async def init_sequence(self) -> None:
        sqlite = get_sqlite_db()
        events = await sqlite.get_analysis_events(str(self.analysis_id))
        self._sequence = len(events)

    async def emit(self, event_type: str, stage: str, payload: dict) -> dict:
        self._sequence += 1
        now = datetime.now(UTC)

        event_dict = {
            "sequence_number": self._sequence,
            "event_type": event_type,
            "stage": stage,
            "payload": payload,
            "created_at": now.isoformat(),
        }

        sqlite = get_sqlite_db()
        try:
            await sqlite.append_analysis_event(str(self.analysis_id), event_dict)
        except Exception as err:
            logger.warning("Could not append event to SQLite", error=str(err))

        logger.info(
            "Emitted workflow event",
            analysis_id=str(self.analysis_id),
            event_type=event_type,
            stage=stage,
            sequence=self._sequence,
        )
        return event_dict


def format_sse_message(event_type: str, data: dict, event_id: int | str) -> str:
    """Format SSE protocol text block."""
    json_data = json.dumps(data, default=str)
    return f"id: {event_id}\nevent: {event_type}\ndata: {json_data}\n\n"
