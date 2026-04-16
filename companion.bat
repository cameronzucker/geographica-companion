@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==========================================
echo   Geographica Companion
echo ==========================================
echo.

:: Find Python
where py >nul 2>&1
if !errorlevel! equ 0 (
    set PY=py -3
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set PY=python
    ) else (
        echo ERROR: Python not found. Download from https://www.python.org/downloads/
        goto :fail
    )
)

:: Create venv if needed
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    !PY! -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo ERROR: Failed to create virtual environment.
        goto :fail
    )
)

:: Use venv python directly instead of activate.bat
set "PYTHON=.venv\Scripts\python.exe"
set "PIP=.venv\Scripts\pip.exe"

:: Install dependencies (show output so errors are visible)
echo Installing dependencies...
"!PIP!" install -r requirements.txt
if !errorlevel! neq 0 (
    echo ERROR: Failed to install dependencies.
    goto :fail
)

:: Verify critical dependency
"!PYTHON!" -c "import rasterio; print('rasterio ' + rasterio.__version__ + ' OK')" 2>nul
if !errorlevel! neq 0 (
    echo.
    echo ERROR: rasterio failed to install. Trying direct install...
    "!PIP!" install rasterio numpy shapely pyshp
    if !errorlevel! neq 0 (
        echo ERROR: Could not install rasterio. Check Python version compatibility.
        goto :fail
    )
)

echo.
echo Starting server at http://127.0.0.1:9000
echo Press Ctrl+C to stop.
echo.
"!PYTHON!" companion.py
goto :done

:fail
echo.
echo Something went wrong. See the error above.

:done
echo.
pause
