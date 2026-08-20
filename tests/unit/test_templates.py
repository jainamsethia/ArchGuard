"""Structural checks on the served HTML templates.

Note on C7: the Phase-1 audit recorded dashboard.html as carrying two unmatched
``</div>`` closers. That was wrong. The check behind it matched tags with a
line-oriented regex, which cannot see an opening tag whose attributes are split
across lines --

    <div aria-live="polite" class="glass-card" id="fallback-banner" hidden
         style="border-left:4px solid var(--warn-color)">

-- so every such element counted as closed but never opened, and its real
closer read as stray. Parsed properly, both templates balance exactly. The
guard is kept because the failure it was meant to catch is real and silent
(a browser reparents the following content rather than reporting anything);
only the parser was wrong.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "archguard" / "dashboard" / "templates"
TEMPLATE_FILES = sorted(TEMPLATES.glob("*.html"))

#: HTML void elements never carry a closing tag.
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _Balance(HTMLParser):
    """Records unmatched close tags and elements left open at EOF."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open_stack: list[tuple[str, int]] = []
        self.stray_close: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag not in VOID:
            self.open_stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        for i in range(len(self.open_stack) - 1, -1, -1):
            if self.open_stack[i][0] == tag:
                del self.open_stack[i:]
                return
        self.stray_close.append((tag, self.getpos()[0]))


def _parse(template: Path) -> _Balance:
    parser = _Balance()
    parser.feed(template.read_text(encoding="utf-8"))
    parser.close()
    return parser


def test_template_directory_is_not_empty() -> None:
    """Guard the guard: a glob matching nothing makes every test below vacuous."""
    assert TEMPLATE_FILES, f"no templates found under {TEMPLATES}"


@pytest.mark.parametrize("template", TEMPLATE_FILES, ids=lambda p: p.name)
def test_tags_are_balanced(template: Path) -> None:
    """An unmatched closer is silent: the browser reparents what follows."""
    parsed = _parse(template)
    assert not parsed.stray_close, (
        f"{template.name}: close tag with no matching open: {parsed.stray_close}"
    )
    assert not parsed.open_stack, (
        f"{template.name}: elements left open at EOF: {parsed.open_stack}"
    )


@pytest.mark.parametrize("template", TEMPLATE_FILES, ids=lambda p: p.name)
def test_every_referenced_static_asset_exists(template: Path) -> None:
    """A 404 on a <script src> is invisible until the page quietly misbehaves."""
    static = TEMPLATES.parent / "static"
    html = template.read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="/([^"?#]+)"', html)
    missing = [r for r in refs if not (static / r).exists()]
    assert not missing, f"{template.name} references missing static files: {missing}"


@pytest.mark.parametrize("template", TEMPLATE_FILES, ids=lambda p: p.name)
def test_element_ids_are_unique(template: Path) -> None:
    """Duplicate ids make getElementById return whichever came first, which is
    how a handler ends up wired to the wrong element."""
    ids = re.findall(r'\bid="([^"]+)"', template.read_text(encoding="utf-8"))
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"{template.name}: duplicate element ids {sorted(duplicates)}"
