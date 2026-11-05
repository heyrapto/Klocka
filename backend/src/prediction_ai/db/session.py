from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from prediction_ai.core.config import Settings


@dataclass(slots=True)
class Database:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session that rolls back automatically on error."""
        session = self.session_factory()
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> None:
        from sqlalchemy import text

        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))


def create_database(settings: Settings) -> Database:
    """Create an async engine and session factory.

    The API keeps one instance for the process lifetime. Celery workers create
    and dispose a fresh instance per task so no pooled connection crosses event
    loops.
    """
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        future=True,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=settings.DATABASE_POOL_RECYCLE_SECONDS,
        connect_args={"command_timeout": settings.DATABASE_COMMAND_TIMEOUT_SECONDS},
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return Database(engine=engine, session_factory=session_factory)


_db: Database | None = None


def get_database(settings: Settings | None = None) -> Database:
    """Get or initialize the shared database engine singleton."""
    global _db
    if _db is None:
        from prediction_ai.core.config import get_settings

        _db = create_database(settings or get_settings())
    return _db


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Dependency helper yielding an active async database session."""
    db = get_database()
    async with db.session() as session:
        yield session

