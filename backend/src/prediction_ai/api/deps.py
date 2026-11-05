from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from prediction_ai.auth.jwt import AuthenticatedUser, verify_supabase_jwt
from prediction_ai.db.mongo import MongoDBStore, get_mongo_db
from prediction_ai.db.session import get_database
from prediction_ai.db.sqlite import SQLiteStore, get_sqlite_db


async def get_current_user(
    user: AuthenticatedUser = Depends(verify_supabase_jwt),
) -> AuthenticatedUser:
    """FastAPI dependency injecting verified authenticated user."""
    return user


async def get_db() -> AsyncGenerator[AsyncSession | None, None]:
    """FastAPI dependency injecting async database session if available, else None."""
    try:
        db = get_database()
        async with db.session() as session:
            yield session
    except Exception:
        yield None


def get_sqlite() -> SQLiteStore:
    """FastAPI dependency injecting zero-daemon SQLite store."""
    return get_sqlite_db()


def get_mongo() -> MongoDBStore:
    """FastAPI dependency injecting MongoDB store."""
    return get_mongo_db()
