import json
import subprocess
import sys
from pathlib import Path

from super_z.config import load_config


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "super_z", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_skills_command_lists_expected_count() -> None:
    result = run_cli("--skills")
    assert result.returncode == 0, result.stderr
    stdout = result.stdout
    assert "72" in stdout or "72 skills" in stdout.lower()
    assert "blog-writer" in stdout


def test_run_command_emits_valid_brief() -> None:
    result = run_cli("--run", "blog-writer", "write a short test post")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["skill"] == "blog-writer"
    assert payload["status"] == "success"
    assert payload["claims"]


def test_load_config_from_env(monkeypatch: object) -> None:
    monkeypatch.setenv("SUPER_Z_BACKEND", "mock")
    config = load_config(config_file=None)
    assert config.backend == "mock"


def test_run_with_mock_backend_flag() -> None:
    result = run_cli("--run", "blog-writer", "write a short test post", "--backend", "mock")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["skill"] == "blog-writer"
