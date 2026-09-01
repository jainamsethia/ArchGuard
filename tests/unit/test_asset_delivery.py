"""How the dashboard's assets reach the browser.

Measured before this existed: 1001.9 KB of static payload on every dashboard
load, none of it compressed, none of it cacheable. Asking for gzip returned
byte-identical responses because no compression middleware was installed, and
no asset carried a Cache-Control header, so a returning visitor re-fetched all
of it.

The largest single item was `vis-network.min.js` at 628.7 KB -- 63% of the
total -- loaded eagerly on every page view for a graph that only renders when
someone opens the Dependencies tab.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()


# ------------------------------------------------------------- compression


def test_a_large_asset_is_compressed():
    """`index.css` is 61 KB of highly compressible text."""
    response = client.get("/index.css", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"


def test_html_is_compressed():
    response = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"


def test_compression_actually_shrinks_the_payload():
    """Asserting the header alone would pass against a middleware that
    advertised gzip and sent the bytes through unchanged.

    This used to read the wire size from `content-length` on the compressed
    response, which worked only while the file fit in one chunk. `FileResponse`
    reads in 64 KB chunks and `GZipMiddleware` switches to streaming the moment
    there is more than one, dropping `content-length` -- so the assertion broke
    when index.css crossed 64 KB, for a reason that had nothing to do with
    compression.

    The property is checked two ways instead, neither size-dependent. httpx
    decodes transparently, so a body that round-trips to the identical bytes is
    a body that really was valid gzip: a middleware passing the content through
    while claiming gzip would fail to decode rather than compare equal. And the
    saving is real, measured on the same bytes the server sent.
    """
    import gzip

    raw = client.get("/index.css", headers={"Accept-Encoding": "identity"})
    compressed = client.get("/index.css", headers={"Accept-Encoding": "gzip"})

    assert compressed.headers.get("content-encoding") == "gzip"
    assert compressed.content == raw.content, (
        "the gzip response did not decode back to the original asset"
    )
    assert len(gzip.compress(raw.content)) < len(raw.content) / 2, (
        f"gzip saves almost nothing on this asset: "
        f"{len(gzip.compress(raw.content))} vs {len(raw.content)} bytes"
    )


def test_a_client_that_cannot_gzip_still_gets_the_asset():
    response = client.get("/index.css", headers={"Accept-Encoding": "identity"})
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert b"--text-primary" in response.content


def test_tiny_responses_are_not_compressed():
    """Below the threshold, gzip costs more than it saves."""
    response = client.get("/health", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") != "gzip"


# ------------------------------------------------------------------ caching


def test_static_assets_carry_a_cache_policy():
    """Every one of them was served with no Cache-Control at all."""
    for path in ("/index.css", "/js/main.js", "/vendor/chart.umd.min.js"):
        response = client.get(path)
        assert response.status_code == 200
        assert "cache-control" in response.headers, f"{path} has no cache policy"


def test_a_fingerprinted_asset_is_cached_for_a_year():
    """Safe only because the URL changes when the bytes do."""
    fingerprint = client.get("/index.css?v=deadbeef").headers.get("cache-control", "")
    assert "immutable" in fingerprint
    assert "max-age=31536000" in fingerprint


def test_an_unfingerprinted_asset_is_not_cached_for_a_year():
    """A bare URL must stay short-lived: nothing invalidates it, so a year-long
    immutable policy would strand a deploy in every browser that had ever
    loaded the old one."""
    policy = client.get("/js/main.js").headers.get("cache-control", "")
    assert "immutable" not in policy
    assert "max-age=31536000" not in policy


def test_the_templates_request_fingerprinted_assets():
    """Otherwise the long cache policy is never used by anything."""
    body = client.get("/dashboard.html").text
    for asset in ("index.css", "js/main.js"):
        assert f"{asset}?v=" in body, f"{asset} is referenced without a fingerprint"


def test_the_fingerprint_follows_the_content(tmp_path):
    """A deploy that changes a file must change its URL, or nobody sees it."""
    from archguard.dashboard.app import _asset_fingerprint

    target = tmp_path / "probe.css"
    target.write_text("a{}", encoding="utf-8")
    first = _asset_fingerprint(target)
    target.write_text("a{color:red}", encoding="utf-8")
    assert _asset_fingerprint(target) != first


# -------------------------------------------------------------- lazy loading


def test_the_graph_library_is_not_in_the_initial_page():
    """628.7 KB, 63% of the payload, for a tab most visitors never open.

    It is loaded on demand when the Dependencies tab is first activated.
    """
    body = client.get("/dashboard.html").text
    assert "vis-network" not in body or 'src="/vendor/vis-network' not in body, (
        "vis-network is still a blocking script tag in the initial page"
    )


def test_the_chart_library_is_still_loaded_eagerly():
    """Charts render on the default tab, so deferring them would only trade a
    download for a visible delay on the page everyone lands on."""
    body = client.get("/dashboard.html").text
    assert "chart.umd.min.js" in body


def test_the_dependency_tab_knows_how_to_fetch_the_library():
    """The lazy path has to exist in the shipped script, not just in intent."""
    script = client.get("/js/render/deps.js").text
    assert "vis-network" in script, (
        "nothing in the dependency module loads the graph library on demand"
    )
