@echo off
title 12_5 Tech - YT Downloader
color 0A

echo.
echo  ============================================
echo   12_5 Tech ^|^| YT Playlist Downloader
echo   Powered by yt-dlp + Flask + ffmpeg
echo  ============================================
echo.

:: ── Check Python ────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo  [ERROR] Python is not installed or not in PATH.
    echo.
    echo  Please install Python from: https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during install!
    echo.
    pause
    exit /b 1
)

echo  [OK] Python found.

:: ── Check pip ────────────────────────────────────────────────
pip --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo  [ERROR] pip not found. Please reinstall Python.
    pause
    exit /b 1
)

:: ── Create templates folder if missing ───────────────────────
if not exist "templates" (
    echo  [INFO] Creating templates\ folder...
    mkdir templates
)

:: ── Move index.html into templates if it's in the same folder ─
if exist "index.html" (
    if not exist "templates\index.html" (
        echo  [INFO] Moving index.html into templates\ folder...
        move "index.html" "templates\index.html" >nul
    )
)

:: ── Check app.py exists ───────────────────────────────────────
if not exist "app.py" (
    color 0C
    echo  [ERROR] app.py not found in this folder.
    echo  Make sure run.bat is in the same folder as app.py
    echo.
    pause
    exit /b 1
)

:: ── Install / upgrade dependencies ───────────────────────────
echo.
echo  [STEP 1/2] Installing dependencies...
echo  (This may take a minute on first run)
echo.

pip install --upgrade flask yt-dlp >nul 2>&1
if errorlevel 1 (
    echo  [WARN] pip upgrade had issues, trying without --upgrade...
    pip install flask yt-dlp
)

echo  [OK] Dependencies ready.

:: ── Check ffmpeg (optional but recommended) ───────────────────
echo.
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    color 0E
    echo  [WARN] ffmpeg not found. Video merging and Convert File tab won't work.
    echo.
    echo  To install ffmpeg:
    echo    1. Download from https://www.gyan.dev/ffmpeg/builds/
    echo    2. Extract and add the "bin" folder to your System PATH
    echo    OR install via: winget install ffmpeg
    echo.
    color 0A
    echo  Press any key to continue anyway (downloads may still work)...
    pause >nul
) else (
    echo  [OK] ffmpeg found.
)

:: ── Launch app ────────────────────────────────────────────────
echo.
echo  [STEP 2/2] Starting YT Downloader...
echo.
echo  ============================================
echo   App is running at: http://localhost:5050
echo   Opening browser automatically...
echo   Close this window to STOP the app.
echo  ============================================
echo.

python app.py

:: ── If app crashes, show error instead of window closing ─────
if errorlevel 1 (
    color 0C
    echo.
    echo  [ERROR] The app crashed. See error above.
    echo.
    pause
)
