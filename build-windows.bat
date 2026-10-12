@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller imageio-ffmpeg
for /f "delims=" %%I in ('".venv\Scripts\python.exe" -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"') do set "FFMPEG_PATH=%%I"
if not exist ".ffmpeg-bundle" mkdir ".ffmpeg-bundle"
copy /Y "%FFMPEG_PATH%" ".ffmpeg-bundle\ffmpeg.exe" >nul
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name "Video Downloader" --add-data "index.html;." --add-binary ".ffmpeg-bundle\ffmpeg.exe;." sbervideo_downloader.py
echo Приложение: dist\Video Downloader.exe
pause
