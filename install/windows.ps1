# bootstrap.ps1 — Super-Z Skill Orchestrator one-command installer for Windows.
#
# Usage (PowerShell):
#   .\install\windows.ps1                # install + register + verify
#   .\install\windows.ps1 -Quick         # skip venv creation, use system Python
#   .\install\windows.ps1 -Uninstall     # remove super-z CLI from PATH
#
# Or via batch launcher:
#   install\windows.bat
#
# After install:
#   super-z "your request"               # run the orchestrator
#   super-z --watch                      # interactive watcher mode
#   super-z --brief                      # show current context_brief.json
#
param(
    [switch]$Quick,
    [switch]$Uninstall,
    [switch]$Help
)

# ─── Help ──────────────────────────────────────────────────────────────
if ($Help) {
    Get-Help $MyInvocation.MyCommand.Path -Detailed
    exit 0
}

# ─── Detect project root ───────────────────────────────────────────────
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

# ─── Uninstall ─────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Host "› Uninstalling super-z CLI..." -ForegroundColor Blue
    $UserProfile = $env:USERPROFILE
    $LocalBin = Join-Path $UserProfile ".local\bin"
    $SuperZCmd = Join-Path $LocalBin "super-z.cmd"
    if (Test-Path $SuperZCmd) {
        Remove-Item $SuperZCmd -Force
        Write-Host "✓ Removed $SuperZCmd" -ForegroundColor Green
    }
    # Remove from PATH
    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if ($userPath -match [regex]::Escape($LocalBin)) {
        $newPath = $userPath -replace [regex]::Escape($LocalBin + ";"), "" -replace [regex]::Escape($LocalBin), ""
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
        Write-Host "✓ Removed $LocalBin from PATH" -ForegroundColor Green
    }
    exit 0
}

# ─── Banner ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════════╗" -ForegroundColor White
Write-Host "║       Super-Z Skill Orchestrator — Windows Installer         ║" -ForegroundColor White
Write-Host "║       72 skills · proactive watcher · adaptive router        ║" -ForegroundColor White
Write-Host "╚══════════════════════════════════════════════════════════════╝" -ForegroundColor White
Write-Host ""

# ─── Step 1: Check Python ──────────────────────────────────────────────
Write-Host "› Step 1/6: Checking Python..." -ForegroundColor Blue
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Host "✗ Python 3 not found. Install Python 3.10+ from https://python.org" -ForegroundColor Red
    Write-Host "  Make sure to check 'Add Python to PATH' during installation." -ForegroundColor Yellow
    exit 1
}
$PythonCmd = $python.Source
$pyVersion = & $PythonCmd --version 2>&1
Write-Host "✓ Python found: $pyVersion ($PythonCmd)" -ForegroundColor Green

# Verify version >= 3.10
$versionInfo = & $PythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
$parts = $versionInfo.ToString().Split('.')
try {
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
        Write-Host "✗ Python 3.10+ required, found $versionInfo" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "! Could not verify Python version, continuing..." -ForegroundColor Yellow
}

# ─── Step 2: Check z-ai CLI ────────────────────────────────────────────
Write-Host "› Step 2/6: Checking z-ai CLI..." -ForegroundColor Blue
$zAi = Get-Command z-ai -ErrorAction SilentlyContinue
if (-not $zAi) {
    Write-Host "✗ z-ai CLI not found." -ForegroundColor Red
    Write-Host "  Install it first: npm install -g z-ai-web-dev-sdk" -ForegroundColor Yellow
    Write-Host "  Or check: https://github.com/z-ai-zai/z-ai-web-dev-sdk" -ForegroundColor Yellow
    exit 1
}
Write-Host "✓ z-ai CLI present ($($zAi.Source))" -ForegroundColor Green

# ─── Step 3: Create venv ───────────────────────────────────────────────
Write-Host "› Step 3/6: Setting up Python environment..." -ForegroundColor Blue
$VenvDir = Join-Path $ProjectRoot ".venv"
if (-not $Quick) {
    if (Test-Path $VenvDir) {
        Write-Host "✓ Found existing venv at .venv\" -ForegroundColor Green
    } else {
        & $PythonCmd -m venv $VenvDir
        if (Test-Path $VenvDir) {
            Write-Host "✓ Created venv at .venv\" -ForegroundColor Green
        } else {
            Write-Host "✗ Failed to create venv" -ForegroundColor Red
            exit 1
        }
    }
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    $VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
    if (Test-Path $VenvPython) {
        $PythonCmd = $VenvPython
        $PipCmd = $VenvPip
    } else {
        Write-Host "! venv created but python.exe not found, using system Python" -ForegroundColor Yellow
        $PipCmd = "pip"
    }
} else {
    Write-Host "! Skipping venv creation (-Quick mode), using system Python" -ForegroundColor Yellow
    $PipCmd = "pip"
}
Write-Host "✓ Using Python: $PythonCmd" -ForegroundColor Green

