"""Verify `get_details` link discovery against today's live HTML.

Uses captured fixtures from annas-archive.gl, libgen.li, and z-lib.gd.

Status of the existing helpers (as of these fixtures):
- Libgen.li:  GET-button xpath still matches; one link found.
- Libgen.rs Fiction: external host (libgen.is) unreachable from CI sandbox;
  routing is exercised but the helper itself isn't run against a fixture.
- Z-Library:  z-lib.gd now serves a JS challenge interstitial — the
  `addDownloadedBook` class is no longer present. Helper returns None.
- Partner Server / Slow Partner Server: AA's first-party paths are
  DDoS-Guard gated and not auto-followable. Currently invisible to the
  plugin because the panel xpath uses `/ul` instead of `//ul`.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from calibre_plugins.store_annas_archive import annas_archive as aa_mod
from calibre_plugins.store_annas_archive.annas_archive import AnnasArchiveStore

from .test_annas_archive import FakeBrowser, FakeResponse, make_store


FIXTURE_DIR = Path(__file__).parent / "fixtures"
DETAIL_FIXTURE = FIXTURE_DIR / "md5_detail.html"
LIBGEN_LI_FIXTURE = FIXTURE_DIR / "libgen_li_ads.html"
ZLIB_FIXTURE = FIXTURE_DIR / "zlib.html"


def _read(path: Path) -> bytes:
    if not path.exists():
        pytest.skip(f"missing fixture: {path}")
    return path.read_bytes()


# ---------------------------------------------------------------------------
# Routing on the AA detail page
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetailPagePanelXpath:
    """The plugin's xpath for discovering download links."""

    def test_xpath_finds_external_links(self):
        from lxml import html

        body = _read(DETAIL_FIXTURE)
        doc = html.fromstring(body)
        xpath = (
            '//div[@id="md5-panel-downloads"]/ul[contains(@class, "list-inside")]'
            '/li/a[contains(@class, "js-download-link")]'
        )
        links = doc.xpath(xpath)
        assert len(links) > 0, "panel xpath found no links"

        texts = [' '.join(a.itertext()).strip() for a in links]
        # The known-routable sources should be visible somewhere in the list.
        assert any("Libgen.li" in t for t in texts), texts
        assert any("Z-Library" in t for t in texts), texts

    def test_partner_server_links_are_now_visible(self):
        """Regression: previously /ul (direct child) missed Partner Server links."""
        from lxml import html

        body = _read(DETAIL_FIXTURE)
        doc = html.fromstring(body)
        # The plugin's xpath should match Partner Server links on this fixture.
        new_xpath = (
            '//div[@id="md5-panel-downloads"]//ul[contains(@class, "list-inside")]'
            '/li/a[contains(@class, "js-download-link")]'
        )
        texts = [' '.join(a.itertext()).strip() for a in doc.xpath(new_xpath)]
        assert any(t.startswith("Fast Partner Server") for t in texts), texts
        assert any(t.startswith("Slow Partner Server") for t in texts), texts

    def test_partner_server_urls_are_emitted_as_absolute(self):
        body = _read(DETAIL_FIXTURE)
        store = make_store()
        store.working_mirror = "https://annas-archive.gl"

        from calibre.gui2.store.search_result import SearchResult
        sr = SearchResult()
        sr.detail_item = "09e074defb9d86d006bbc70e0c2f980b"
        sr.formats = "EPUB"

        detail_resp = FakeResponse(body, url="https://annas-archive.gl/md5/x")

        with patch.object(aa_mod.time, "sleep"), \
             patch.object(aa_mod, "browser", return_value=FakeBrowser(lambda u: detail_resp)), \
             patch.object(AnnasArchiveStore, "_get_libgen_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_libgen_nonfiction_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_scihub_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_zlib_link", return_value=None):
            store.config["link"] = {"url_extension": False, "content_type": False}
            store.get_details(sr, timeout=5)

        # Every Partner Server label should be an entry, mapped to an
        # absolute AA URL on the working mirror.
        partner_keys = [k for k in sr.downloads if "Partner Server" in k]
        assert partner_keys, sr.downloads
        for key in partner_keys:
            url = sr.downloads[key]
            assert url.startswith("https://annas-archive.gl/"), url
            assert "/fast_download/" in url or "/slow_download/" in url, url

    def test_get_details_routes_known_link_types(self):
        """Run get_details with mocked helpers; assert which routes fire."""
        body = _read(DETAIL_FIXTURE)
        store = make_store()
        store.working_mirror = "https://annas-archive.gl"

        from calibre.gui2.store.search_result import SearchResult
        sr = SearchResult()
        sr.detail_item = "09e074defb9d86d006bbc70e0c2f980b"
        sr.formats = "EPUB"

        # `browser()` returns the stub used to fetch the detail page.
        detail_resp = FakeResponse(body, url="https://annas-archive.gl/md5/09e074defb9d86d006bbc70e0c2f980b")

        with patch.object(aa_mod.time, "sleep"), \
             patch.object(aa_mod, "browser", return_value=FakeBrowser(lambda u: detail_resp)), \
             patch.object(AnnasArchiveStore, "_get_libgen_link", return_value="https://libgen.li/file.epub") as m_lg, \
             patch.object(AnnasArchiveStore, "_get_libgen_nonfiction_link", return_value="https://libgen.is/file.epub") as m_lgnf, \
             patch.object(AnnasArchiveStore, "_get_scihub_link", return_value=None) as m_sh, \
             patch.object(AnnasArchiveStore, "_get_zlib_link", return_value=None) as m_zl:
            # Disable extension/content-type filtering so links survive the
            # routing stage and we observe routing choices directly.
            store.config["link"] = {"url_extension": False, "content_type": False}
            store.get_details(sr, timeout=5)

        # Libgen.rs Fiction is in this fixture and routes to nonfiction helper.
        assert m_lgnf.called
        # Libgen.li is in this fixture.
        assert m_lg.called
        # Z-Library appears twice; helper should be called (returned None so
        # nothing was added — that's expected current breakage).
        assert m_zl.called
        # No Sci-Hub link in this fixture.
        assert not m_sh.called

        # Survived links: only those whose helper returned a non-None URL.
        assert "Libgen.rs Fiction.EPUB" in sr.downloads
        assert "Libgen.li.EPUB" in sr.downloads
        # Z-Library returned None on both occurrences.
        assert "Z-Library.EPUB" not in sr.downloads


