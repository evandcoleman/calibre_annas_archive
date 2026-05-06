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

    def test_partner_server_links_are_invisible_to_current_xpath(self):
        """Document a known gap: Partner Server links live in a wrapped <ul>.

        The current xpath uses `/ul` (direct child). Partner Server links sit
        inside `div.mb-4 > ul.list-inside`, so they're missed. They're also
        DDoS-Guard gated and not auto-followable today, so this is a known
        limitation rather than something to fix without a working follow-
        through.
        """
        from lxml import html

        body = _read(DETAIL_FIXTURE)
        doc = html.fromstring(body)
        plugin_xpath = (
            '//div[@id="md5-panel-downloads"]/ul[contains(@class, "list-inside")]'
            '/li/a[contains(@class, "js-download-link")]'
        )
        broad_xpath = (
            '//div[@id="md5-panel-downloads"]'
            '//a[contains(@class, "js-download-link")]'
        )
        plugin_count = len(doc.xpath(plugin_xpath))
        broad_count = len(doc.xpath(broad_xpath))
        assert broad_count > plugin_count, (
            "fixture should contain Partner Server links the broad xpath catches"
        )

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

        with patch.object(aa_mod, "browser", return_value=FakeBrowser(lambda u: detail_resp)), \
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
