#!/usr/bin/env bash
# linux.sh — Super-Z Skill Orchestrator one-command installer for Linux/macOS.
#
# This is the same as bootstrap.sh, but located in install/ for cleaner structure.
# bootstrap.sh in the project root remains as a backwards-compatible symlink.
#
# Usage:
#   ./install/linux.sh                # install + register + verify
#   ./install/linux.sh --quick        # skip optional deps (Node, Playwright)
#   ./install/linux.sh --uninstall    # remove super-z CLI symlink
#
set -e

# ─── Colors ─────────────────────────────────────────────────────────────
RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
BLUE=$'\033[34m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

log()  { echo "${BLUE}›${RESET} $*"; }
ok()   { echo "${GREEN}✓${RESET} $*"; }
warn() { echo "${YELLOW}!${RESET} $*"; }
err()  { echo "${RED}✗${RESET} $*" >&2; }

# ─── Detect project root ────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

QUICK_MODE=false
UNINSTALL=false
for arg in "$@"; do
    case "$arg" in
        --quick)      QUICK_MODE=true ;;
        --uninstall)  UNINSTALL=true ;;
        --help|-h)    sed -n '2,15p' "$0"; exit 0 ;;
    esac
done

if $UNINSTALL; then
    log "Uninstalling super-z CLI..."
    rm -f /usr/local/bin/super-z 2>/dev/null && ok "Removed /usr/local/bin/super-z" || warn "Need sudo to remove /usr/local/bin/super-z"
    rm -f "$HOME/.local/bin/super-z" 2>/dev/null && ok "Removed ~/.local/bin/super-z"
    exit 0
fi

# ─── Banner ─────────────────────────────────────────────────────────────
echo ""
echo "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo "${BOLD}║       Super-Z Skill Orchestrator — Linux Installer           ║${RESET}"
echo "${BOLD}║       72 skills · proactive watcher · adaptive router        ║${RESET}"
echo "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""

# ─── Step 1: Check Python ───────────────────────────────────────────────
log "Step 1/6: Checking Python..."
if ! command -v python3 >/dev/null 2>&1; then
    err "Python 3 not found. Install Python 3.10+ first: https://python.org"
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)')
if [ "$PY_OK" != "1" ]; then
    err "Python 3.10+ required, found $PY_VERSION"
    exit 1
fi
ok "Python $PY_VERSION"

# ─── Step 2: Check z-ai CLI (optional) ─────────────────────────────────
log "Step 2/6: Checking z-ai CLI (optional)..."
if ! command -v z-ai >/dev/null 2>&1; then
    warn "z-ai CLI not found; the new CLI can still run in mock mode for docs-only skills"
else
    ok "z-ai CLI present"
fi

# ─── Step 3: Create/use venv ────────────────────────────────────────────
log "Step 3/6: Setting up Python environment..."
VENV_DIR="$PROJECT_ROOT/.venv"
if [ -d "$VENV_DIR" ]; then
    ok "Found existing venv at .venv/"
else
    if ! $QUICK_MODE; then
        python3 -m venv "$VENV_DIR" && ok "Created venv at .venv/"
    else
        warn "Skipping venv creation (--quick mode), using system Python"
        VENV_DIR=""
    fi
fi

if [ -n "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/python3" ]; then
    PYTHON="$VENV_DIR/bin/python3"
    PIP="$VENV_DIR/bin/pip"
else
    PYTHON="$(command -v python3)"
    PIP="$(command -v pip3 || command -v pip)"
fi
ok "Using Python: $PYTHON"

# ─── Step 4: Install Python dependencies ────────────────────────────────
log "Step 4/6: Installing Python dependencies..."
if [ -f "requirements.txt" ]; then
    $PIP install --upgrade pip --quiet 2>/dev/null || warn "pip self-upgrade skipped"
    $PIP install -r requirements.txt --quiet 2>&1 | tail -5
else
    warn "requirements.txt not found, skipping"
fi

$PIP install -e "$PROJECT_ROOT" --quiet 2>&1 | tail -5
ok "Python package installed"

# ─── Step 5: Register all skills (best effort) ────────────────────────
log "Step 5/6: Registering skills..."
REGISTRY_SCRIPT="$PROJECT_ROOT/scripts/register_remaining_skills.py"
if [ -f "$REGISTRY_SCRIPT" ]; then
    $PYTHON "$REGISTRY_SCRIPT" 2>&1 | tail -8 || warn "Registration script reported issues"
