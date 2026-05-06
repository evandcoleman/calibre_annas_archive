"""Tests for constants.py — search option metaclass + registered options."""
from __future__ import annotations

import pytest

from calibre_plugins.store_annas_archive import constants
from calibre_plugins.store_annas_archive.constants import (
    Access,
    CheckboxConfiguration,
    Content,
    DEFAULT_MIRRORS,
    FileType,
    Language,
    Order,
    RESULTS_PER_PAGE,
    SearchConfiguration,
    SearchOption,
    Source,
)


@pytest.mark.unit
class TestModuleLevelConstants:
    def test_default_mirrors_is_non_empty_list(self):
        assert isinstance(DEFAULT_MIRRORS, list)
        assert DEFAULT_MIRRORS, "expected at least one default mirror"

    def test_default_mirrors_are_https_urls(self):
        for mirror in DEFAULT_MIRRORS:
            assert mirror.startswith("https://"), mirror
            # mirrors must not include trailing slashes — `_search` appends paths
            assert not mirror.endswith("/"), mirror

    def test_results_per_page_positive(self):
        assert isinstance(RESULTS_PER_PAGE, int)
        assert RESULTS_PER_PAGE > 0


@pytest.mark.unit
class TestSearchOptionRegistry:
    def test_all_options_registered(self):
        names = [opt.__name__ for opt in SearchOption.options]
        assert names == ["Order", "Content", "Access", "Filetype", "Source", "Language"]

    def test_each_option_has_unique_config_key(self):
        keys = [opt.config_option for opt in SearchOption.options]
        assert len(keys) == len(set(keys)), f"duplicate config keys: {keys}"

    def test_each_option_has_unique_url_param(self):
        params = [opt.url_param for opt in SearchOption.options]
        assert len(params) == len(set(params))

    def test_values_match_options_second_column(self):
        for opt in SearchOption.options:
            expected = tuple(value for _, value in opt.options)
            assert opt.values == expected

    @pytest.mark.parametrize("opt", SearchOption.options)
    def test_option_labels_and_codes_are_strings(self, opt):
        for label, value in opt.options:
            assert isinstance(label, str) and label
            assert isinstance(value, str)


@pytest.mark.unit
class TestSpecificSearchOptions:
    def test_order_inherits_search_configuration(self):
        assert issubclass(Order, SearchConfiguration)
        assert not issubclass(Order, CheckboxConfiguration)
        assert Order.default == ""
        # Default ordering ("Most relevant") must use an empty value so the URL
        # param is omitted.
        assert ("Most relevant", "") in Order.options

    def test_checkbox_options_inherit_checkbox_configuration(self):
        for opt in (Content, Access, FileType, Source, Language):
            assert issubclass(opt, CheckboxConfiguration), opt.__name__
            assert opt.default == []

    def test_filetype_includes_supported_formats(self):
        codes = {value for _, value in FileType.options}
        # the plugin advertises EPUB/MOBI/PDF/AZW3/CBR/CBZ/FB2 in __init__.py
        for fmt in ("epub", "mobi", "pdf", "azw3", "cbr", "cbz", "fb2"):
            assert fmt in codes, f"{fmt} missing from FileType"

    def test_filetype_label_equals_code(self):
        # FileType is built via zip(*((codes,)*2)) so label == code.
        for label, code in FileType.options:
            assert label == code

    def test_language_unknown_uses_sentinel(self):
        # The unknown-language entry uses '_empty' as its code so that the
        # display label is plain "Unknown language" without a [_empty] tag.
        labels_by_code = {code: label for label, code in Language.options}
        assert "_empty" in labels_by_code
        assert labels_by_code["_empty"] == "Unknown language"

    def test_language_codes_unique(self):
        codes = [code for _, code in Language.options]
        assert len(codes) == len(set(codes))

    def test_source_keys_lowercase_alnum(self):
        for _, code in Source.options:
            assert code.replace("_", "").isalnum() and code.islower(), code


@pytest.mark.unit
class TestCheckboxConfigurationBehaviour:
    def test_to_save_returns_only_checked_codes(self):
        cfg = Content()  # __init__ sets up empty checkboxes dict

        class FakeCbx:
            def __init__(self, checked):
                self._checked = checked

            def isChecked(self):
                return self._checked

        cfg.checkboxes = {
            "book_fiction": FakeCbx(True),
            "magazine": FakeCbx(False),
            "audiobook": FakeCbx(True),
        }
        assert sorted(cfg.to_save()) == ["audiobook", "book_fiction"]

    def test_load_only_sets_known_codes(self):
        cfg = Content()

        class FakeCbx:
            def __init__(self):
                self.checked = False

            def setChecked(self, value):
                self.checked = value

        cfg.checkboxes = {"book_fiction": FakeCbx(), "magazine": FakeCbx()}
        cfg.load(["book_fiction", "does_not_exist"])
        assert cfg.checkboxes["book_fiction"].checked is True
        assert cfg.checkboxes["magazine"].checked is False
