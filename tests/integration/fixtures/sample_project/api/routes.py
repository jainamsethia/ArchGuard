"""API routes layer."""

from db.models import get_user, save_user

def create_user_route(name: str) -> dict:
    """Create a new user route."""
    user = save_user(name)
    return {"status": "ok", "user": user}

def get_user_route(user_id: int) -> dict:
    """Get an existing user route."""
    user = get_user(user_id)
    return {"status": "ok", "user": user}