# ---------------------------------------------------------------------------
# Helper xpaths against today's external pages
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHelpersAgainstLiveFixtures:
    def test_libgen_li_helper_extracts_get_link(self):
        body = _read(LIBGEN_LI_FIXTURE)
        resp = FakeResponse(body, url="https://libgen.li/ads.php?md5=09e074defb9d86d006bbc70e0c2f980b")
        br = MagicMock()
        br.open.return_value = resp

        url = AnnasArchiveStore._get_libgen_link(
            "https://libgen.li/ads.php?md5=09e074defb9d86d006bbc70e0c2f980b", br
        )
        assert url, "Libgen.li helper must extract the GET URL"
        assert url.startswith("https://libgen.li/"), url
        assert "//" not in url[len("https://"):], f"double slash in {url}"
        # The GET link points to get.php with the md5 + a session key.
        assert "get.php" in url
        assert "md5=" in url

    def test_helpers_accept_timeout(self):
        """Inner br.open() calls in every helper must pass timeout through."""
        body = b"<html><body></body></html>"
        for helper, kwargs in [
            (AnnasArchiveStore._get_libgen_link, {}),
            (AnnasArchiveStore._get_libgen_nonfiction_link, {}),
            (AnnasArchiveStore._get_scihub_link, {}),
            (AnnasArchiveStore._get_zlib_link, {}),
        ]:
            br = MagicMock()
            br.open.return_value = FakeResponse(body, url="https://example.com/x")
            helper("https://example.com/x", br, timeout=7)
            args, kw = br.open.call_args
            assert kw.get("timeout") == 7, (
                f"{helper.__name__} must forward timeout to br.open; got {kw}"
            )


