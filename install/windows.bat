@echo off
REM windows.bat — launcher for windows.ps1 (handles ExecutionPolicy)
setlocal
set PS1=%~dp0windows.ps1

REM Try to run PowerShell with -ExecutionPolicy Bypass for this session
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -ExecutionPolicy Bypass -File "%PS1%" %*
    goto end
)

where powershell >nul 2>nul
if %errorlevel%==0 (
    powershell -ExecutionPolicy Bypass -File "%PS1%" %*
    goto end
)

echo ✗ PowerShell not found. Install PowerShell Core or Windows PowerShell.
exit /b 1

:end
endlocal