# ─── Step 4: Install Python dependencies ───────────────────────────────
Write-Host "› Step 4/6: Installing Python dependencies..." -ForegroundColor Blue
$ReqFile = Join-Path $ProjectRoot "requirements.txt"
if (Test-Path $ReqFile) {
    & $PipCmd install --upgrade pip --quiet 2>&1 | Out-Null
    & $PipCmd install -r $ReqFile --quiet 2>&1 | Select-Object -Last 5
    Write-Host "✓ Python dependencies installed" -ForegroundColor Green
} else {
    Write-Host "! requirements.txt not found, skipping" -ForegroundColor Yellow
}

# ─── Step 5: Register all skills ───────────────────────────────────────
Write-Host "› Step 5/6: Registering skills..." -ForegroundColor Blue
$RegScript = Join-Path $ProjectRoot "scripts\register_remaining_skills.py"
if (-not (Test-Path $RegScript)) {
    $RegScript = Join-Path $ProjectRoot "scripts\register_all_skills.py"
}
if (Test-Path $RegScript) {
    & $PythonCmd $RegScript 2>&1 | Select-Object -Last 8
    Write-Host "✓ Skills registered" -ForegroundColor Green
} else {
    Write-Host "✗ No registration script found in scripts\" -ForegroundColor Red
    exit 1
}

# Verify skill count
$SkillsDir = Join-Path $ProjectRoot "skills"
$skillCount = (Get-ChildItem -Path $SkillsDir -Directory | Where-Object { $_.Name -notmatch "^_" -and $_.Name -ne "ARCHITECTURE.md" }).Count
$execCount = (Get-ChildItem -Path $SkillsDir -Directory | Where-Object { $_.Name -notmatch "^_" -and $_.Name -ne "ARCHITECTURE.md" -and (Test-Path (Join-Path $_.FullName "scripts\run.py")) }).Count
Write-Host "✓ Found $skillCount skills ($execCount with executable wrappers)" -ForegroundColor Green

# ─── Step 6: Install super-z CLI ───────────────────────────────────────
Write-Host "› Step 6/6: Installing super-z CLI..." -ForegroundColor Blue

# Create super-z.cmd wrapper in user's .local\bin
# This wrapper calls bin/super-z.py — the cross-platform Python entry point
# that has feature parity with bin/super-z (bash) on Linux.
$UserProfile = $env:USERPROFILE
$LocalBin = Join-Path $UserProfile ".local\bin"
if (-not (Test-Path $LocalBin)) {
    New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null
}

$SuperZCmd = Join-Path $LocalBin "super-z.cmd"

# .cmd wrapper that calls super-z.py with all args preserved
$cmdContent = @"
@echo off
REM super-z — Windows wrapper for Super-Z Skill Orchestrator
REM Calls bin/super-z.py (cross-platform Python entry point)
setlocal
set PROJECT_ROOT=$ProjectRoot
set PYTHON=$PythonCmd
if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" set PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe

"%PYTHON%" "%PROJECT_ROOT%\bin\super-z.py" %*
exit /b %ERRORLEVEL%
"@

$cmdContent | Out-File -FilePath $SuperZCmd -Encoding ASCII -Force
Write-Host "✓ Installed super-z → $SuperZCmd" -ForegroundColor Green

# Add to user PATH if not already there
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notmatch [regex]::Escape($LocalBin)) {
    $newPath = if ($userPath) { "$LocalBin;$userPath" } else { $LocalBin }
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    Write-Host "✓ Added $LocalBin to user PATH (restart terminal to take effect)" -ForegroundColor Green
    $env:PATH = "$LocalBin;$env:PATH"
} else {
    Write-Host "✓ $LocalBin already in PATH" -ForegroundColor Green
}

# ─── Verify watcher ────────────────────────────────────────────────────
Write-Host "› Verifying watcher..." -ForegroundColor Blue
& $PythonCmd (Join-Path $ProjectRoot "skills\_orchestrator\scripts\watcher.py") --verify 2>&1 | Select-Object -Last 5
if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Watcher verification passed" -ForegroundColor Green
} else {
    Write-Host "! Watcher verification had issues (non-fatal)" -ForegroundColor Yellow
}

# ─── Done ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║                    ✨  INSTALL COMPLETE  ✨                    ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Skills registered: $skillCount  ($execCount executable)"
Write-Host "  Watcher signals:   31 patterns, 30 mappings"
Write-Host "  CLI command:       super-z"
Write-Host ""
Write-Host "  Quick start:" -ForegroundColor White
Write-Host "    super-z `"напиши пост про ИИ`""
Write-Host "    super-z --watch                          # interactive mode"
Write-Host "    super-z --brief                          # show context brief"
Write-Host ""
Write-Host "  NOTE: Restart your PowerShell/terminal for PATH changes to take effect." -ForegroundColor Yellow
Write-Host ""
