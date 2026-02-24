# 📺 12_5 Tech - YT Downloader

A powerful and user-friendly YouTube Downloader built with **Flask**, **yt-dlp**, and **FFmpeg**. This tool allows you to download individual videos, entire playlists, or specific ranges from a playlist in high quality (up to 4K/1080p) or as high-quality audio (MP3/AAC).

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)
![yt-dlp](https://img.shields.io/badge/yt--dlp-Latest-red.svg)

## ✨ Features

- **High-Quality Video**: Download videos in 1080p, 720p, or 480p with optimized H.264+AAC encoding.
- **Audio Extraction**: Convert videos directly to MP3, AAC, FLAC, or Opus with high bitrates.
- **Playlist Management**:
  - Fetch entire playlists with one click.
  - Download all videos, a specific range (e.g., videos 5 to 10), or individual items.
  - Track download status (Queued, Downloading, Done, Failed).
- **Smart Strategies**: Built-in multi-strategy retry system to bypass "403 Forbidden" or "429 Too Many Requests" errors.
- **Live Logging**: Real-time progress updates via Server-Sent Events (SSE).
- **File Conversion**: Built-in tool to convert existing files to different audio formats.
- **One-Click Startup**: Windows users can use `run.bat` for automatic dependency installation and launch.

## 🚀 Installation & Setup

### Prerequisites

1.  **Python 3.8+**: [Download Python](https://www.python.org/downloads/) (Ensure "Add Python to PATH" is checked).
2.  **FFmpeg**: Required for merging video/audio and file conversion.
    - **Windows**: `winget install ffmpeg` or download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/).
    - **Linux**: `sudo apt update && sudo apt install ffmpeg`.

### Quick Start (Windows)

Just double-click the `run.bat` file. It will:
1. Verify Python and pip.
2. Install/Upgrade `flask` and `yt-dlp`.
3. Check for FFmpeg.
4. Launch the web interface (prefers `http://localhost:5050`; if busy, auto-falls back to next open port).

### Manual Setup (Linux/Mac/Manual Windows)

1. **Clone the repository**:
   ```bash
   git clone https://github.com/tereachar134/12_5-YT-Downlaoder.git
   cd 12_5-YT-Downlaoder
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the app**:
   ```bash
   python app.py
   ```

## 🛠 Usage Instructions

After starting the app, open the URL shown in terminal output (usually `http://localhost:5050`; if that port is busy it will auto-use the next available port).

### 1) Single Download (Video/Audio)
1. Open the **Single Download** panel.
2. Paste a supported URL in the **URL** field.
3. Set your **Save Folder**.
4. Choose either:
   - **Video**: select quality (Best/1080p/720p/480p), or
   - **Audio**: select format + bitrate.
5. Click **Download Video** or **Download Audio**.
6. Track progress in **Live Terminal Output**.

### 2) Playlist Download
1. Open the **Playlist Manager** panel.
2. Paste the playlist URL and click **Load**.
3. Choose mode (**Video** or **Audio**) and quality.
4. Download by scope:
   - **Single** item,
   - **Range** (From → To), or
   - **Download All**.
5. Watch statuses and logs to monitor queued/downloading/done/failed items.

### 3) Convert Local File
1. Open the **Convert File** panel.
2. Enter the local media file path.
3. Choose output format and bitrate.
4. Click **Convert to Audio**.

### 4) Settings & Maintenance
Use the **Settings** panel to:
- check tool versions/status (`yt-dlp`, `ffmpeg`, Python),
- update `yt-dlp`,
- review troubleshooting guidance for restricted videos.

### Troubleshooting "403 Forbidden"
If you encounter access errors, use the **Settings** tab to:
- Update `yt-dlp` to the latest version.
- Select your browser (Chrome/Edge/Firefox) in the **Cookies** section to use your browser session for authentication.

## 📜 Requirements

See [requirements.txt](requirements.txt) for the list of Python packages.

---
*Developed by **12_5 Tech***
