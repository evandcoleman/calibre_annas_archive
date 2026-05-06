"""Stub Calibre/Qt imports so the plugin modules can be imported under pytest.

Calibre ships its own embedded Python with calibre.* and qt.core available.
When running tests outside Calibre we need lightweight stand-ins so that
importing `calibre_plugins.store_annas_archive.annas_archive` does not blow up
on its calibre/qt imports. The stubs are intentionally minimal — tests should
mock anything they actually exercise.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = Path(__file__).resolve().parent.parent


def _install_stub_modules() -> None:
    # --- calibre and submodules ----------------------------------------------
    calibre = types.ModuleType("calibre")

    def _browser(*_, **__):  # pragma: no cover - replaced per-test
        return MagicMock(name="calibre.browser")

    calibre.browser = _browser  # type: ignore[attr-defined]
    sys.modules.setdefault("calibre", calibre)

    customize = types.ModuleType("calibre.customize")

    class StoreBase:  # minimal base class
        pass

    customize.StoreBase = StoreBase  # type: ignore[attr-defined]
    sys.modules.setdefault("calibre.customize", customize)

    gui2 = types.ModuleType("calibre.gui2")
    gui2.open_url = MagicMock(name="calibre.gui2.open_url")  # type: ignore[attr-defined]
    sys.modules.setdefault("calibre.gui2", gui2)

    gui2_store = types.ModuleType("calibre.gui2.store")

    class StorePlugin:
        def __init__(self, gui=None, name=None, config=None, base_plugin=None):
            self.gui = gui
            self.name = name
            self.config = config if config is not None else {}
            self.base_plugin = base_plugin

    gui2_store.StorePlugin = StorePlugin  # type: ignore[attr-defined]
    sys.modules.setdefault("calibre.gui2.store", gui2_store)

    search_result_mod = types.ModuleType("calibre.gui2.store.search_result")

    class SearchResult:
        DRM_UNLOCKED = "unlocked"
        DRM_LOCKED = "locked"

        def __init__(self):
            self.detail_item = None
            self.cover_url = ""
            self.title = ""
            self.author = ""
            self.formats = ""
            self.price = ""
            self.drm = None
            self.downloads = {}

    search_result_mod.SearchResult = SearchResult  # type: ignore[attr-defined]
    sys.modules.setdefault("calibre.gui2.store.search_result", search_result_mod)

    web_store_mod = types.ModuleType("calibre.gui2.store.web_store_dialog")

    class WebStoreDialog:  # pragma: no cover - GUI-only
        def __init__(self, *_, **__):
            pass

        def setWindowTitle(self, *_):
            pass

        def set_tags(self, *_):
            pass

        def exec(self):
            pass

    web_store_mod.WebStoreDialog = WebStoreDialog  # type: ignore[attr-defined]
    sys.modules.setdefault("calibre.gui2.store.web_store_dialog", web_store_mod)

    # --- qt.core --------------------------------------------------------------
    qt_core = types.ModuleType("qt.core")
    qt_core.QUrl = MagicMock(name="qt.core.QUrl")  # type: ignore[attr-defined]
    qt_pkg = types.ModuleType("qt")
    qt_pkg.core = qt_core  # type: ignore[attr-defined]
    sys.modules.setdefault("qt", qt_pkg)
    sys.modules.setdefault("qt.core", qt_core)

    # --- calibre_plugins.store_annas_archive alias ----------------------------
    # The plugin imports itself as `calibre_plugins.store_annas_archive.*`.
    # Map that namespace onto the repository root so absolute imports work.
    calibre_plugins = sys.modules.get("calibre_plugins") or types.ModuleType(
        "calibre_plugins"
    )
    calibre_plugins.__path__ = []  # type: ignore[attr-defined]
    sys.modules["calibre_plugins"] = calibre_plugins

    pkg_name = "calibre_plugins.store_annas_archive"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(REPO_ROOT)]  # type: ignore[attr-defined]
    sys.modules[pkg_name] = pkg
    setattr(calibre_plugins, "store_annas_archive", pkg)


_install_stub_modules()
