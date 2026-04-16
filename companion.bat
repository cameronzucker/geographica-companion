@echo off
cd /d "%~dp0"

echo ==========================================
echo   Geographica Companion
echo ==========================================
echo.

:: Find Python
where py >nul 2>&1
if %errorlevel% equ 0 (
    set PY=py -3
) else (
    where python >nul 2>&1
    if %errorlevel% equ 0 (
        set PY=python
    ) else (
        echo ERROR: Python not found. Download from https://www.python.org/downloads/
        goto :fail
    )
)

:: Create venv if needed
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo ERROR: Failed to create virtual environment.
        goto :fail
    )
)

:: Use venv python directly instead of activate.bat
set PYTHON=.venv\Scripts\python.exe
set PIP=.venv\Scripts\pip.exe

:: Install dependencies
echo Installing dependencies...
"%PIP%" install -q -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies.
    goto :fail
)

:: Set GDAL environment
if exist "bin\windows-x64\gdalwarp.exe" (
    set PATH=%cd%\bin\windows-x64;%PATH%
    if exist "bin\windows-x64\share\proj" set PROJ_LIB=%cd%\bin\windows-x64\share\proj
    if exist "bin\windows-x64\share\gdal" set GDAL_DATA=%cd%\bin\windows-x64\share\gdal
    echo Using bundled GDAL
) else (
    where gdalwarp >nul 2>&1
    if %errorlevel% neq 0 (
        echo WARNING: No GDAL found. Pipelines requiring GDAL will fail.
    )
)

echo.
echo Starting server at http://127.0.0.1:9000
echo Press Ctrl+C to stop.
echo.
"%PYTHON%" companion.py
goto :done

:fail
echo.
echo Something went wrong. See the error above.

:done
echo.
pause
