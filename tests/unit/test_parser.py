"""Unit tests for archguard.analysis.parser — all 5 import forms."""

from __future__ import annotations

import pytest

from archguard.analysis.parser import ImportParser

SAMPLE_1 = "import os"
SAMPLE_2 = "from os import path"
SAMPLE_3 = "from os import *"
SAMPLE_4 = "import os as operating_system"
SAMPLE_5 = "from . import utils"
SAMPLE_6 = "from ..models import User"
SAMPLE_7 = "import requests"
SAMPLE_8 = "from mymodule.sub import thing"


class TestImportParser:
    """Tests for ImportParser.parse_file()."""

    @pytest.fixture(autouse=True)
    def _setup_parser(self) -> None:
        self.parser = ImportParser()

    def test_import_stdlib(self) -> None:
        """``import os`` → is_stdlib=True, is_relative=False."""
        edges = self.parser.parse_file(SAMPLE_1)
        assert len(edges) == 1
        assert edges[0].imported_module == "os"
        assert edges[0].is_stdlib is True
        assert edges[0].is_relative is False

    def test_from_import_stdlib(self) -> None:
        """``from os import path`` → imported_module='os.path', is_stdlib=True."""
        edges = self.parser.parse_file(SAMPLE_2)
        assert len(edges) == 1
        assert edges[0].imported_module == "os.path"
        assert edges[0].is_stdlib is True

    def test_star_import(self) -> None:
        """``from os import *`` → imported_module='os', is_stdlib=True."""
        edges = self.parser.parse_file(SAMPLE_3)
        assert len(edges) == 1
        assert edges[0].imported_module == "os"
        assert edges[0].is_stdlib is True

    def test_aliased_import(self) -> None:
        """``import os as operating_system`` → imported_module='os', is_stdlib=True."""
        edges = self.parser.parse_file(SAMPLE_4)
        assert len(edges) == 1
        assert edges[0].imported_module == "os"
        assert edges[0].is_stdlib is True

    def test_relative_import(self) -> None:
        """``from . import utils`` → is_relative=True, imported_module='.utils'."""
        edges = self.parser.parse_file(SAMPLE_5)
        assert len(edges) == 1
        assert edges[0].is_relative is True
        assert edges[0].imported_module == ".utils"

    def test_relative_parent_import(self) -> None:
        """``from ..models import User`` → is_relative=True, imported_module='..models.User'."""
        edges = self.parser.parse_file(SAMPLE_6)
        assert len(edges) == 1
        assert edges[0].is_relative is True
        assert edges[0].imported_module == "..models.User"

    def test_third_party_import(self) -> None:
        """``import requests`` with empty module_paths → is_third_party=True."""
        edges = self.parser.parse_file(SAMPLE_7, module_paths={})
        assert len(edges) == 1
        assert edges[0].is_third_party is True

    def test_multiple_imports(self) -> None:
        """Multiple imports in one file → all returned."""
        source = "import os\nimport sys\nfrom pathlib import Path"
        edges = self.parser.parse_file(source)
        assert len(edges) == 3
