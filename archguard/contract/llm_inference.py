import ast
import logging
import typing
from pathlib import Path

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
  "version": "3.0",
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
            connector = "`-- " if is_last else "|-- "
            tree.append(f"{prefix}{connector}{p.name}")

            if p.is_dir():
                new_prefix = prefix + ("    " if is_last else "|   ")
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
        except (SyntaxError, ValueError, OSError) as exc:
            logger = logging.getLogger(__name__)
            logger.exception("Contract LLM inference failed for %s: %s", init_file, exc)
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
    import asyncio

    from archguard.llm.cloud import FALLBACK_MODEL, PRIMARY_MODEL, CloudLLMExplainer
    from archguard.llm.gemini import (
        NON_RETRYABLE_ERRORS,
        RETRYABLE_ERRORS,
        TRY_NEXT_MODEL_ERRORS,
        llm_disabled,
        resolve_api_key,
    )

    off = llm_disabled()
    if off:
        # Before the directory walk and the prompt build, both of which are
        # pointless work if nothing may be sent.
        raise ValueError(off)

    api_key = resolve_api_key()

    tree = _build_directory_tree(repo_path, max_depth=3)
    docstrings = _extract_module_docstrings(repo_path)
    readme = _read_readme_excerpt(repo_path, max_chars=2000)

    prompt = CONTRACT_GENERATION_PROMPT.format(
        directory_tree=tree,
        module_docstrings=docstrings,
        readme_excerpt=readme,
    )

    explainer = CloudLLMExplainer(api_key=api_key)
    response_text: str = ""
    last_error: Exception | None = None

    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            response_text, _stop_reason = await asyncio.to_thread(
                explainer._call_api, prompt, model, system=""
            )
            break
        except (*TRY_NEXT_MODEL_ERRORS, *RETRYABLE_ERRORS) as exc:
            # A retired model id, or a transient failure: the other tier is
            # worth a try. This clause must come first -- TRY_NEXT_MODEL_ERRORS
            # is a subset of NON_RETRYABLE_ERRORS below, and Python takes the
            # first matching handler.
            last_error = exc
            continue
        except NON_RETRYABLE_ERRORS:
            # Bare `except Exception` here meant a bad API key burned an attempt
            # on both tiers and then reported the second failure, hiding the
            # real cause -- the same bug cloud.py fixed, in a copy of the loop
            # that never got the fix. Credentials and malformed requests fail
            # identically on the cheaper tier.
            raise
    else:
        raise RuntimeError(
            f"Contract generation failed on both {PRIMARY_MODEL} and {FALLBACK_MODEL}. Last error: {last_error}"
        )

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

    from archguard.contract.validator import validate_contract

    try:
        validate_contract(contract)
    except Exception as e:
        raise ValueError(f"LLM generated an invalid contract: {e}")

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
