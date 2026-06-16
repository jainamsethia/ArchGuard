import pytest
from pathlib import Path

def test_evolution_dashboard_ui_elements():
    """Verify that index.html contains the necessary HTML elements for the Evolution Dashboard."""
    index_path = Path(__file__).parent.parent.parent / "archguard" / "dashboard" / "static" / "index.html"
    assert index_path.exists(), "index.html not found"
    
    content = index_path.read_text(encoding="utf-8")
    
    # Verify grid container exists
    assert 'id="evolution-trends-grid"' in content
    assert 'Evolution Trends' in content
    
    # Verify trend value containers
    assert 'id="trend-health-val"' in content
    assert 'id="trend-debt-val"' in content
    assert 'id="trend-violation-val"' in content
    assert 'id="trend-fitness-val"' in content
    
    # Verify trend status containers
    assert 'id="trend-health-status"' in content
    assert 'id="trend-debt-status"' in content
    assert 'id="trend-violation-status"' in content
    assert 'id="trend-fitness-status"' in content

def test_evolution_dashboard_js_functions():
    """Verify that index.html contains the necessary JS to fetch and render Evolution data."""
    index_path = Path(__file__).parent.parent.parent / "archguard" / "dashboard" / "static" / "index.html"
    content = index_path.read_text(encoding="utf-8")
    
    # Verify fetch call
    assert "fetch('/api/evolution/trends')" in content
    
    # Verify update function exists
    assert "function updateEvolutionTrends(evoData)" in content
    
    # Verify it processes each card type
    assert "updateCard('health', evoData.health_trend)" in content
    assert "updateCard('debt', evoData.debt_trend)" in content
    assert "updateCard('violation', evoData.violation_trend)" in content
    assert "updateCard('fitness', evoData.fitness_trend)" in content

    # Verify classification logic is present
    assert "cls === 'improving'" in content
    assert "cls === 'declining'" in content
