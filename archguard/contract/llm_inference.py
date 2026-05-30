import os
import ast
from pathlib import Path
import typing

CONTRACT_GENERATION_PROMPT = """
You are an expert software architect analyzing a Python codebase.
Given the following codebase structure and documentation, generate an ArchGuard contract that defines the module boundaries.

Codebase structure:
{directory_tree}

Module docstrings:
{module_docstrings}

README excerpt:
{readme_excerpt}

Generate a JSON object with this exact schema:
{{
  "schema_version": "3.0",
  "modules": [
    {{
      "name": "<module_name>",
      "paths": ["<relative_path>/"],
      "description": "<what this module does>",
      "coupling_budget": <integer 5-20>,
      "semantic_drift_threshold": <float 0.15-0.40>,
      "allowed_imports": ["<other_module_names>"],
      "disallowed_imports": []
    }}
  ],
  "fail_threshold": 0.75,
  "warn_threshold": 0.50
}}

Rules:
- Create 2-8 modules (not too granular, not too coarse)
- Module names should be lowercase, underscore-separated
- Coupling budget reflects expected fan-out (larger modules = higher budget)
- Only output JSON, no markdown or explanation
"""


def _build_directory_tree(repo_path: Path, max_depth: int = 3) -> str:
    """Build a text representation of the directory tree."""
    tree = []

    def walk(directory: Path, depth: int, prefix: str = "") -> None:
        if depth > max_depth:
            return

        try:
            paths = sorted(
                [
                    p
                    for p in directory.iterdir()
                    if not p.name.startswith(".") and p.name != "__pycache__"
                ]
            )
        except OSError:
            return

        for i, p in enumerate(paths):
            is_last = i == len(paths) - 1
            connector = "└── " if is_last else "├── "
            tree.append(f"{prefix}{connector}{p.name}")

            if p.is_dir():
                new_prefix = prefix + ("    " if is_last else "│   ")
                walk(p, depth + 1, new_prefix)

    tree.append(repo_path.name)
    walk(repo_path, 1)
    return "\n".join(tree)


def _extract_module_docstrings(repo_path: Path) -> str:
    """Extract top-level docstrings from __init__.py files."""
    docstrings = []
    for init_file in repo_path.rglob("**/__init__.py"):
        if any(part.startswith(".") for part in init_file.parts):
            continue
        try:
            content = init_file.read_text(errors="ignore")
            module = ast.parse(content)
            docstring = ast.get_docstring(module)
            if docstring:
                rel_path = init_file.relative_to(repo_path).parent
                docstrings.append(f"{rel_path}: {docstring.strip().splitlines()[0]}")
        except Exception:
            continue
    return "\n".join(docstrings)


def _read_readme_excerpt(repo_path: Path, max_chars: int = 2000) -> str:
    for name in ["README.md", "README.rst", "README.txt"]:
        readme = repo_path / name
        if readme.exists():
            try:
                return readme.read_text(errors="ignore")[:max_chars]
            except OSError:
                continue
    return ""


async def generate_contract_from_llm(repo_path: Path) -> dict[str, typing.Any]:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "ML dependencies are not installed. Run: pip install archguard[ml]"
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set.")

    tree = _build_directory_tree(repo_path, max_depth=3)
    docstrings = _extract_module_docstrings(repo_path)
    readme = _read_readme_excerpt(repo_path, max_chars=2000)

    prompt = CONTRACT_GENERATION_PROMPT.format(
        directory_tree=tree,
        module_docstrings=docstrings,
        readme_excerpt=readme,
    )

    async with anthropic.AsyncAnthropic(api_key=api_key) as client:
        response = await client.messages.create(
            model=os.getenv("ARCHGUARD_PRIMARY_MODEL", "claude-3-5-sonnet-20240620"),
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = response.content[0].text

    import json

    # Strip markdown code blocks if the LLM adds them despite instructions
    response_text = response_text.strip()
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    elif response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]

    contract = json.loads(response_text.strip())

    # We could validate here, but skipping rigorous validation so we can merge it
    return typing.cast(dict[str, typing.Any], contract)


def _merge_contracts(
    louvain: dict[str, typing.Any], llm: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """Prefer LLM module boundaries, use Louvain to refine coupling budgets."""
    import copy

    merged = copy.deepcopy(llm)

    # Use Louvain coupling data to set realistic budgets
    louvain_by_path = {}
    for m in louvain.get("modules", []):
        if m.get("paths"):
            louvain_by_path[m["paths"][0]] = m

    for module in merged.get("modules", []):
        for path in module.get("paths", []):
            if path in louvain_by_path:
                module["coupling_budget"] = louvain_by_path[path].get(
                    "coupling_budget", 10
                )

    return merged
