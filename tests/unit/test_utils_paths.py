from archguard.utils.paths import path_belongs_to_module


def test_path_belongs_to_module_boundary_safe():
    assert path_belongs_to_module("src/payments/api.py", ["src/payments/"]) is True
    assert path_belongs_to_module("src/payments_v2/api.py", ["src/payments/"]) is False


def test_path_belongs_to_module_adds_trailing_slash():
    # Should work even if module_path doesn't have trailing slash
    assert path_belongs_to_module("src/payments/api.py", ["src/payments"]) is True