@pytest.mark.unit
class TestGetDetailsRobustness:
    """Behaviors that protect users from cascading helper failures."""

    def _setup(self, store):
        from calibre.gui2.store.search_result import SearchResult
        sr = SearchResult()
        sr.detail_item = "09e074defb9d86d006bbc70e0c2f980b"
        sr.formats = "EPUB"
        return sr

    def test_one_helper_failing_does_not_kill_others(self):
        """A raising helper is skipped; remaining helpers still run."""
        body = _read(DETAIL_FIXTURE)
        store = make_store()
        store.working_mirror = "https://annas-archive.gl"
        sr = self._setup(store)

        from urllib.error import URLError
        detail_resp = FakeResponse(body, url="https://annas-archive.gl/md5/x")

        with patch.object(aa_mod.time, "sleep"), \
             patch.object(aa_mod, "browser", return_value=FakeBrowser(lambda u: detail_resp)), \
             patch.object(AnnasArchiveStore, "_get_libgen_link", side_effect=URLError("dead")), \
             patch.object(AnnasArchiveStore, "_get_libgen_nonfiction_link", return_value="https://libgen.is/file.epub"), \
             patch.object(AnnasArchiveStore, "_get_zlib_link", side_effect=TimeoutError("slow")):
            store.config["link"] = {"url_extension": False, "content_type": False}
            store.get_details(sr, timeout=5)

        # Libgen.li raised, Z-Library raised — both must NOT take down the
        # whole call, and the surviving helper's URL must still be added.
        assert "Libgen.rs Fiction.EPUB" in sr.downloads

    def test_url_extension_filter_keeps_matching_extensions(self):
        """Regression: the filter previously dropped URLs that DID match."""
        body = _read(DETAIL_FIXTURE)
        store = make_store()
        store.working_mirror = "https://annas-archive.gl"
        sr = self._setup(store)

        detail_resp = FakeResponse(body, url="https://annas-archive.gl/md5/x")

        with patch.object(aa_mod.time, "sleep"), \
             patch.object(aa_mod, "browser", return_value=FakeBrowser(lambda u: detail_resp)), \
             patch.object(AnnasArchiveStore, "_get_libgen_link", return_value="https://libgen.li/file.epub"), \
             patch.object(AnnasArchiveStore, "_get_libgen_nonfiction_link", return_value="https://libgen.is/file.epub"), \
             patch.object(AnnasArchiveStore, "_get_scihub_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_zlib_link", return_value=None):
            store.config["link"] = {"url_extension": True, "content_type": False}
            store.get_details(sr, timeout=5)

        # url_extension=True: a URL ending in `.epub` must survive.
        assert "Libgen.li.EPUB" in sr.downloads
        assert sr.downloads["Libgen.li.EPUB"] == "https://libgen.li/file.epub"

    def test_url_extension_filter_drops_non_matching_extensions(self):
        body = _read(DETAIL_FIXTURE)
        store = make_store()
        store.working_mirror = "https://annas-archive.gl"
        sr = self._setup(store)

        detail_resp = FakeResponse(body, url="https://annas-archive.gl/md5/x")

        with patch.object(aa_mod.time, "sleep"), \
             patch.object(aa_mod, "browser", return_value=FakeBrowser(lambda u: detail_resp)), \
             patch.object(AnnasArchiveStore, "_get_libgen_link", return_value="https://libgen.li/get.php?md5=abc"), \
             patch.object(AnnasArchiveStore, "_get_libgen_nonfiction_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_scihub_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_zlib_link", return_value=None):
            store.config["link"] = {"url_extension": True, "content_type": False}
            store.get_details(sr, timeout=5)

        # Session URL doesn't end in `.epub` → filter drops it.
        assert sr.downloads == {}

    def test_url_extension_default_off_keeps_session_urls(self):
        """Default behavior (no filter): session URLs are kept."""
        body = _read(DETAIL_FIXTURE)
        store = make_store()
        store.working_mirror = "https://annas-archive.gl"
        sr = self._setup(store)

        detail_resp = FakeResponse(body, url="https://annas-archive.gl/md5/x")

        with patch.object(aa_mod.time, "sleep"), \
             patch.object(aa_mod, "browser", return_value=FakeBrowser(lambda u: detail_resp)), \
             patch.object(AnnasArchiveStore, "_get_libgen_link", return_value="https://libgen.li/get.php?md5=abc&key=K"), \
             patch.object(AnnasArchiveStore, "_get_libgen_nonfiction_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_scihub_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_zlib_link", return_value=None):
            # No 'link' config at all → defaults apply
            store.config.pop("link", None)
            store.get_details(sr, timeout=5)

        assert "Libgen.li.EPUB" in sr.downloads

    def test_get_details_falls_back_to_next_mirror_on_detail_page_error(self):
        """If the working_mirror dies, get_details should try other mirrors."""
        from urllib.error import URLError

        store = make_store()
        store.working_mirror = "https://dead.example"
        store.config["mirrors"] = ["https://dead.example", "https://annas-archive.gl"]
        sr = self._setup(store)

        body = _read(DETAIL_FIXTURE)
        good = FakeResponse(body, url="https://annas-archive.gl/md5/x")

        def respond(url):
            if "dead.example" in url:
                raise URLError("nope")
            return good

        with patch.object(aa_mod, "browser", return_value=FakeBrowser(respond)), \
             patch.object(AnnasArchiveStore, "_get_libgen_link", return_value="https://libgen.li/file.epub"), \
             patch.object(AnnasArchiveStore, "_get_libgen_nonfiction_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_scihub_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_zlib_link", return_value=None):
            store.config["link"] = {"url_extension": False, "content_type": False}
            store.get_details(sr, timeout=5)

        assert store.working_mirror == "https://annas-archive.gl"
        assert "Libgen.li.EPUB" in sr.downloads

    def test_get_details_retries_same_mirror_on_transient_502(self):
        """Single 502 from the working mirror should not produce empty downloads."""
        from urllib.error import HTTPError
        import io

        body = _read(DETAIL_FIXTURE)
        store = make_store()
        store.working_mirror = "https://annas-archive.gl"
        store.config["mirrors"] = ["https://annas-archive.gl"]  # only one mirror — common user setup
        sr = self._setup(store)

        ok = FakeResponse(body, url="https://annas-archive.gl/md5/x")
        calls = {"n": 0}

        def respond(url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HTTPError(url, 502, "Bad Gateway", {}, io.BytesIO(b""))
            return ok

        with patch.object(aa_mod.time, "sleep"), \
             patch.object(aa_mod, "browser", return_value=FakeBrowser(respond)), \
             patch.object(AnnasArchiveStore, "_get_libgen_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_libgen_nonfiction_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_scihub_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_zlib_link", return_value=None):
            store.config["link"] = {"url_extension": False, "content_type": False}
            store.get_details(sr, timeout=5)

        # Despite the 502, retry succeeded and Partner Server links populate.
        assert any("Partner Server" in k for k in sr.downloads), sr.downloads

    def test_get_details_rejects_response_missing_panel(self):
        """A 200 with junk HTML (e.g. error page rendered as 200) shouldn't fool us."""
        store = make_store()
        store.working_mirror = "https://annas-archive.gl"
        store.config["mirrors"] = ["https://annas-archive.gl", "https://annas-archive.org"]
        sr = self._setup(store)

        good = FakeResponse(_read(DETAIL_FIXTURE), url="https://annas-archive.org/md5/x")
        junk = FakeResponse(b"<html><body><h1>maintenance</h1></body></html>", url="https://annas-archive.gl/md5/x")

        def respond(url):
            if "annas-archive.gl" in url:
                return junk
            return good

        with patch.object(aa_mod.time, "sleep"), \
             patch.object(aa_mod, "browser", return_value=FakeBrowser(respond)), \
             patch.object(AnnasArchiveStore, "_get_libgen_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_libgen_nonfiction_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_scihub_link", return_value=None), \
             patch.object(AnnasArchiveStore, "_get_zlib_link", return_value=None):
            store.config["link"] = {"url_extension": False, "content_type": False}
            store.get_details(sr, timeout=5)

        # gl returned 200 with junk HTML; plugin must reject it and fall back to .org
        assert store.working_mirror == "https://annas-archive.org"
        assert any("Partner Server" in k for k in sr.downloads)

    def test_get_details_silently_returns_when_all_mirrors_fail(self):
        """If every mirror dies, return without raising — no downloads added."""
        from urllib.error import URLError

        store = make_store()
        store.working_mirror = None
        store.config["mirrors"] = ["https://a.example", "https://b.example"]
        sr = self._setup(store)

        with patch.object(aa_mod.time, "sleep"), \
             patch.object(aa_mod, "browser", return_value=FakeBrowser(lambda u: (_ for _ in ()).throw(URLError("dead")))):
            store.get_details(sr, timeout=5)

        assert sr.downloads == {}

    def test_zlib_helper_returns_none_against_js_challenge(self):
        """Document the breakage: z-lib.gd now serves a JS challenge."""
        body = _read(ZLIB_FIXTURE)
        resp = FakeResponse(body, url="https://z-lib.gd/md5/09e074defb9d86d006bbc70e0c2f980b")
        br = MagicMock()
        br.open.return_value = resp

        url = AnnasArchiveStore._get_zlib_link(
            "https://z-lib.gd/md5/09e074defb9d86d006bbc70e0c2f980b", br
        )
        # The helper's xpath finds nothing in the JS-challenge page.
        assert url is None, (
            f"z-lib.gd is bot-protected; expected None, got {url!r}. "
            "If this starts returning a URL, the site is reachable again — "
            "verify the helper still produces the right thing."
        )
