from contextlib import closing
from http.client import RemoteDisconnected
from math import ceil
from typing import Generator
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urljoin
from urllib.request import urlopen, Request

from calibre import browser
from calibre.gui2 import open_url
from calibre.gui2.store import StorePlugin
from calibre.gui2.store.search_result import SearchResult
from calibre.gui2.store.web_store_dialog import WebStoreDialog
from calibre_plugins.store_annas_archive.constants import DEFAULT_MIRRORS, RESULTS_PER_PAGE, SearchOption
from lxml import html

try:
    from qt.core import QUrl
except (ImportError, ModuleNotFoundError):
    from PyQt5.Qt import QUrl

SearchResults = Generator[SearchResult, None, None]


class AnnasArchiveStore(StorePlugin):

    def __init__(self, gui, name, config=None, base_plugin=None):
        super().__init__(gui, name, config, base_plugin)
        self.working_mirror = None

    def _ordered_mirrors(self):
        """Return a fresh list of mirrors, last working one first.

        Always returns a new list so callers can reorder without mutating the
        configured list or the module-level DEFAULT_MIRRORS.
        """
        mirrors = list(self.config.get('mirrors', DEFAULT_MIRRORS))
        if self.working_mirror and self.working_mirror in mirrors:
            mirrors.remove(self.working_mirror)
            mirrors.insert(0, self.working_mirror)
        return mirrors

    def _search(self, url: str, max_results: int, timeout: int) -> SearchResults:
        br = browser()
        counter = max_results

        for page in range(1, ceil(max_results / RESULTS_PER_PAGE) + 1):
            mirrors = self._ordered_mirrors()
            doc = None
            last_error = None
            for mirror in mirrors:
                try:
                    with closing(br.open(url.format(base=mirror, page=page), timeout=timeout)) as resp:
                        body = resp.read()
                except (HTTPError, URLError, TimeoutError, RemoteDisconnected, OSError) as exc:
                    last_error = exc
                    continue
                self.working_mirror = mirror
                doc = html.fromstring(body)
                break
            if doc is None:
                self.working_mirror = None
                raise Exception(
                    "No working mirrors of Anna's Archive found"
                    + (f" (last error: {last_error})" if last_error else '')
                )

            books = doc.xpath('//table/tr')
            for book in books:
                if counter <= 0:
                    break

                columns = book.findall("td")
                s = SearchResult()

                cover = columns[0].xpath('./a[@tabindex="-1"]')
                if cover:
                    cover = cover[0]
                else:
                    continue
                s.detail_item = cover.get('href', '').split('/')[-1]
                if not s.detail_item:
                    continue

                s.cover_url = ''.join(cover.xpath('(./span/img/@src)[1]'))
                s.title = ''.join(columns[1].xpath('./a/span/text()'))
                s.author = ''.join(columns[2].xpath('./a/span/text()'))
                s.formats = ''.join(columns[9].xpath('./a/span/text()')).upper()

                s.price = '$0.00'
                s.drm = SearchResult.DRM_UNLOCKED

                counter -= 1
                yield s

    def search(self, query, max_results=10, timeout=60) -> SearchResults:
        url = f'{{base}}/search?page={{page}}&q={quote_plus(query)}&display=table'
        search_opts = self.config.get('search', {})
        for option in SearchOption.options:
            value = search_opts.get(option.config_option, ())
            if isinstance(value, str):
                value = (value,)
            for item in value:
                url += f'&{option.url_param}={item}'
        yield from self._search(url, max_results, timeout)

    def open(self, parent=None, detail_item=None, external=False):
        if detail_item:
            url = self._get_url(detail_item)
        else:
            if self.working_mirror is not None:
                url = self.working_mirror
            else:
                url = self.config.get('mirrors', DEFAULT_MIRRORS)[0]
        if external or self.config.get('open_external', False):
            open_url(QUrl(url))
        else:
            d = WebStoreDialog(self.gui, self.working_mirror, parent, url)
            d.setWindowTitle(self.name)
            d.set_tags(self.config.get('tags', ''))
            d.exec()

    def get_details(self, search_result: SearchResult, timeout=60):
        if not search_result.formats:
            return

        _format = '.' + search_result.formats.lower()

        link_opts = self.config.get('link', {})
        url_extension = link_opts.get('url_extension', False)
        content_type = link_opts.get('content_type', False)

        br = browser()
        doc = self._open_detail_page(br, search_result.detail_item, timeout)
        if doc is None:
            return

        # `//ul` (descendant) instead of `/ul` (direct child) — AA's Partner
        # Server links live in a wrapped <ul> one level deeper than the
        # external-mirror list, and the original xpath missed them entirely.
        for link in doc.xpath('//div[@id="md5-panel-downloads"]//ul[contains(@class, "list-inside")]/li/a[contains(@class, "js-download-link")]'):
            url = link.get('href')
            link_text = ''.join(link.itertext())

            try:
                if link_text.startswith('Fast Partner Server') or link_text.startswith('Slow Partner Server'):
                    # First-party AA download. /fast_download/ requires a
                    # membership cookie; /slow_download/ serves a wait page.
                    # Pass the absolute URL through as-is — Calibre's browser
                    # already carries any cookies set during the search flow.
                    url = urljoin(self.working_mirror or '', url)
                elif link_text == 'Libgen.li':
                    url = self._get_libgen_link(url, br, timeout=timeout)
                elif link_text == 'Libgen.rs Fiction' or link_text == 'Libgen.rs Non-Fiction':
                    url = self._get_libgen_nonfiction_link(url, br, timeout=timeout)
                elif link_text.startswith('Sci-Hub'):
                    url = self._get_scihub_link(url, br, timeout=timeout)
                elif link_text == 'Z-Library':
                    url = self._get_zlib_link(url, br, timeout=timeout)
                else:
                    continue
            except (HTTPError, URLError, TimeoutError, RemoteDisconnected, OSError):
                # One bad mirror shouldn't kill the whole get_details. Skip it
                # and move on so the user still sees other download options.
                continue

            if not url:
                continue

            # Takes longer, but more accurate
            if content_type:
                try:
                    with urlopen(Request(url, method='HEAD'), timeout=timeout) as resp:
                        if resp.info().get_content_maintype() != 'application':
                            continue
                except (HTTPError, URLError, TimeoutError, RemoteDisconnected, OSError):
                    pass
            elif url_extension:
                # Filter to URLs that look like direct file downloads (URL
                # path ends with the requested extension). This is a fast
                # heuristic — opt-in because most of Anna's Archive's links
                # are session URLs that don't carry the extension.
                params = url.find("?")
                if params < 0:
                    params = None
                if not url.endswith(_format, 0, params):
                    continue
            search_result.downloads[f"{link_text}.{search_result.formats}"] = url

    def _open_detail_page(self, br, md5: str, timeout: int):
        """Fetch the AA md5 detail page, failing over across mirrors."""
        last_error = None
        first_try = self.working_mirror
        candidates = []
        if first_try:
            candidates.append(first_try)
        for m in self.config.get('mirrors', DEFAULT_MIRRORS):
            if m and m not in candidates:
                candidates.append(m)
        for mirror in candidates:
            try:
                with closing(br.open(f"{mirror}/md5/{md5}", timeout=timeout)) as resp:
                    body = resp.read()
            except (HTTPError, URLError, TimeoutError, RemoteDisconnected, OSError) as exc:
                last_error = exc
                continue
            self.working_mirror = mirror
            return html.fromstring(body)
        return None

    @staticmethod
    def _get_libgen_link(url: str, br, timeout: int = 30) -> str:
        with closing(br.open(url, timeout=timeout)) as resp:
            doc = html.fromstring(resp.read())
            base = resp.geturl()
        href = ''.join(doc.xpath('//a[h2[text()="GET"]]/@href'))
        if not href:
            return ''
        return urljoin(base, href)

    @staticmethod
    def _get_libgen_nonfiction_link(url: str, br, timeout: int = 30) -> str:
        with closing(br.open(url, timeout=timeout)) as resp:
            doc = html.fromstring(resp.read())
        url = ''.join(doc.xpath('//h2/a[text()="GET"]/@href'))
        return url

    @staticmethod
    def _get_scihub_link(url, br, timeout: int = 30):
        with closing(br.open(url, timeout=timeout)) as resp:
            doc = html.fromstring(resp.read())
            base = resp.geturl()
        src = ''.join(doc.xpath('//embed[@id="pdf"]/@src'))
        if src:
            return urljoin(base, src)

    @staticmethod
    def _get_zlib_link(url, br, timeout: int = 30):
        with closing(br.open(url, timeout=timeout)) as resp:
            doc = html.fromstring(resp.read())
            base = resp.geturl()
        href = ''.join(doc.xpath('//a[contains(@class, "addDownloadedBook")]/@href'))
        if href:
            return urljoin(base, href)

    def _get_url(self, md5):
        return f"{self.working_mirror}/md5/{md5}"

    def config_widget(self):
        from calibre_plugins.store_annas_archive.config import ConfigWidget
        return ConfigWidget(self)

    def save_settings(self, config_widget):
        config_widget.save_settings()
