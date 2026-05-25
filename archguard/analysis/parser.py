"""tree-sitter-based Python import extraction."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

STDLIB_MODULES: frozenset[str] = frozenset(sys.stdlib_module_names)

_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".venv", "venv", ".git", "node_modules",
})


@dataclass(frozen=True)
class ImportEdge:
    """Represents a single import dependency edge."""

    source_file: str          # relative path from repo root
    imported_module: str      # normalized module name e.g. "os.path"
    is_stdlib: bool
    is_third_party: bool      # not stdlib and not in repo
    is_relative: bool


def _is_stdlib(module_root: str) -> bool:
    """Check if the root module name belongs to the standard library."""
    return module_root in STDLIB_MODULES


class ImportParser:
    """Parse Python source files for import statements using tree-sitter."""

    def __init__(self) -> None:
        # Lazy-init tree-sitter — imported inside __init__ body only
        from tree_sitter import Language, Parser
        import tree_sitter_python as tspython

        self._parser: Any = Parser(Language(tspython.language()))

    def parse_file(
        self,
        source: str,
        file_path: str = "<string>",
        module_paths: dict[str, list[str]] | None = None,
    ) -> list[ImportEdge]:
        """Parse Python source string, return all ImportEdge objects."""
        tree: Any = self._parser.parse(source.encode("utf-8"))
        first_party_roots = self._get_first_party_roots(module_paths or {})
        edges: list[ImportEdge] = []

        for node in self._iter_nodes(tree.root_node):
            if node.type == "import_statement":
                edges.extend(
                    self._process_import(node, file_path, first_party_roots)
                )
            elif node.type == "import_from_statement":
                edges.extend(
                    self._process_import_from(node, file_path, first_party_roots)
                )

        return edges

    def parse_repo(
        self,
        repo_root: Path,
        module_paths: dict[str, list[str]],
    ) -> list[ImportEdge]:
        """Walk repo_root for *.py files, parse each, return all edges.

        Skips: __pycache__, .venv, venv, .git, node_modules
        """
        edges: list[ImportEdge] = []
        for py_file in sorted(repo_root.rglob("*.py")):
            if any(skip in py_file.parts for skip in _SKIP_DIRS):
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
                rel_path = str(py_file.relative_to(repo_root)).replace("\\", "/")
                edges.extend(self.parse_file(source, rel_path, module_paths))
            except Exception:  # noqa: BLE001
                continue
        return edges

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_nodes(node: Any) -> Iterator[Any]:
        """Iterate over all nodes in the tree using iterative DFS."""
        stack: list[Any] = [node]
        while stack:
            current = stack.pop()
            yield current
            # Reversed so children are yielded left-to-right
            stack.extend(reversed(current.children))

    @staticmethod
    def _get_first_party_roots(
        module_paths: dict[str, list[str]],
    ) -> frozenset[str]:
        """Extract root directory names from module paths for first-party detection."""
        roots: set[str] = set()
        for paths in module_paths.values():
            for p in paths:
                parts = Path(p).parts
                if parts:
                    roots.add(parts[0])
        return frozenset(roots)

    def _process_import(
        self,
        node: Any,
        file_path: str,
        first_party_roots: frozenset[str],
    ) -> list[ImportEdge]:
        """Process ``import X`` / ``import X as Y`` statements."""
        edges: list[ImportEdge] = []
        for child in node.children_by_field_name("name"):
            module_text: str
            if child.type == "dotted_name":
                module_text = child.text.decode("utf-8")
            elif child.type == "aliased_import":
                name_node: Any = child.child_by_field_name("name")
                if name_node is None:
                    continue
                module_text = name_node.text.decode("utf-8")
            else:
                continue

            root = module_text.split(".")[0]
            stdlib = _is_stdlib(root)
            third_party = not stdlib and root not in first_party_roots

            edges.append(ImportEdge(
                source_file=file_path,
                imported_module=module_text,
                is_stdlib=stdlib,
                is_third_party=third_party,
                is_relative=False,
            ))
        return edges

    def _process_import_from(
        self,
        node: Any,
        file_path: str,
        first_party_roots: frozenset[str],
    ) -> list[ImportEdge]:
        """Process ``from X import Y`` / ``from . import Y`` statements."""
        edges: list[ImportEdge] = []

        module_node: Any = node.child_by_field_name("module_name")
        if module_node is None:
            return edges

        is_relative: bool = module_node.type == "relative_import"
        module_text: str = module_node.text.decode("utf-8")

        # Check for wildcard import  (from X import *)
        has_wildcard = any(c.type == "wildcard_import" for c in node.children)

        if has_wildcard:
            root = (
                module_text.lstrip(".").split(".")[0] if not is_relative else ""
            )
            stdlib = _is_stdlib(root) if root else False
            third_party = (
                not is_relative and not stdlib and root not in first_party_roots
            )

            edges.append(ImportEdge(
                source_file=file_path,
                imported_module=module_text,
                is_stdlib=stdlib,
                is_third_party=third_party,
                is_relative=is_relative,
            ))
        else:
            for name_node in node.children_by_field_name("name"):
                name: str
                if name_node.type == "aliased_import":
                    actual_name: Any = name_node.child_by_field_name("name")
                    if actual_name is None:
                        continue
                    name = actual_name.text.decode("utf-8")
                elif name_node.type == "dotted_name":
                    name = name_node.text.decode("utf-8")
                else:
                    continue

                # Build full imported module name
                # "." + "utils" → ".utils"  (relative, dots only)
                # "..models" + "." + "User" → "..models.User"
                if all(c == "." for c in module_text):
                    imported_module = module_text + name
                else:
                    imported_module = module_text + "." + name

                root = (
                    module_text.lstrip(".").split(".")[0]
                    if not is_relative
                    else ""
                )
                stdlib = _is_stdlib(root) if root else False
                third_party = (
                    not is_relative
                    and not stdlib
                    and root not in first_party_roots
                )

                edges.append(ImportEdge(
                    source_file=file_path,
                    imported_module=imported_module,
                    is_stdlib=stdlib,
                    is_third_party=third_party,
                    is_relative=is_relative,
                ))

        return edges
