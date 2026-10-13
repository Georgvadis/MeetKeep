@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 (
        echo Установите Python с python.org и включите Python Launcher.
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo Не удалось установить зависимости.
    pause
    exit /b 1
)
if not exist "frontend\dist\index.html" (
    pushd frontend
    call npm ci && call npm run build
    if errorlevel 1 (
        echo Для сборки интерфейса установите Node.js с nodejs.org.
        pause
        exit /b 1
    )
    popd
)
".venv\Scripts\python.exe" meetkeep.py
