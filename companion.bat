@echo off
setlocal

cd /d "%~dp0"

:: Log everything for debugging
set LOGFILE=%cd%\companion.log
echo Geographica Companion starting at %date% %time% > "%LOGFILE%"

:: Check Python
py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=py -3
    goto :found_python
)
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=python
    goto :found_python
)
echo ERROR: Python 3 not found.
echo Download from https://www.python.org/downloads/
pause
exit /b 1

:found_python
for /f "tokens=*" %%V in ('%PYTHON% --version 2^>^&1') do echo %% %%V >> "%LOGFILE%"

:: Create venv if needed
if not exist ".venv" (
    echo Creating virtual environment...
    %PYTHON% -m venv .venv >> "%LOGFILE%" 2>&1
    if not exist ".venv\Scripts\activate.bat" (
        echo ERROR: Failed to create virtual environment. See companion.log for details.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

:: Install dependencies
echo Installing dependencies...
pip install -r requirements.txt >> "%LOGFILE%" 2>&1
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. See companion.log for details.
    pause
    exit /b 1
)
echo Dependencies installed OK >> "%LOGFILE%"

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

echo.
echo ==========================================
echo   Geographica Companion
echo   http://127.0.0.1:9000
echo   Press Ctrl+C to stop
echo ==========================================
echo.
echo Starting server... >> "%LOGFILE%"

:: Run server — stderr+stdout both go to log AND console
python companion.py 2>> "%LOGFILE%"

echo. >> "%LOGFILE%"
echo Server exited at %date% %time% >> "%LOGFILE%"
echo.
echo Server exited. If it crashed, check companion.log for details.
pause
