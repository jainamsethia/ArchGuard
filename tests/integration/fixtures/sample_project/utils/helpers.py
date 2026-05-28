"""Utility functions layer."""

def format_name(name: str) -> str:
    """Format a name to title case."""
    return name.strip().title()

def generate_id() -> int:
    """Generate a random ID."""
    import random
    return random.randint(1000, 9999)

def format_name_duplicate(name: str) -> str:
    """Format a name to title case (duplicate)."""
    return name.strip().title()
