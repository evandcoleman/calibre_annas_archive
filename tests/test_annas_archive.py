"""Tests for annas_archive.AnnasArchiveStore — URL building, helpers, parsing."""
from __future__ import annotations

import io
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from calibre_plugins.store_annas_archive import annas_archive as aa_mod
from calibre_plugins.store_annas_archive.annas_archive import AnnasArchiveStore
from calibre_plugins.store_annas_archive.constants import DEFAULT_MIRRORS


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_store(config=None):
    """Construct an AnnasArchiveStore without running StorePlugin.__init__.

    StorePlugin lives inside Calibre and we only need the methods on
    AnnasArchiveStore itself, so bypass __init__ and seed the attributes the
    methods touch.
    """
    store = AnnasArchiveStore.__new__(AnnasArchiveStore)
    store.gui = None
    store.name = "Anna's Archive"
    store.config = config if config is not None else {}
    store.working_mirror = None
    return store


class FakeResponse:
    """Mimic the file-like response returned by mechanize/calibre.browser."""

    def __init__(self, body: bytes, *, code: int = 200, url: str = "https://example.com/"):
        self._body = body
        self.code = code
        self._url = url

    def read(self):
        return self._body

    def geturl(self):
        return self._url

    # support `with closing(...)`
    def close(self):
        pass


class FakeBrowser:
    def __init__(self, responses):
        # responses: dict[url_substring, FakeResponse] OR callable(url) -> FakeResponse
        self._responses = responses
        self.calls = []

    def open(self, url, timeout=None):
        self.calls.append((url, timeout))
        if callable(self._responses):
            return self._responses(url)
        for key, resp in self._responses.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected url: {url}")


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchUrlConstruction:
    def _capture_url(self, store, query, max_results=10):
        captured = {}

        def fake_search(url, max_results, timeout):
            captured["url"] = url
            return iter(())

        with patch.object(store, "_search", side_effect=fake_search):
            list(store.search(query, max_results=max_results, timeout=30))
        return captured["url"]

    def test_search_query_is_url_encoded(self):
        store = make_store()
        url = self._capture_url(store, "war and peace")
        # The URL is a template with {base} and {page} placeholders left in
        # for `_search` to fill in. Verify the encoded query is present.
        assert "q=war+and+peace" in url
        assert "{base}" in url and "{page}" in url

    def test_special_characters_are_quoted(self):
        store = make_store()
        url = self._capture_url(store, "C++ & Rust?")
        qs = parse_qs(urlsplit(url.replace("{base}", "https://x").replace("{page}", "1")).query)
        assert qs["q"] == ["C++ & Rust?"]

    def test_display_table_param_present(self):
        store = make_store()
        url = self._capture_url(store, "anything")
        assert "display=table" in url

    def test_unselected_options_omit_url_params(self):
        store = make_store(config={"search": {}})
        url = self._capture_url(store, "x")
        # No filters configured: ext/sort/content/etc. should not appear.
        for forbidden in ("&ext=", "&sort=", "&content=", "&acc=", "&src=", "&lang="):
            assert forbidden not in url

    def test_selected_filetype_appended(self):
        store = make_store(config={"search": {"filetype": ["epub", "pdf"]}})
        url = self._capture_url(store, "x")
        assert "&ext=epub" in url
        assert "&ext=pdf" in url

    def test_string_option_values_are_treated_as_single_value(self):
        # The Order option saves a single string, not a list.
        store = make_store(config={"search": {"order": "newest"}})
        url = self._capture_url(store, "x")
        assert "&sort=newest" in url
        # not "&sort=n&sort=e&sort=w..." — i.e. it isn't iterated char-by-char
        assert url.count("&sort=") == 1

    def test_multiple_option_categories_combined(self):
        store = make_store(config={
            "search": {
                "order": "newest",
                "filetype": ["epub"],
                "language": ["en"],
                "content": ["book_fiction"],
            }
        })
        url = self._capture_url(store, "x")
        for fragment in ("&sort=newest", "&ext=epub", "&lang=en", "&content=book_fiction"):
            assert fragment in url


# ---------------------------------------------------------------------------
# Detail URL helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetUrl:
    def test_uses_working_mirror(self):
        store = make_store()
        store.working_mirror = "https://annas-archive.org"
        assert store._get_url("abc123") == "https://annas-archive.org/md5/abc123"

    def test_returns_path_even_without_mirror(self):
        # Currently `_get_url` will produce 'None/md5/...' if no mirror — this
        # is a known sharp edge: callers should ensure a mirror is set first.
        store = make_store()
        store.working_mirror = None
        assert "/md5/abc" in store._get_url("abc")


