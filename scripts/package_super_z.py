#!/usr/bin/env python3
"""
package_super_z.py — Stage clean Super-Z package and create tar.gz archive.

Copies only Super-Z relevant files (no Next.js, no node_modules, no .git, no
runtime artifacts) into a staging dir, then tars it up.
"""
import os
import shutil
import tarfile
from pathlib import Path

PROJECT_ROOT = Path("/home/z/my-project")
STAGING = PROJECT_ROOT / "staging" / "super-z"
ARCHIVE = PROJECT_ROOT / "download" / "super-z-v1.3.0.tar.gz"
ARCHIVE_ZIP = PROJECT_ROOT / "download" / "super-z-v1.3.0.zip"
STAGING_FILES = PROJECT_ROOT / "staging-files"  # README, .gitignore, LICENSE live here
INSTALL_FILES = PROJECT_ROOT / "install-files"  # linux.sh, windows.ps1, windows.bat

# ─── What to copy ───────────────────────────────────────────────────────
COPY_DIRS = [
    ("skills", "skills"),       # 70MB but mostly useful assets
    ("bin", "bin"),
]

COPY_FILES = [
    "bootstrap.sh",          # backwards-compat launcher (delegates to install/linux.sh)
    "requirements.txt",
    "setup.py",
]

# Specific scripts to copy (filter out unrelated add_chapter_*, build_*, etc.)
COPY_SCRIPTS = [
    "register_remaining_skills.py",
    "create_missing_wrappers.py",
    "create_wrappers_v2.py",
    "create_wrappers_v3.py",
    "fix_manifests_for_wrappers.py",
    "debug_classifier.py",
    "test_pattern3_routing.py",
    "test_final.py",
    "package_super_z.py",
    "ask_llm.py",
]

# ─── What to exclude from skills/ (large/binary/runtime) ────────────────
EXCLUDE_PATTERNS = [
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    "*.egg-info",
    ".DS_Store",
    "node_modules",
    ".venv",
    "*.log",
]


def should_exclude(path: Path) -> bool:
    """Check if a path matches any exclusion pattern."""
    name = path.name
    for pat in EXCLUDE_PATTERNS:
        if pat.startswith("*"):
            if name.endswith(pat[1:]):
                return True
        elif name == pat:
            return True
    return False


def copy_tree_filtered(src: Path, dst: Path):
    """Copy directory tree, skipping excluded patterns."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if should_exclude(item):
            continue
        if item.is_dir():
            copy_tree_filtered(item, dst / item.name)
        else:
            shutil.copy2(item, dst / item.name)


def main():
    # Clean staging
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)

    print(f"Staging directory: {STAGING}")

    # Copy dirs
    for src_name, dst_name in COPY_DIRS:
        src = PROJECT_ROOT / src_name
        dst = STAGING / dst_name
        if not src.exists():
            print(f"  SKIP {src_name} (not found)")
            continue
        print(f"  COPY {src_name}/ → {dst_name}/")
        copy_tree_filtered(src, dst)

    # Copy top-level files
    for fname in COPY_FILES:
        src = PROJECT_ROOT / fname
        if not src.exists():
            print(f"  SKIP {fname} (not found)")
            continue
        dst = STAGING / fname
        shutil.copy2(src, dst)
        if fname == "bootstrap.sh":
            os.chmod(dst, 0o755)
        print(f"  COPY {fname}")

    # Copy staging-files (README.md, .gitignore, LICENSE)
    if STAGING_FILES.exists():
        for item in STAGING_FILES.iterdir():
            if item.is_file():
                dst = STAGING / item.name
                shutil.copy2(item, dst)
                print(f"  COPY {item.name} (repo metadata)")

    # Copy install-files → install/ (platform installers)
    if INSTALL_FILES.exists():
        install_dst = STAGING / "install"
        install_dst.mkdir(exist_ok=True)
        for item in INSTALL_FILES.iterdir():
            if item.is_file():
                dst = install_dst / item.name
                shutil.copy2(item, dst)
                if item.name.endswith(".sh") or item.name.endswith(".bat") or item.name.endswith(".ps1"):
                    os.chmod(dst, 0o755)
                print(f"  COPY install/{item.name}")

    # Copy specific scripts
    scripts_dst = STAGING / "scripts"
    scripts_dst.mkdir(exist_ok=True)
    for sname in COPY_SCRIPTS:
        src = PROJECT_ROOT / "scripts" / sname
        if not src.exists():
            print(f"  SKIP scripts/{sname} (not found)")
            continue
        shutil.copy2(src, scripts_dst / sname)
        print(f"  COPY scripts/{sname}")

    # Verify super-z CLI is executable
    super_z = STAGING / "bin" / "super-z"
    if super_z.exists():
        os.chmod(super_z, 0o755)

    # Compute staging size
    total_size = sum(f.stat().st_size for f in STAGING.rglob("*") if f.is_file())
    print(f"\nStaging size: {total_size / 1024 / 1024:.1f} MB")

    # Create archive
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    if ARCHIVE.exists():
        ARCHIVE.unlink()

    print(f"Creating tar.gz archive: {ARCHIVE}")
    with tarfile.open(ARCHIVE, "w:gz") as tar:
        tar.add(STAGING, arcname="super-z")

    archive_size = ARCHIVE.stat().st_size
    print(f"Archive size: {archive_size / 1024 / 1024:.1f} MB")

    # Also create .zip via subprocess (Windows-friendly)
    import subprocess
    if ARCHIVE_ZIP.exists():
        ARCHIVE_ZIP.unlink()
    print(f"Creating zip archive: {ARCHIVE_ZIP}")
    r = subprocess.run(
        ["zip", "-qr", str(ARCHIVE_ZIP), "super-z"],
        cwd=STAGING.parent,
    )
    if r.returncode == 0:
        zip_size = ARCHIVE_ZIP.stat().st_size
        print(f"Zip size: {zip_size / 1024 / 1024:.1f} MB")
    else:
        print(f"! zip creation failed (returncode={r.returncode})")

    print(f"\n✓ Archives ready:")
    print(f"  {ARCHIVE}")
    print(f"  {ARCHIVE_ZIP}")


if __name__ == "__main__":
    main()
