@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller imageio-ffmpeg
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -c "import imageio_ffmpeg, shutil; from pathlib import Path; p = Path('.ffmpeg-bundle'); p.mkdir(exist_ok=True); shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), p / 'ffmpeg.exe')"
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name "Vidloader" --add-data "index.html;." --add-binary ".ffmpeg-bundle\ffmpeg.exe;." sbervideo_downloader.py
if errorlevel 1 exit /b 1
echo Приложение: dist\Vidloader.exe