fi

# Verify skill count
SKILL_COUNT=$(find "$PROJECT_ROOT/skills" -maxdepth 1 -mindepth 1 -type d | wc -l)
EXEC_COUNT=$(find "$PROJECT_ROOT/skills" -name "run.py" -path "*/scripts/*" | wc -l)
ok "Found $SKILL_COUNT skills ($EXEC_COUNT with executable wrappers)"

# ─── Step 6: Install super-z CLI ────────────────────────────────────────
log "Step 6/6: Installing super-z CLI..."

mkdir -p "$PROJECT_ROOT/bin"
chmod +x "$PROJECT_ROOT/bin/super-z" "$PROJECT_ROOT/bin/super-z.py" 2>/dev/null || true

LOCAL_BIN="$HOME/.local/bin"
USR_BIN="/usr/local/bin"

if [ -d "$LOCAL_BIN" ] && [ -w "$LOCAL_BIN" ]; then
    # Symlink to the Python entry point (cross-platform)
    ln -sf "$PROJECT_ROOT/bin/super-z.py" "$LOCAL_BIN/super-z"
    ok "Installed super-z → $LOCAL_BIN/super-z"
    BIN_PATH="$LOCAL_BIN"
elif sudo -n true 2>/dev/null; then
    sudo ln -sf "$PROJECT_ROOT/bin/super-z.py" "$USR_BIN/super-z"
    ok "Installed super-z → $USR_BIN/super-z"
    BIN_PATH="$USR_BIN"
else
    warn "Could not install super-z to system PATH automatically."
    warn "Add to PATH manually:"
    warn "  export PATH=\"$PROJECT_ROOT/bin:\$PATH\""
    BIN_PATH="$PROJECT_ROOT/bin"
fi

# ─── Optional: Deploy skills to target directory ──────────────────────
# By default, skills stay in $PROJECT_ROOT/skills/ and super-z uses them there.
# Set SUPER_Z_TARGET_DIR env var to copy them elsewhere (e.g. ~/.local/share/super-z/skills).
TARGET_DIR="${SUPER_Z_TARGET_DIR:-}"
if [ -n "$TARGET_DIR" ]; then
    log "Deploying skills to $TARGET_DIR..."
    mkdir -p "$TARGET_DIR"
    cp -r "$PROJECT_ROOT/skills/"* "$TARGET_DIR/" 2>/dev/null || warn "Some skills failed to copy"
    SKILL_DEPLOYED=$(find "$TARGET_DIR" -maxdepth 1 -mindepth 1 -type d | wc -l)
    ok "Deployed $SKILL_DEPLOYED skills to $TARGET_DIR"
fi

# ─── Verify watcher ─────────────────────────────────────────────────────
log "Verifying watcher..."
if $PYTHON "$PROJECT_ROOT/skills/_orchestrator/scripts/watcher.py" --verify 2>&1 | tail -5; then
    ok "Watcher verification passed"
else
    warn "Watcher verification had issues (non-fatal)"
fi

# ─── Done ───────────────────────────────────────────────────────────────
echo ""
echo "${BOLD}${GREEN}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo "${BOLD}${GREEN}║                    ✨  INSTALL COMPLETE  ✨                    ║${RESET}"
echo "${BOLD}${GREEN}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""
echo "  ${BOLD}Skills registered:${RESET} $SKILL_COUNT  ($EXEC_COUNT executable)"
echo "  ${BOLD}Watcher signals:${RESET}   31 patterns, 30 mappings"
echo "  ${BOLD}CLI command:${RESET}       super-z"
echo ""
echo "  ${BOLD}Quick start:${RESET}"
if [ "$BIN_PATH" = "$PROJECT_ROOT/bin" ]; then
    echo "    export PATH=\"$PROJECT_ROOT/bin:\$PATH\""
    echo "    $PROJECT_ROOT/bin/super-z \"напиши пост про ИИ\""
else
    echo "    super-z \"напиши пост про ИИ\""
fi
echo "    poler-edit                              # открыть веб-интерфейс PolerEdit"
echo "    super-z --watch                          # interactive mode"
echo "    super-z --brief                          # show context brief"
echo ""
