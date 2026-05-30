from pathlib import Path


def patch_coupling() -> None:
    p = Path("analysis/coupling.py")
    text = p.read_text(encoding="utf-8")

    helper = '''from pathlib import Path

def _path_belongs_to_module(file_path: str, module_path: str) -> bool:
    """
    Check if file_path is inside module_path, with proper boundary checking.
    Prevents false matches like api_utils matching module 'api'.
    """
    # Normalize both to forward-slash, strip leading slash
    file_parts = Path(file_path.strip("/")).parts
    module_parts = Path(module_path.strip("/")).parts
    if len(file_parts) < len(module_parts):
        return False
    # Compare part-by-part, not as raw strings
    return file_parts[:len(module_parts)] == module_parts

'''

    text = text.replace(
        'def _normalize_path(path: str) -> str:\n    """Normalize path separators and strip trailing slash."""\n    return path.replace("\\\\", "/").rstrip("/")\n\n',
        'def _normalize_path(path: str) -> str:\n    """Normalize path separators and strip trailing slash."""\n    return path.replace("\\\\", "/").rstrip("/")\n\n'
        + helper,
    )

    text = text.replace(
        'def _file_belongs_to_module(file_path: str, paths: list[str]) -> bool:\n    """Check if *file_path* starts with any of the module\'s path prefixes."""\n    normalized = _normalize_path(file_path)\n    return any(normalized.startswith(_normalize_path(p)) for p in paths)',
        'def _file_belongs_to_module(file_path: str, paths: list[str]) -> bool:\n    """Check if *file_path* starts with any of the module\'s path prefixes."""\n    return any(_path_belongs_to_module(file_path, p) for p in paths)',
    )

    old_assign = """    for mod_name, paths in module_paths.items():
        for p in paths:
            prefix = _normalize_path(p)
            if normalized.startswith(prefix) and len(prefix) > best_len:
                best_match = mod_name
                best_len = len(prefix)"""
    new_assign = """    for mod_name, paths in module_paths.items():
        for p in paths:
            if _path_belongs_to_module(file_path, p):
                prefix_len = len(_normalize_path(p))
                if prefix_len > best_len:
                    best_match = mod_name
                    best_len = prefix_len"""
    text = text.replace(old_assign, new_assign)

    old_fanin = """        targets_us = any(
            import_as_path.startswith(_normalize_path(tp))
            or _normalize_path(tp).startswith(import_as_path)
            for tp in target_paths
        )"""
    new_fanin = """        targets_us = any(
            _path_belongs_to_module(import_as_path, tp)
            or _path_belongs_to_module(tp, import_as_path)
            for tp in target_paths
        )"""
    text = text.replace(old_fanin, new_fanin)

    p.write_text(text, encoding="utf-8")


def patch_layers() -> None:
    p = Path("analysis/layers.py")
    text = p.read_text(encoding="utf-8")

    helper = '''def _path_belongs_to_module(file_path: str, module_path: str) -> bool:
    """
    Check if file_path is inside module_path, with proper boundary checking.
    Prevents false matches like api_utils matching module 'api'.
    """
    # Normalize both to forward-slash, strip leading slash
    file_parts = Path(file_path.strip("/")).parts
    module_parts = Path(module_path.strip("/")).parts
    if len(file_parts) < len(module_parts):
        return False
    # Compare part-by-part, not as raw strings
    return file_parts[:len(module_parts)] == module_parts

'''
    text = text.replace(
        'def _normalize_path(path: str) -> str:\n    """Normalize path separators."""\n    return path.replace("\\\\", "/").rstrip("/")\n\n',
        'def _normalize_path(path: str) -> str:\n    """Normalize path separators."""\n    return path.replace("\\\\", "/").rstrip("/")\n\n'
        + helper,
    )

    old_l1 = """                # Determine which module this file belongs to
                file_module: str | None = None
                for mod_name, paths in module_paths.items():
                    norm_rel = _normalize_path(rel)
                    for p in paths:
                        if norm_rel.startswith(_normalize_path(p)):
                            file_module = mod_name
                            break
                    if file_module:
                        break"""
    new_l1 = """                # Determine which module this file belongs to
                file_module: str | None = None
                for mod_name, paths in module_paths.items():
                    for p in paths:
                        if _path_belongs_to_module(rel, p):
                            file_module = mod_name
                            break
                    if file_module:
                        break"""
    text = text.replace(old_l1, new_l1)

    old_self = """                            is_self = any(
                                root.startswith(_normalize_path(p).split("/")[0])
                                for p in module_paths.get(file_module, [])
                            )"""
    new_self = """                            is_self = any(
                                _path_belongs_to_module(root, _normalize_path(p).split("/")[0])
                                for p in module_paths.get(file_module, [])
                            )"""
    text = text.replace(old_self, new_self)

    old_aff = """                matched = False
                for p in paths:
                    if _normalize_path(rel).startswith(_normalize_path(p)):
                        matched = True
                        break"""
    new_aff = """                matched = False
                for p in paths:
                    if _path_belongs_to_module(rel, p):
                        matched = True
                        break"""
    text = text.replace(old_aff, new_aff)

    p.write_text(text, encoding="utf-8")


patch_coupling()
patch_layers()
print("PATCH_OK")
