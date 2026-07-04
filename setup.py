#!/usr/bin/env python3
"""
setup.py — Super-Z Skill Orchestrator as installable Python package.

Usage:
    pip install -e .                # editable install
    pip install .                   # regular install

After install, the `super-z` command is available system-wide.
"""
from setuptools import setup, find_packages
from pathlib import Path

ROOT = Path(__file__).parent
README = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").exists() else ""

setup(
    name="super-z",
    version="1.3.2",
    description="Self-regulating skill orchestrator: 72 skills, proactive watcher, adaptive router",
    long_description=README,
    long_description_content_type="text/markdown",
    author="Vitalij Kotok",
    author_email="vitalijkotok18@gmail.com",
    python_requires=">=3.10",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        # Pinned in requirements.txt — this is the short list for pip metadata
        "requests>=2.31",
        "pyyaml>=6.0",
        "jsonschema>=4.20",
        "pymorphy3>=2.0",
        "yt-dlp>=2024.10.07",
        "beautifulsoup4>=4.12",
        "lxml>=5.0",
        "markdown>=3.5",
        "python-docx>=1.1",
        "reportlab>=4.0",
        "openpyxl>=3.1",
        "python-pptx>=0.6.23",
        "matplotlib>=3.8",
        "Pillow>=10.0",
        "aiohttp>=3.9",
    ],
    entry_points={
        "console_scripts": [
            "super-z=super_z.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS",
        "Operating System :: Microsoft :: Windows",
    ],
)
