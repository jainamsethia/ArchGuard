"""Database models layer."""

def get_user(user_id: int) -> dict:
    """Retrieve a user from the database."""
    return {"id": user_id, "name": f"User_{user_id}"}

def save_user(name: str) -> dict:
    """Save a user to the database."""
    return {"id": hash(name) % 1000, "name": name}

def delete_user(user_id: int) -> bool:
    """Delete a user from the database."""
    return True
