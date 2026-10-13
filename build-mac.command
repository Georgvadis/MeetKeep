#!/bin/zsh
set -e
cd "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v npm >/dev/null; then
  echo "Установите Node.js (brew install node): он нужен для сборки интерфейса."
  exit 1
fi
(cd frontend && npm ci && npm run build)
./start-mac.command --prepare-only
.venv/bin/python -m pip install -q pyinstaller imageio-ffmpeg
FFMPEG_PATH="$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
mkdir -p .ffmpeg-bundle
cp "$FFMPEG_PATH" .ffmpeg-bundle/ffmpeg
.venv/bin/python -m PyInstaller --noconfirm --clean --onedir --windowed \
  --name "MeetKeep" --icon "assets/icon.icns" --add-data "frontend/dist:ui" --add-binary ".ffmpeg-bundle/ffmpeg:." meetkeep.py
# PyInstaller also leaves the bare onedir folder next to the bundle; ship only the .app.
rm -rf dist/MeetKeep
echo "Приложение: dist/MeetKeep.app"
