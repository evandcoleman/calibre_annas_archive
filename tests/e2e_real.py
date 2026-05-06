"""End-to-end test against live Anna's Archive.

Uses real mechanize.Browser (same library Calibre uses). Run directly:

    .venv/bin/python -m tests.e2e_real <md5>

Prints exactly what search_result.downloads ends up containing — no mocks.
"""
from __future__ import annotations

import sys
from contextlib import closing
from urllib.parse import urlsplit

# Install calibre/qt stubs before importing the plugin.
from tests.conftest import _install_stub_modules

_install_stub_modules()

import mechanize  # noqa: E402

from calibre_plugins.store_annas_archive import annas_archive as aa_mod  # noqa: E402
from calibre_plugins.store_annas_archive.annas_archive import AnnasArchiveStore  # noqa: E402
from calibre.gui2.store.search_result import SearchResult  # noqa: E402


def real_browser():
    br = mechanize.Browser()
    br.set_handle_robots(False)
    br.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
    ]
    return br


def make_store():
    s = AnnasArchiveStore.__new__(AnnasArchiveStore)
    s.gui = None
    s.name = "Anna's Archive"
    s.config = {}
    s.working_mirror = "https://annas-archive.gl"
    return s


def main(md5: str, fmt: str = "PDF") -> None:
    aa_mod.browser = real_browser  # type: ignore[assignment]

    store = make_store()
    sr = SearchResult()
    sr.detail_item = md5
    sr.formats = fmt

    print(f"# fetching detail for md5={md5} fmt={fmt}")
    print(f"# working_mirror = {store.working_mirror}")
    try:
        store.get_details(sr, timeout=30)
    except Exception as exc:
        print(f"!! get_details raised: {type(exc).__name__}: {exc}")
        raise

    print(f"\n# downloads ({len(sr.downloads)}):")
    for name, url in sr.downloads.items():
        host = urlsplit(url).netloc
        print(f"  {name:40s}  [{host}]  {url[:100]}")
    if not sr.downloads:
        print("  (empty — nothing populated)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tests.e2e_real <md5> [format]", file=sys.stderr)
        sys.exit(2)
    md5 = sys.argv[1]
    fmt = sys.argv[2] if len(sys.argv) > 2 else "PDF"
    main(md5, fmt)
