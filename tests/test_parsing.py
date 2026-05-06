"""Parsing tests for `_search` against fixture and (optionally) live HTML.

The fixture under `tests/fixtures/search_war_and_peace.html` was captured from
https://annas-archive.gl/search?q=war+and+peace&display=table and represents
the current shape of the search page. If Anna's Archive changes its layout the
fixture-based tests will fail with a clear signal that the parser xpath needs
updating.

The live test is gated behind `@pytest.mark.network` and is skipped by default.
Run with `pytest -m network` to opt in.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from calibre_plugins.store_annas_archive import annas_archive as aa_mod
from calibre_plugins.store_annas_archive.annas_archive import AnnasArchiveStore
from calibre_plugins.store_annas_archive.constants import DEFAULT_MIRRORS

from .test_annas_archive import FakeBrowser, FakeResponse, make_store


FIXTURE_DIR = Path(__file__).parent / "fixtures"
SEARCH_FIXTURE = FIXTURE_DIR / "search_war_and_peace.html"


def _fake_browser_returning(body: bytes, *, code: int = 200, url: str = "https://annas-archive.gl/"):
    resp = FakeResponse(body, code=code, url=url)
    return FakeBrowser(lambda u: resp)


@pytest.mark.unit
class TestSearchPageParsing:
    @pytest.fixture(scope="class")
    def fixture_html(self) -> bytes:
        if not SEARCH_FIXTURE.exists():
            pytest.skip(f"missing fixture: {SEARCH_FIXTURE}")
        return SEARCH_FIXTURE.read_bytes()

    def _run(self, store, body, max_results=10):
        br = _fake_browser_returning(body)
        with patch.object(aa_mod, "browser", return_value=br):
            return list(store._search(
                "{base}/search?page={page}&q=x&display=table",
                max_results=max_results,
                timeout=5,
            ))

    def test_yields_results(self, fixture_html):
        store = make_store()
        results = self._run(store, fixture_html, max_results=10)
        assert len(results) == 10, f"expected 10 results, got {len(results)}"

    def test_results_have_md5_detail_item(self, fixture_html):
        store = make_store()
        results = self._run(store, fixture_html, max_results=5)
        for r in results:
            # md5s are 32-char hex strings
            assert isinstance(r.detail_item, str)
            assert len(r.detail_item) == 32, r.detail_item
            assert all(c in "0123456789abcdef" for c in r.detail_item.lower()), r.detail_item

    def test_results_have_title_author_format(self, fixture_html):
        store = make_store()
        results = self._run(store, fixture_html, max_results=5)
        for r in results:
            assert r.title.strip(), f"empty title: {r.detail_item}"
            assert r.author.strip(), f"empty author: {r.detail_item}"
            # `formats` is upper-cased and must be one of the formats Calibre
            # advertises support for in __init__.py.
            assert r.formats in {
                "EPUB", "MOBI", "PDF", "AZW3", "CBR", "CBZ", "FB2", "DJVU", "TXT", ""
            }, f"unexpected format: {r.formats!r}"

    def test_results_marked_drm_unlocked_and_free(self, fixture_html):
        store = make_store()
        results = self._run(store, fixture_html, max_results=3)
        for r in results:
            assert r.price == "$0.00"
            assert r.drm == "unlocked"

    def test_max_results_caps_yield(self, fixture_html):
        store = make_store()
        results = self._run(store, fixture_html, max_results=3)
        assert len(results) == 3

    def test_working_mirror_recorded(self, fixture_html):
        store = make_store()
        self._run(store, fixture_html, max_results=1)
        assert store.working_mirror == DEFAULT_MIRRORS[0]


@pytest.mark.unit
class TestMirrorListNotMutated:
    """Regression: `_search` does `mirrors.remove(...); mirrors.insert(0, ...)`.

    If the list returned by `self.config.get('mirrors', ...)` is the same
    object stored in config (or the shared default), repeated searches mutate
    user state. The default-list case below is the dangerous one: the
    `DEFAULT_MIRRORS` module-level list gets re-ordered for the rest of the
    Calibre session.
    """

    def test_default_mirror_list_is_not_reordered_after_search(self):
        original = list(DEFAULT_MIRRORS)
        store = make_store()  # config={} so mirrors fall back to DEFAULT_MIRRORS
        store.working_mirror = DEFAULT_MIRRORS[1]  # pretend last run used #1

        body = b"<html><body><table></table></body></html>"
        br = _fake_browser_returning(body)
        with patch.object(aa_mod, "browser", return_value=br):
            list(store._search(
                "{base}/search?page={page}&q=x&display=table",
                max_results=1,
                timeout=5,
            ))

        assert DEFAULT_MIRRORS == original, (
            "DEFAULT_MIRRORS was mutated by _search — repeated searches "
            "permanently re-order the module-level default."
        )


# ---------------------------------------------------------------------------
# Live integration — opt-in
# ---------------------------------------------------------------------------


@pytest.mark.network
class TestLiveAnnasArchive:
    """Hit the real site. Run with: pytest -m network"""

    HOSTS = [
        "https://annas-archive.gl",
        "https://annas-archive.org",
        "https://annas-archive.li",
        "https://annas-archive.se",
    ]

    def _fetch(self, host: str, path: str) -> bytes | None:
        from urllib.error import URLError
        from urllib.request import Request, urlopen
        req = Request(host + path, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with closing(urlopen(req, timeout=20)) as resp:
                if 200 <= resp.code < 300:
                    return resp.read()
        except (URLError, TimeoutError, OSError):
            return None
        return None

    def test_at_least_one_default_mirror_is_reachable(self):
        for host in self.HOSTS:
            body = self._fetch(host, "/")
            if body and b"Anna" in body:
                return
        pytest.fail(
            "None of the configured mirrors were reachable. "
            "The plugin's DEFAULT_MIRRORS list is likely stale."
        )

    def test_search_returns_parseable_results(self):
        body = None
        host_used = None
        for host in self.HOSTS:
            body = self._fetch(host, "/search?q=war+and+peace&display=table")
            if body:
                host_used = host
                break
        if not body:
            pytest.skip("no mirror reachable")

        store = make_store()
        br = _fake_browser_returning(body, url=host_used + "/")
        with patch.object(aa_mod, "browser", return_value=br):
            results = list(store._search(
                "{base}/search?page={page}&q=war+and+peace&display=table",
                max_results=5,
                timeout=20,
            ))
        assert results, f"no results parsed from {host_used}"
        for r in results:
            assert r.detail_item and r.title, (
                f"missing fields from {host_used}: md5={r.detail_item!r} "
                f"title={r.title!r}"
            )
