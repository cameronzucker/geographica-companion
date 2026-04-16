@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

:: Check Python
py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=py -3
) else (
    python --version >nul 2>&1
    if %errorlevel% equ 0 (
        set PYTHON=python
    ) else (
        echo ERROR: Python 3 not found.
        echo Download from https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

:: Check version
for /f "tokens=2 delims= " %%V in ('%PYTHON% --version 2^>^&1') do set PY_VER=%%V
for /f "tokens=1,2 delims=." %%A in ("%PY_VER%") do (
    if %%A lss 3 (
        echo ERROR: Python %PY_VER% found, but 3.10+ required.
        pause
        exit /b 1
    )
    if %%A equ 3 if %%B lss 10 (
        echo ERROR: Python %PY_VER% found, but 3.10+ required.
        pause
        exit /b 1
    )
)

:: Create venv if needed
if not exist ".venv" (
    echo Creating virtual environment...
    %PYTHON% -m venv .venv
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment is missing activation script.
    echo Try deleting the .venv folder and running again.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

:: Install dependencies
echo Installing dependencies...
pip install -q -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

:: Set GDAL environment
if exist "bin\windows-x64\gdalwarp.exe" (
    set PATH=%cd%\bin\windows-x64;%PATH%
    if exist "bin\windows-x64\share\proj" set PROJ_LIB=%cd%\bin\windows-x64\share\proj
    if exist "bin\windows-x64\share\gdal" set GDAL_DATA=%cd%\bin\windows-x64\share\gdal
    echo Using bundled GDAL from bin\windows-x64\
) else (
    where gdalwarp >nul 2>&1
    if %errorlevel% equ 0 (
        echo Using system GDAL
    ) else (
        echo WARNING: No GDAL found. Pipelines that require GDAL will fail.
    )
)

echo Starting Geographica Companion...
echo.
echo If the browser doesn't open automatically, visit:
echo   http://127.0.0.1:9000
echo.
echo Press Ctrl+C to stop the server.
echo.
python companion.py
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Companion server exited with an error.
)
pause
