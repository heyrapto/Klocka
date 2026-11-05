"""Authentication and JWT verification package."""

from prediction_ai.auth.jwt import AuthenticatedUser, verify_supabase_jwt

__all__ = ["verify_supabase_jwt", "AuthenticatedUser"]
