@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if not errorlevel 1 (
    python --version
    if not errorlevel 1 (
        set "PYTHON=python"
        goto python_ready
    )
)

where py >nul 2>nul
if errorlevel 1 goto no_python
set "PYTHON=py -3"
%PYTHON% --version
if errorlevel 1 goto no_python

:python_ready
echo.
echo Installing build and game dependencies...
%PYTHON% -m pip install -r requirements-build.txt
if errorlevel 1 goto build_failed

if not exist "build\windows" mkdir "build\windows"
if not exist "dist\windows" mkdir "dist\windows"

echo.
echo Building DEADGRID.exe. This may take a few minutes...
%PYTHON% -m PyInstaller --noconfirm --clean --onefile --windowed --name DEADGRID --add-data "%CD%\DeadGrid_ Gritty Urban Target Logo.png;." --distpath "%CD%\dist\windows" --workpath "%CD%\build\windows\work" --specpath "%CD%\build\windows" deadgrid.py
if errorlevel 1 goto build_failed

if not exist "dist\windows\DEADGRID.exe" goto missing_output
echo.
echo Build succeeded: "%CD%\dist\windows\DEADGRID.exe"
pause
exit /b 0

:no_python
echo.
echo ERROR: Python 3 was not found. Install Python 3.10 or newer and enable the Python launcher or add Python to PATH.
pause
exit /b 1

:build_failed
echo.
echo ERROR: The build failed. Read the error above, fix the reported dependency or packaging problem, then run this file again.
pause
exit /b 1

:missing_output
echo.
echo ERROR: PyInstaller finished without creating dist\windows\DEADGRID.exe.
pause
exit /b 1