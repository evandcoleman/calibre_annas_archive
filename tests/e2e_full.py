"""Full end-to-end: search → get_details, mimicking Calibre's flow."""
from __future__ import annotations

import sys

from tests.conftest import _install_stub_modules
_install_stub_modules()

import mechanize  # noqa: E402

from calibre_plugins.store_annas_archive import annas_archive as aa_mod  # noqa: E402
from calibre_plugins.store_annas_archive.annas_archive import AnnasArchiveStore  # noqa: E402


def real_browser():
    br = mechanize.Browser()
    br.set_handle_robots(False)
    br.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    ]
    return br


def make_store():
    s = AnnasArchiveStore.__new__(AnnasArchiveStore)
    s.gui = None
    s.name = "Anna's Archive"
    s.config = {"mirrors": ["https://annas-archive.gl"]}
    s.working_mirror = None
    return s


def main(query: str) -> None:
    aa_mod.browser = real_browser  # type: ignore[assignment]

    store = make_store()
    print(f"# search query: {query!r}")
    results = list(store.search(query, max_results=5, timeout=30))
    print(f"# search returned {len(results)} results")
    print(f"# working_mirror after search: {store.working_mirror}")

    for i, sr in enumerate(results):
        print(f"\n[{i}] md5={sr.detail_item}")
        print(f"    title={sr.title!r}")
        print(f"    author={sr.author!r}")
        print(f"    formats={sr.formats!r}    <-- get_details returns early if this is empty")
        print(f"    cover={sr.cover_url[:80]}")

    if not results:
        return

    print("\n# fetching details for first result")
    first = results[0]
    store.get_details(first, timeout=30)
    print(f"\n# downloads ({len(first.downloads)}):")
    for name, url in first.downloads.items():
        print(f"  {name:40s} {url[:90]}")


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "war and peace"
    main(q)
