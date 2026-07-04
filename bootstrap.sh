#!/usr/bin/env bash
# bootstrap.sh — backwards-compatible entry point.
# Delegates to install/linux.sh (the canonical installer since v1.3.0).
#
# Usage:
#   ./bootstrap.sh                # same as ./install/linux.sh
#   ./bootstrap.sh --quick
#   ./bootstrap.sh --uninstall
#
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/install/linux.sh" "$@"