# ---------------------------------------------------------------------------
# Mirror failover via `_search`
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchMirrorFailover:
    def _patched_search(self, store, *, browser_responses, html_body=b"<html></html>"):
        fake_br = FakeBrowser(browser_responses)
        with patch.object(aa_mod, "browser", return_value=fake_br), \
             patch.object(aa_mod.time, "sleep"):
            return list(store._search(
                "{base}/search?page={page}&q=x&display=table",
                max_results=1,
                timeout=5,
            )), fake_br

    def test_first_mirror_used_when_healthy(self):
        store = make_store()
        ok = FakeResponse(b"<html><body><table></table></body></html>", code=200)

        def respond(url):
            return ok

        results, br = self._patched_search(store, browser_responses=respond)
        assert results == []  # empty table, no rows
        # Only the first mirror should be tried.
        assert len(br.calls) == 1
        assert DEFAULT_MIRRORS[0] in br.calls[0][0]
        assert store.working_mirror == DEFAULT_MIRRORS[0]

    def test_falls_back_to_next_mirror_on_url_error(self):
        from urllib.error import URLError
        store = make_store()
        ok = FakeResponse(b"<html><body><table></table></body></html>", code=200)

        def respond(url):
            if DEFAULT_MIRRORS[0] in url:
                raise URLError("Name or service not known")
            return ok

        results, br = self._patched_search(store, browser_responses=respond)
        assert results == []
        # Tries mirror 0 twice (retry), then succeeds on mirror 1.
        assert len(br.calls) == 3
        assert all(DEFAULT_MIRRORS[0] in c[0] for c in br.calls[:2])
        assert DEFAULT_MIRRORS[1] in br.calls[2][0]
        assert store.working_mirror == DEFAULT_MIRRORS[1]

    def test_retries_same_mirror_on_transient_error(self):
        """A single 502 should not give up on the mirror — retry once."""
        from urllib.error import HTTPError
        import io
        store = make_store()
        ok = FakeResponse(b"<html><body><table></table></body></html>", code=200)
        calls = {"n": 0}

        def respond(url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HTTPError(url, 502, "Bad Gateway", {}, io.BytesIO(b""))
            return ok

        results, br = self._patched_search(store, browser_responses=respond)
        assert results == []
        # First call 502'd, retry succeeded — 2 calls total, all to mirror 0.
        assert len(br.calls) == 2
        assert store.working_mirror == DEFAULT_MIRRORS[0]

    def test_raises_when_all_mirrors_fail(self):
        from urllib.error import URLError
        store = make_store()

        def respond(url):
            raise URLError("dead")

        with pytest.raises(Exception, match="No working mirrors"):
            self._patched_search(store, browser_responses=respond)
        assert store.working_mirror is None


# ---------------------------------------------------------------------------
# Download-link helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDownloadLinkHelpers:
    def test_libgen_link_extraction(self):
        html_body = b"""
        <html><body>
            <h2><a>not me</a></h2>
            <a href="get.php?md5=abc"><h2>GET</h2></a>
            <a><h2>OTHER</h2></a>
        </body></html>
        """
        resp = FakeResponse(html_body, url="https://libgen.li/edition.php?id=1")
        br = MagicMock()
        br.open.return_value = resp

        url = AnnasArchiveStore._get_libgen_link("https://libgen.li/edition.php?id=1", br)
        assert url == "https://libgen.li/get.php?md5=abc"

    def test_libgen_link_handles_absolute_href(self):
        """Regression: hrefs like '/get.php?...' must not produce '//get.php?...'."""
        html_body = b"""
        <html><body>
            <a href="/get.php?md5=abc"><h2>GET</h2></a>
        </body></html>
        """
        resp = FakeResponse(html_body, url="https://libgen.li/edition.php?id=1")
        br = MagicMock()
        br.open.return_value = resp

        url = AnnasArchiveStore._get_libgen_link("https://libgen.li/edition.php?id=1", br)
        assert url == "https://libgen.li/get.php?md5=abc", url

    def test_libgen_nonfiction_link_extraction(self):
        html_body = b"""
        <html><body>
            <h2><a href="https://example.com/book.pdf">GET</a></h2>
        </body></html>
        """
        resp = FakeResponse(html_body, url="https://library.lol/main/abc")
        br = MagicMock()
        br.open.return_value = resp

        url = AnnasArchiveStore._get_libgen_nonfiction_link("https://library.lol/main/abc", br)
        assert url == "https://example.com/book.pdf"

    def test_scihub_link_extraction(self):
        html_body = b"""
        <html><body>
            <embed id="pdf" src="//sci-hub.example/file.pdf"/>
        </body></html>
        """
        resp = FakeResponse(html_body, url="https://sci-hub.example/10.1/abc")
        br = MagicMock()
        br.open.return_value = resp

        url = AnnasArchiveStore._get_scihub_link("https://sci-hub.example/10.1/abc", br)
        assert url == "https://sci-hub.example/file.pdf"

    def test_scihub_returns_none_when_no_embed(self):
        resp = FakeResponse(b"<html></html>", url="https://sci-hub.example/x")
        br = MagicMock()
        br.open.return_value = resp
        assert AnnasArchiveStore._get_scihub_link("https://sci-hub.example/x", br) is None

    def test_zlib_link_extraction(self):
        html_body = b"""
        <html><body>
            <a class="btn addDownloadedBook" href="/dl/123/abc">Download</a>
        </body></html>
        """
        resp = FakeResponse(html_body, url="https://z-library.sk/book/123/abc")
        br = MagicMock()
        br.open.return_value = resp

        url = AnnasArchiveStore._get_zlib_link("https://z-library.sk/book/123/abc", br)
        assert url == "https://z-library.sk/dl/123/abc"


# ---------------------------------------------------------------------------
# get_details routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDetailsRouting:
    def test_no_format_returns_immediately(self):
        store = make_store()
        store.working_mirror = "https://annas-archive.org"

        from calibre.gui2.store.search_result import SearchResult
        sr = SearchResult()
        sr.formats = ""

        # Must not call browser() at all when there are no formats.
        with patch.object(aa_mod, "browser") as br:
            store.get_details(sr, timeout=5)
            br.assert_not_called()
