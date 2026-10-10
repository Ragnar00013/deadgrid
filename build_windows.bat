@echo off
setlocal

python -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

if not exist build\windows mkdir build\windows
python -m PyInstaller --noconfirm --clean --onefile --windowed --name DEADGRID --add-data "DeadGrid_ Gritty Urban Target Logo.png;." --distpath dist\windows --workpath build\windows\work --specpath build\windows deadgrid.py
if errorlevel 1 exit /b 1

echo Built dist\windows\DEADGRID.exe