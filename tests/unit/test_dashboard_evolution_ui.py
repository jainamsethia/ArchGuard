from pathlib import Path


def test_evolution_dashboard_ui_elements():
    """Verify that dashboard.html contains the necessary HTML elements for the Evolution Dashboard."""
    index_path = (
        Path(__file__).parent.parent.parent
        / "archguard"
        / "dashboard"
        / "templates"
        / "dashboard.html"
    )
    assert index_path.exists(), "dashboard.html not found"

    content = index_path.read_text(encoding="utf-8")

    # Verify grid container exists
    assert 'id="evolution-trends-grid"' in content
    assert "Evolution Trends" in content

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
    """The JS that fetches and renders Evolution data still exists.

    Read across the module tree rather than one file: dashboard.js was split
    into archguard/dashboard/static/js/, and the fetch now lives in poll.js
    while the rendering lives in render/evolution.js. Concatenating keeps this
    test about behaviour being present rather than about which file holds it.
    """
    js_root = (
        Path(__file__).parent.parent.parent
        / "archguard"
        / "dashboard"
        / "static"
        / "js"
    )
    sources = sorted(js_root.rglob("*.js"))
    assert sources, f"no dashboard modules found under {js_root}"
    content = chr(10).join(p.read_text(encoding="utf-8") for p in sources)

    # Verify fetch call
    assert "safeFetch(`/api/v1/evolution/trends" in content

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
