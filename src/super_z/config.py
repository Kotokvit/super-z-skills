from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SuperZConfig:
    skills_dir: Path
    backend: str = "mock"
    model: str = "mock"
    timeout: int = 120
    max_workers: int = 4


def get_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_package_root() -> Path:
    return Path(__file__).resolve().parent


def get_skills_dir(explicit: Optional[str | Path] = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()

    env_value = os.getenv("SUPER_Z_SKILLS_DIR")
    if env_value:
        return Path(env_value).expanduser().resolve()

    repo_dir = get_repo_root() / "skills"
    if repo_dir.exists():
        return repo_dir.resolve()

    package_dir = get_package_root() / "skills"
    if package_dir.exists():
        return package_dir.resolve()

    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        candidate = Path(xdg) / "super-z" / "skills"
        if candidate.exists():
            return candidate.resolve()

    return (Path.home() / ".local" / "share" / "super-z" / "skills").resolve()


def get_context_dir() -> Path:
    return get_repo_root() / ".context"


def get_brief_file() -> Path:
    return get_context_dir() / "context_brief.json"


def _candidate_config_files() -> list[Path]:
    paths = []
    env_path = os.getenv("SUPER_Z_CONFIG")
    if env_path:
        paths.append(Path(env_path).expanduser())
    paths.extend([
        Path.home() / ".config" / "super-z" / "config.toml",
        Path.home() / ".super-z.toml",
        get_repo_root() / ".super-z.toml",
    ])
    return [p for p in paths if p.exists()]


def load_config(config_file: Optional[str | Path] = None) -> SuperZConfig:
    config_path = Path(config_file).expanduser() if config_file else None
    if config_path is None:
        candidates = _candidate_config_files()
        if candidates:
            config_path = candidates[0]

    data: dict[str, object] = {}
    if config_path is not None:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)

    core = data.get("core", {}) if isinstance(data.get("core"), dict) else {}
    llm = data.get("llm", {}) if isinstance(data.get("llm"), dict) else {}

    skills_dir = os.getenv("SUPER_Z_SKILLS_DIR") or str(core.get("skills_dir") or get_skills_dir())
    backend = os.getenv("SUPER_Z_BACKEND") or str(llm.get("backend") or "mock")
    model = os.getenv("SUPER_Z_MODEL") or str(llm.get("model") or "mock")
    timeout = int(os.getenv("SUPER_Z_TIMEOUT") or str(llm.get("timeout") or 120))
    max_workers = int(os.getenv("SUPER_Z_MAX_WORKERS") or str(core.get("max_workers") or 4))

    return SuperZConfig(
        skills_dir=Path(skills_dir).expanduser().resolve(),
        backend=backend,
        model=model,
        timeout=timeout,
        max_workers=max_workers,
    )


def init_config(path: Optional[str | Path] = None) -> Path:
    target = Path(path).expanduser() if path else Path.home() / ".config" / "super-z" / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    content = '''[core]\nskills_dir = "~/.local/share/super-z/skills"\nmax_workers = 4\n\n[llm]\nbackend = "mock"\nmodel = "mock"\ntimeout = 120\n'''
    target.write_text(content, encoding="utf-8")
    return target
