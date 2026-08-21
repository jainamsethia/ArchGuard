"""The landing page, and the pages a real site is expected to have.

`index.html` was a URL input box titled "ArchGuard - Job Submission". No value
proposition, no explanation of what the four layers measure, no example output,
no favicon, no meta description, no privacy policy, and a 404 that returned
`{"detail":"Not Found"}` as JSON to a browser.

A visitor who does not already know what ArchGuard is has no reason to paste a
repository URL into it. That is the difference between a tool someone was sent
a link to and a product someone can find.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()


def _index() -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.text


def _without_comments(html: str) -> str:
    """HTML comments are not page content.

    Several comments in these templates quote the copy they replaced, to say
    why it went. A test looking for the absence of that copy has to read what
    the visitor reads.
    """
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


# ------------------------------------------------------------- the pitch


def test_the_page_leads_with_what_the_product_does():
    """Above the input box, in plain language.

    The old page's entire explanation was the subtitle "Advanced Architectural
    AI Intelligence", which names a category rather than an outcome.

    Asserted against the hero specifically, not against the page text. An
    earlier version of this test looked for keywords anywhere in the document
    and passed while the hero was missing entirely -- the words it wanted
    happened to appear further down in the layer descriptions.
    """
    body = _without_comments(_index())
    hero = re.search(r'<header class="hero">(.*?)</header>', body, re.S)
    assert hero, "the page has no hero section"
    text = re.sub(r"<[^>]+>", " ", hero.group(1)).lower()

    assert "github" in text, "the hero never says what you give it"
    assert re.search(r"health|score", text), "the hero never says what you get back"
    assert len(text.split()) > 25, "the hero is too short to explain anything"


def test_the_old_category_subtitle_is_gone():
    """"Advanced Architectural AI Intelligence" told a visitor nothing."""
    assert "Advanced Architectural AI Intelligence" not in _without_comments(_index())


def test_the_form_is_not_the_first_thing_on_the_page():
    """The pitch comes before the input box, not after it."""
    body = _index()
    assert body.index('class="hero"') < body.index('id="github-url"')


def test_the_four_layers_are_explained():
    """Each one, in words a visitor can evaluate.

    They are the product. A page that mentions "4-layer analysis" without
    saying what the layers look for is asking for trust it has not earned.
    """
    body = _index().lower()
    for phrase in ("import", "coupling", "drift", "duplication"):
        assert phrase in body, f"the landing page never mentions {phrase}"


def test_there_is_a_worked_example_to_look_at():
    """A link to a real report, so the product demonstrates itself.

    The cheapest thing that answers "what do I actually get?" without asking
    anyone to hand over a repository URL first.
    """
    assert 'href="/example"' in _index()


def test_the_example_report_resolves():
    response = client.get("/example")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_the_example_is_a_real_measured_run_not_invented_numbers():
    """It reports what ArchGuard measured on its own repository.

    Made-up numbers on a landing page for a measurement product would be a
    strange thing to ship.
    """
    body = client.get("/example").text
    assert "ArchGuard" in body
    # A score and a grade, the two things the report leads with.
    assert re.search(r"\d\d\.\d", body), "no measured score shown"


# --------------------------------------------------------------- the head


def test_the_title_describes_the_product():
    title = re.search(r"<title>(.*?)</title>", _index(), re.S)
    assert title, "no <title>"
    text = title.group(1).strip()
    assert "Job Submission" not in text, "the title still names an internal step"
    assert "ArchGuard" in text


def test_there_is_a_meta_description():
    """What a search result shows. Without it the engine invents one."""
    body = _index()
    match = re.search(r'<meta name="description" content="([^"]{40,320})"', body)
    assert match, "no usable meta description"


def test_link_previews_will_render():
    """Open Graph and Twitter cards.

    The most common way this link travels is pasted into a chat window. With no
    cards it arrives as a bare URL.
    """
    body = _index()
    for tag in ('property="og:title"', 'property="og:description"', 'property="og:type"'):
        assert tag in body, f"missing {tag}"
    assert 'name="twitter:card"' in body


def test_there_is_a_favicon():
    body = _index()
    assert 'rel="icon"' in body
    assert client.get("/favicon.svg").status_code == 200


# ------------------------------------------------------- the boring pages


@pytest.mark.parametrize("path", ["/privacy", "/terms"])
def test_the_policy_pages_exist(path):
    """A product that analyses people's repositories should say what it keeps."""
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_the_privacy_page_says_what_is_stored():
    body = client.get("/privacy").text.lower()
    for topic in ("repositor", "github", "delete"):
        assert topic in body, f"the privacy page never mentions {topic}"


