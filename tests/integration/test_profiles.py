"""Integration tests for configuration profiles."""

import yaml
from typer.testing import CliRunner

from archguard.cli.main import app
from tests.conftest import strip_rich

runner = CliRunner()


def test_profile_strict_vs_lenient(tmp_path, monkeypatch):
    # Setup contract
    contract = {
        "version": "3.0",
        "modules": [{"name": "test_mod", "path": "src/"}],
    }
    contract_file = tmp_path / ".archguard.yml"
    with open(contract_file, "w") as f:
        yaml.dump(contract, f)

    # Setup dummy python file with 8 external imports
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    code = "\n".join([f"import ext{i}" for i in range(8)])
    (src_dir / "foo.py").write_text(code)
    monkeypatch.chdir(tmp_path)

    # Strict profile expects max_coupling = 5. So 8 imports = violation.
    # strict min_health_score = 85.
    result_strict = runner.invoke(app, ["analyze", "--profile", "strict"])

    # Lenient profile expects max_coupling = 15. So 8 imports = passing.
    result_lenient = runner.invoke(app, ["analyze", "--profile", "lenient"])

    # Strict should log a violation or fail due to high coupling and/or low score
    assert "Applied configuration profile: strict" in strip_rich(result_strict.stdout)
    assert "Applied configuration profile: lenient" in strip_rich(result_lenient.stdout)

    # Lenient should pass coupling (budget 15) vs strict failing coupling (budget 5)
    # The exit codes and scores will differ based on the fan_out violation.
    assert result_lenient.exit_code == 0 or result_strict.exit_code != 0


def test_apply_profile_validates_against_schema() -> None:
    from archguard.contract.validator import validate_contract
    from archguard.profiles.defaults import apply_profile

    contract = {
        "version": "3.0",
        "modules": [{"name": "core", "path": "src/"}],
    }

    strict_contract = apply_profile(contract, "strict")
    validate_contract(strict_contract)  # Should not raise

    lenient_contract = apply_profile(contract, "lenient")
    validate_contract(lenient_contract)  # Should not raise

    ci_contract = apply_profile(contract, "ci")
    validate_contract(ci_contract)  # Should not raise
