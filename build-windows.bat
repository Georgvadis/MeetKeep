@echo off
setlocal
cd /d "%~dp0"
where npm >nul 2>nul
if errorlevel 1 (
    echo Установите Node.js с nodejs.org: он нужен для сборки интерфейса.
    exit /b 1
)
pushd frontend
call npm ci
if errorlevel 1 exit /b 1
call npm run build
if errorlevel 1 exit /b 1
popd
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller imageio-ffmpeg
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -c "import imageio_ffmpeg, shutil; from pathlib import Path; p = Path('.ffmpeg-bundle'); p.mkdir(exist_ok=True); shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), p / 'ffmpeg.exe')"
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name "MeetKeep" --icon "assets\icon.ico" --add-data "frontend\dist;ui" --add-data "extension;extension" --add-binary ".ffmpeg-bundle\ffmpeg.exe;." meetkeep.py
if errorlevel 1 exit /b 1
echo Приложение: dist\MeetKeep.exe
