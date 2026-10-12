#!/bin/zsh
set -e
cd "${0:A:h}"
./start-mac.command --prepare-only
.venv/bin/python -m pip install -q pyinstaller imageio-ffmpeg
FFMPEG_PATH="$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
mkdir -p .ffmpeg-bundle
cp "$FFMPEG_PATH" .ffmpeg-bundle/ffmpeg
.venv/bin/python -m PyInstaller --noconfirm --clean --onedir --windowed \
  --name "Vidloader" --add-data "index.html:." --add-binary ".ffmpeg-bundle/ffmpeg:." sbervideo_downloader.py
echo "Приложение: dist/Vidloader.app"