def test_robots_txt_is_served():
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "sitemap" in response.text.lower() or "user-agent" in response.text.lower()


def test_robots_does_not_invite_crawling_of_user_data():
    """Result pages belong to whoever ran them, not to a search index."""
    body = client.get("/robots.txt").text
    assert "Disallow: /api/" in body
    assert "Disallow: /dashboard.html" in body


# ------------------------------------------------------------------- 404


def test_a_mistyped_url_gets_a_page_not_a_json_blob():
    """It returned {"detail":"Not Found"} with content-type application/json.

    That is the right answer for an API client and the wrong one for a person
    who mistyped a URL.
    """
    response = client.get("/no-such-page", headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "<html" in response.text.lower()


def test_the_404_page_offers_a_way_back():
    body = client.get("/no-such-page", headers={"Accept": "text/html"}).text
    assert 'href="/"' in body


def test_an_api_client_still_gets_json():
    """Content negotiation, not a blanket change.

    The /api catch-all exists so an unknown API path is a JSON 404 rather than
    falling through to StaticFiles; that must keep working.
    """
    response = client.get("/api/v1/no-such-endpoint")
    assert response.status_code == 404
    assert "application/json" in response.headers["content-type"]


def test_a_request_that_wants_json_gets_json():
    response = client.get("/no-such-page", headers={"Accept": "application/json"})
    assert response.status_code == 404
    assert "application/json" in response.headers["content-type"]


# ----------------------------------------------------------- reachability


@pytest.mark.parametrize("path", ["/", "/example", "/privacy", "/terms", "/robots.txt"])
def test_the_public_pages_need_no_session(path):
    """A first-time visitor has no session and must still be able to read.

    Asked from a non-loopback address, so the local-development fallback in
    `_identity` cannot answer for them.
    """
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()
    remote = TestClient(app, client=("203.0.113.11", 5555))
    assert remote.get(path).status_code == 200


def test_the_public_pages_carry_the_csp_nonce():
    """They are rendered through Jinja like the rest, not served as static files.

    A static file would miss the per-request nonce and its inline theme script
    would be blocked.
    """
    for path in ("/", "/example", "/privacy", "/terms"):
        assert "nonce-" in client.get(path).headers.get("content-security-policy", "")


def test_a_route_that_raises_404_keeps_its_own_message():
    """The HTML 404 handler must not flatten deliberate messages.

    Routes raise 404 with detail worth reading -- "No run found for job_id ..."
    -- and an earlier version of the handler replaced every one of them with a
    flat "Not Found". That turns an actionable API error into a blank one, and
    it is invisible until a client tries to tell two failures apart.
    """
    from fastapi import HTTPException
    from fastapi.routing import APIRoute

    from archguard.dashboard.app import app

    async def _raiser() -> None:
        raise HTTPException(status_code=404, detail="a specific, useful reason")

    # Inserted at the front, not appended. StaticFiles is mounted at "/" and
    # matches everything, so a route added after it never runs -- which is the
    # hazard test_route_structure::test_the_static_mount_is_last exists for,
    # and which this test walked straight into on its first draft.
    app.router.routes.insert(
        0, APIRoute("/__test_404_detail", _raiser, methods=["GET"])
    )

    try:
        response = client.get("/__test_404_detail")
        assert response.status_code == 404
        assert response.json()["detail"] == "a specific, useful reason"
    finally:
        app.router.routes = [
            r for r in app.router.routes
            if getattr(r, "path", None) != "/__test_404_detail"
        ]
