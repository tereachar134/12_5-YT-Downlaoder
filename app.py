import os, sys, subprocess, threading, time, platform, json, queue
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, Response, jsonify, stream_with_context

app = Flask(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
DEFAULT_DIR = str(Path.home() / "Downloads" / "YT-Downloader")
os.makedirs(DEFAULT_DIR, exist_ok=True)
IS_WINDOWS  = platform.system() == "Windows"

# ── Shared state ─────────────────────────────────────────────────────────────
playlist_videos = []
active_process  = None
stop_flag       = threading.Event()
# Per-client SSE queues  {client_id: queue.Queue}
sse_queues: dict[str, queue.Queue] = {}

# ═══════════════════════════════════════════════════════════════════════════
#  STARTUP — auto-update yt-dlp
# ═══════════════════════════════════════════════════════════════════════════
def ts(): return datetime.now().strftime("%H:%M:%S")

def _startup_update():
    try:
        subprocess.run([sys.executable,"-m","pip","install","--upgrade","yt-dlp"],
                       capture_output=True, text=True, timeout=120)
        print("[startup] yt-dlp update done")
    except Exception as e:
        print(f"[startup] yt-dlp update failed: {e}")

threading.Thread(target=_startup_update, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════════════
#  SSE HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def push(client_id: str, msg: str, event="log"):
    """Push a message to a specific client's SSE queue."""
    q = sse_queues.get(client_id)
    if q:
        q.put(json.dumps({"event": event, "data": msg}))

def push_done(client_id: str, success: bool, saved_to: str = ""):
    push(client_id, json.dumps({"ok": success, "path": saved_to}), event="done")

def push_playlist(client_id: str, videos: list):
    push(client_id, json.dumps(videos), event="playlist")

# ═══════════════════════════════════════════════════════════════════════════
#  UTILITY
# ═══════════════════════════════════════════════════════════════════════════
def sanitize(p):
    path = Path(p.strip()) if p.strip() else Path(DEFAULT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)

def build_cookie_args(browser, cookie_file):
    """Return a list of yt-dlp cookie arguments (empty list if none)."""
    if cookie_file and cookie_file.strip() and os.path.isfile(cookie_file.strip()):
        return ["--cookies", cookie_file.strip()]
    browser = (browser or "None").strip()
    if browser == "None": return []
    return ["--cookies-from-browser", browser.lower()]

def build_extra_args(rate_limit, retries):
    """Return a list of yt-dlp extra flag arguments."""
    args = ["--retries", str(retries), "--fragment-retries", str(retries), "--retry-sleep", "5"]
    if rate_limit != "No limit":
        speed = rate_limit.replace("MB/s","M").replace("KB/s","K").strip()
        args += ["--limit-rate", speed]
    return args

# Keep old names as aliases so existing route code still works
def build_cookie_flag(browser, cookie_file):
    return " ".join(build_cookie_args(browser, cookie_file))

def build_extra_flags(rate_limit, retries):
    return " ".join(build_extra_args(rate_limit, retries))

def is_403(o): return any(x in o for x in ["HTTP Error 403","Forbidden","unable to download video data","Got error: 403"])
def is_429(o): return "429" in o or "Too Many Requests" in o
def is_sabr(o): return "SABR" in o or "sabr" in o.lower()

def fmt_fallback(cmd):
    for pat in [
        'bv*+ba/best',
        'bv*[height<=1080]+ba/best[height<=1080]',
        'bv*[height<=720]+ba/best[height<=720]',
        'bv*[height<=480]+ba/best[height<=480]',
    ]:
        cmd = cmd.replace(pat, 'best')
    return cmd

# ═══════════════════════════════════════════════════════════════════════════
#  RUN LIVE  (yields lines to client via SSE)
# ═══════════════════════════════════════════════════════════════════════════
def _parse_cookie_flag(flag_str):
    """Convert legacy cookie flag string back to (browser, cookie_file) for build_cookie_args."""
    if not flag_str: return "None", ""
    if "--cookies " in flag_str:
        return "None", flag_str.replace("--cookies","").strip().strip('"')
    if "--cookies-from-browser " in flag_str:
        browser = flag_str.replace("--cookies-from-browser","").strip()
        return browser, ""
    return "None", ""

def _parse_extra_flags(flag_str):
    """Convert legacy extra flags string to a list for shell=False calls."""
    if not flag_str: return []
    import shlex
    try: return shlex.split(flag_str)
    except: return flag_str.split()

BROWSERS = ["chrome","firefox","edge","brave","opera","chromium","safari"]

def run_and_stream(args, client_id):
    """Run a command (list of args, shell=False), stream output to SSE. Returns (ok, full_output)."""
    global active_process
    try:
        # shell=False + args list: the shell never sees the arguments, so
        # special characters like < > % are passed literally to the process.
        # This is the only reliable cross-platform approach.
        proc = subprocess.Popen(args, shell=False, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        active_process = proc
        out_lines = []
        for raw in proc.stdout:
            if stop_flag.is_set():
                proc.terminate(); proc.wait(); active_process = None
                push(client_id, f"[{ts()}] ⛔ Stopped by user.")
                return False, "\n".join(out_lines)
            line = raw.rstrip()
            if line:
                out_lines.append(line)
                push(client_id, line)
        proc.wait(); active_process = None
        return proc.returncode == 0, "\n".join(out_lines)
    except Exception as e:
        active_process = None
        push(client_id, f"Exception: {e}")
        return False, str(e)

# ── Format map — plain strings, no shell escaping needed (shell=False) ──────
# Format strings — prefer H.264 (avc1) + AAC for maximum compatibility with
# VLC, phones, TVs, Windows Media Player. AV1/VP9 look great but break many players.
# Pattern: try h264+aac first → h264+any audio → any codec at that height → fallback
# Format selectors — 3-level fallback chain for each quality:
#   Level 1: ext=mp4 video (H.264) + ext=m4a audio (AAC)
#            → muxed directly, zero re-encoding, plays on EVERY player
#   Level 2: any video at that height + any audio
#            → ffmpeg merges, may produce webm/mkv
#   Level 3: single best pre-merged file
#            → last resort, whatever YouTube provides
FMT_MAP = {
    "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
    "1080p": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p":  "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p":  "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]",
    "worst": "worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst",
}

def fmt_fallback_list(args):
    """Replace any DASH/codec-filtered format value in args list with plain 'best'."""
    result = list(args)
    for i, a in enumerate(result):
        # Replace any complex format selector with plain "best" as last resort fallback
        if isinstance(a, str) and any(k in a for k in ("ext=mp4","ext=m4a","height","vcodec","acodec","bestvideo","bv*")):
            result[i] = "best"
    return result

def smart_download(base_args, cookie_args, extra_args, out_dir, client_id):
    """
    base_args : list — e.g. ["yt-dlp","--no-playlist","-f","bv*[height<=1080]+ba/best[height<=1080]","--merge-output-format","mp4","--newline",url]
    cookie_args: list — from build_cookie_args()
    extra_args : list — from build_extra_args()
    Uses shell=False so < > % never touch cmd.exe — works on Windows and Linux.
    """
    stop_flag.clear()

    def make(label, xtr="", extra_opts=None, use_cookie=True, fallback=False):
        args = fmt_fallback_list(base_args) if fallback else list(base_args)
        if use_cookie: args += cookie_args
        args += extra_args
        if xtr: args += ["--extractor-args", f"youtube:{xtr}"]
        if extra_opts: args += extra_opts
        args += ["-P", out_dir]
        return label, args

    strategies = [
        make("tv_embedded client",     xtr="player_client=tv_embedded,mediaconnect"),
        make("Android + iOS",          xtr="player_client=android,ios"),
        make("Direct"),
        make("mweb client",            xtr="player_client=mweb"),
        make("tv_embedded + cookies",  xtr="player_client=tv_embedded,mediaconnect"),
        make("Android + IPv4",         xtr="player_client=android", extra_opts=["--force-ipv4"]),
        make("Single-stream fallback", xtr="player_client=android,tv_embedded", fallback=True),
        make("IPv4 + sleep",           extra_opts=["--force-ipv4","--sleep-interval","3","--max-sleep-interval","8"]),
    ]
    if not cookie_args:
        for b in BROWSERS:
            a = fmt_fallback_list(base_args) if False else list(base_args)
            a += ["--cookies-from-browser", b] + extra_args
            a += ["--extractor-args","youtube:player_client=android,tv_embedded","--force-ipv4","-P",out_dir]
            strategies.append((f"Cookies ({b})+android", a))
        for b in BROWSERS:
            a = fmt_fallback_list(base_args) + ["--cookies-from-browser", b] + extra_args
            a += ["--extractor-args","youtube:player_client=android","-P",out_dir]
            strategies.append((f"Fallback+{b}", a))

    sabr = False; tried = 0
    WEB = {"Direct","mweb client"}

    for label, args in strategies:
        if stop_flag.is_set(): push(client_id, f"[{ts()}] ⛔ Stopped."); return False
        if sabr and label in WEB: push(client_id, f"[{ts()}] ⏭ Skipping {label} (SABR)"); continue

        tried += 1
        push(client_id, f"\n[{ts()}] ▶ Strategy {tried}: {label}\n{chr(9472)*50}")
        ok, full_out = run_and_stream(args, client_id)
        if stop_flag.is_set(): push(client_id, f"[{ts()}] ⛔ Stopped."); return False
        if ok: push(client_id, f"\n[{ts()}] ✅ SUCCESS — {label}"); return True

        if is_sabr(full_out): sabr=True; push(client_id, f"[{ts()}] ⚠ SABR detected")
        if is_429(full_out): push(client_id, f"[{ts()}] ⏳ Rate limited — waiting 20s…"); time.sleep(20); continue
        if not is_403(full_out) and not is_sabr(full_out): push(client_id, f"[{ts()}] ❌ Failed — stopping retry"); return False

    push(client_id, f"\n[{ts()}] ❌ ALL {tried} STRATEGIES EXHAUSTED\n💡 Try: update yt-dlp · set cookie browser · export cookies.txt · VPN")
    return False

# ═══════════════════════════════════════════════════════════════════════════
#  DOWNLOAD WORKERS  (run in threads)
# ═══════════════════════════════════════════════════════════════════════════

def _make_base_args(url, fmt, is_audio):
    """Build base yt-dlp args list — shell=False so < > % are never interpreted."""
    if is_audio:
        return ["yt-dlp","--no-playlist",
                "-f","bestaudio[ext=m4a]/bestaudio",
                "--extract-audio","--audio-format",fmt,"--audio-quality","0",
                "--newline",url]
    else:
        # ext=mp4+ext=m4a streams are H.264+AAC — copy both streams directly,
        # no re-encoding needed → fast merge, correct MP4 that plays everywhere.
        # --merge-output-format mp4 ensures container is always MP4.
        return ["yt-dlp","--no-playlist",
                "-f", fmt,
                "--merge-output-format","mp4",
                "--postprocessor-args","ffmpeg:-c:v copy -c:a copy",
                "--newline",url]

def _worker_video(url, out_dir, quality, cookie_flag, extra_flags, client_id):
    stop_flag.clear()
    cookie_args = build_cookie_args(*_parse_cookie_flag(cookie_flag))
    extra_args  = _parse_extra_flags(extra_flags)
    fmt = FMT_MAP.get(quality, "bv*+ba/best")
    base_args = ["yt-dlp","--no-playlist","-f",fmt,"--merge-output-format","mp4",
                 "--postprocessor-args","ffmpeg:-c:v copy -c:a aac","--newline",url]
    push(client_id, f"[{ts()}] 🎬 Starting video download…\nSave to: {out_dir}\n{'='*56}")
    ok = smart_download(base_args, cookie_args, extra_args, out_dir, client_id)
    push_done(client_id, ok, out_dir)

def _worker_audio(url, out_dir, afmt, cookie_flag, extra_flags, client_id):
    stop_flag.clear()
    cookie_args = build_cookie_args(*_parse_cookie_flag(cookie_flag))
    extra_args  = _parse_extra_flags(extra_flags)
    base_args = ["yt-dlp","--no-playlist","-f","bestaudio","--extract-audio",
                 "--audio-format",afmt,"--audio-quality","0","--newline",url]
    push(client_id, f"[{ts()}] 🎵 Starting audio download…\nSave to: {out_dir}\n{'='*56}")
    ok = smart_download(base_args, cookie_args, extra_args, out_dir, client_id)
    push_done(client_id, ok, out_dir)

def _worker_playlist_all(out_dir, quality, mode, skip_done, cookie_flag, extra_flags, client_id):
    global playlist_videos
    stop_flag.clear()
    cookie_args = build_cookie_args(*_parse_cookie_flag(cookie_flag))
    extra_args  = _parse_extra_flags(extra_flags)
    is_audio = mode == "audio"
    fmt = "mp3" if is_audio else FMT_MAP.get(quality, "bv*+ba/best")
    total = len(playlist_videos); done = 0; failed = 0
    push(client_id, f"[{ts()}] 📋 Batch — {total} videos\nSave to: {out_dir}\n{'='*56}")
    for i, v in enumerate(playlist_videos):
        if stop_flag.is_set(): push(client_id, f"[{ts()}] ⛔ Stopped."); break
        if skip_done and v["status"] == "done":
            playlist_videos[i]["status"] = "skipped"; push_playlist(client_id, playlist_videos); continue
        playlist_videos[i]["status"] = "downloading"; push_playlist(client_id, playlist_videos)
        push(client_id, f"\n[{ts()}] [{i+1}/{total}] {v['title'][:60]}\n{'─'*48}")
        base_args = _make_base_args(v["url"], fmt, is_audio)
        ok = smart_download(base_args, cookie_args, extra_args, out_dir, client_id)
        playlist_videos[i]["status"] = "done" if ok else "failed"
        if ok: done+=1
        else: failed+=1
        push_playlist(client_id, playlist_videos)
    push(client_id, f"\n{'='*56}\n[{ts()}] Done — ✅ {done}  ❌ {failed}\nSaved to: {out_dir}")
    push_done(client_id, failed==0, out_dir)

def _worker_playlist_range(start, end, out_dir, quality, mode, skip_done, cookie_flag, extra_flags, client_id):
    global playlist_videos
    stop_flag.clear()
    cookie_args = build_cookie_args(*_parse_cookie_flag(cookie_flag))
    extra_args  = _parse_extra_flags(extra_flags)
    total = len(playlist_videos)
    s = max(0, start-1); e = min(end, total)-1
    is_audio = mode == "audio"
    fmt = "mp3" if is_audio else FMT_MAP.get(quality, "bv*+ba/best")
    rng = e-s+1; done_c = 0; fail_c = 0
    push(client_id, f"[{ts()}] 📋 Range #{start}–#{end} ({rng} videos)\nSave to: {out_dir}\n{'='*56}")
    for i in range(s, e+1):
        v = playlist_videos[i]
        if stop_flag.is_set(): push(client_id, f"[{ts()}] ⛔ Stopped."); break
        if skip_done and v["status"] == "done":
            playlist_videos[i]["status"] = "skipped"; push_playlist(client_id, playlist_videos); continue
        playlist_videos[i]["status"] = "downloading"; push_playlist(client_id, playlist_videos)
        push(client_id, f"\n[{ts()}] [{i-s+1}/{rng}] #{i+1} {v['title'][:55]}\n{'─'*48}")
        base_args = _make_base_args(v["url"], fmt, is_audio)
        ok = smart_download(base_args, cookie_args, extra_args, out_dir, client_id)
        playlist_videos[i]["status"] = "done" if ok else "failed"
        if ok: done_c+=1
        else: fail_c+=1
        push_playlist(client_id, playlist_videos)
    push(client_id, f"\n[{ts()}] Range done — ✅ {done_c}  ❌ {fail_c}")
    push_done(client_id, fail_c==0, out_dir)

def _worker_playlist_one(idx, out_dir, quality, mode, cookie_flag, extra_flags, client_id):
    global playlist_videos
    stop_flag.clear()
    cookie_args = build_cookie_args(*_parse_cookie_flag(cookie_flag))
    extra_args  = _parse_extra_flags(extra_flags)
    i = idx-1
    is_audio = mode == "audio"
    fmt = "mp3" if is_audio else FMT_MAP.get(quality, "bv*+ba/best")
    v = playlist_videos[i]; playlist_videos[i]["status"] = "downloading"
    push_playlist(client_id, playlist_videos)
    push(client_id, f"[{ts()}] #{idx}: {v['title']}\n{'='*56}")
    base_args = _make_base_args(v["url"], fmt, is_audio)
    ok = smart_download(base_args, cookie_args, extra_args, out_dir, client_id)
    playlist_videos[i]["status"] = "done" if ok else "failed"
    push_playlist(client_id, playlist_videos)
    push_done(client_id, ok, out_dir)

def _worker_convert(src, afmt, bitrate, client_id):
    stop_flag.clear()
    dst   = os.path.splitext(src)[0]+f"_converted.{afmt}"
    codec = {"mp3":"libmp3lame","aac":"aac","flac":"flac","wav":"pcm_s16le","opus":"libopus"}.get(afmt,"libmp3lame")
    # ffmpeg also uses shell=False — args list keeps paths with spaces safe
    args = ["ffmpeg","-i",src,"-vn","-acodec",codec,"-ab",bitrate,dst,"-y"]
    push(client_id, f"[{ts()}] 🔄 Converting…\n{' '.join(args)}\n{'─'*56}")
    ok, _ = run_and_stream(args, client_id)
    push(client_id, f"\n[{ts()}] {'✅ Saved: '+dst if ok else '❌ Conversion failed.'}")
    push_done(client_id, ok, dst)

# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html",
        default_dir=DEFAULT_DIR,
        platform=platform.system())

# ── SSE stream ───────────────────────────────────────────────────────────────
@app.route("/stream/<client_id>")
def stream(client_id):
    q = queue.Queue()
    sse_queues[client_id] = q
    def generate():
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    data = json.loads(msg)
                    yield f"event: {data['event']}\ndata: {data['data']}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            sse_queues.pop(client_id, None)
    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ── Download video ────────────────────────────────────────────────────────────
@app.route("/api/download/video", methods=["POST"])
def api_download_video():
    d = request.json
    client_id   = d.get("client_id","")
    url         = d.get("url","").strip()
    out_dir     = sanitize(d.get("save_dir", DEFAULT_DIR))
    quality     = d.get("quality","1080p")
    cookie_flag = build_cookie_flag(d.get("browser","None"), d.get("cookie_file",""))
    extra_flags = build_extra_flags(d.get("rate_limit","No limit"), d.get("retries","5"))
    if not url: return jsonify(error="No URL"), 400
    threading.Thread(target=_worker_video, args=(url,out_dir,quality,cookie_flag,extra_flags,client_id), daemon=True).start()
    return jsonify(ok=True)

@app.route("/api/download/audio", methods=["POST"])
def api_download_audio():
    d = request.json
    client_id   = d.get("client_id","")
    url         = d.get("url","").strip()
    out_dir     = sanitize(d.get("save_dir", DEFAULT_DIR))
    afmt        = d.get("audio_fmt","mp3").lower()
    cookie_flag = build_cookie_flag(d.get("browser","None"), d.get("cookie_file",""))
    extra_flags = build_extra_flags(d.get("rate_limit","No limit"), d.get("retries","5"))
    if not url: return jsonify(error="No URL"), 400
    threading.Thread(target=_worker_audio, args=(url,out_dir,afmt,cookie_flag,extra_flags,client_id), daemon=True).start()
    return jsonify(ok=True)

# ── Playlist ──────────────────────────────────────────────────────────────────
@app.route("/api/playlist/fetch", methods=["POST"])
def api_fetch_playlist():
    global playlist_videos
    d = request.json
    url = d.get("url","").strip()
    if not url: return jsonify(error="No URL"), 400

    # Build args list — shell=False bypasses cmd.exe on Windows entirely,
    # so % characters in the --print format are never expanded as env vars.
    args = ["yt-dlp", "--flat-playlist",
            "--print", "%(id)s|||%(title)s|||%(url)s",
            "--no-warnings"]

    # Add cookie args if set
    browser = (d.get("browser","None") or "None").strip()
    cookie_file = d.get("cookie_file","")
    if cookie_file and cookie_file.strip() and os.path.isfile(cookie_file.strip()):
        args += ["--cookies", cookie_file.strip()]
    elif browser != "None":
        args += ["--cookies-from-browser", browser.lower()]

    args.append(url)

    try:
        result = subprocess.run(args, shell=False, capture_output=True, text=True, timeout=90)
        lines  = [l for l in result.stdout.strip().split("\n") if "|||" in l]
        videos = []
        for line in lines:
            parts = line.split("|||")
            if len(parts) >= 2:
                vid_id  = parts[0].strip(); title = parts[1].strip()
                vid_url = parts[2].strip() if len(parts)>2 else f"https://www.youtube.com/watch?v={vid_id}"
                videos.append({"id":vid_id,"title":title,"url":vid_url,"status":"queued"})
        playlist_videos = videos
        return jsonify(videos=videos)
    except subprocess.TimeoutExpired:
        return jsonify(error="Timeout — playlist took too long"), 408
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route("/api/playlist/download/one", methods=["POST"])
def api_dl_one():
    d = request.json
    idx = int(d.get("index",1)); client_id = d.get("client_id","")
    out_dir = sanitize(d.get("save_dir", DEFAULT_DIR))
    cookie_flag = build_cookie_flag(d.get("browser","None"), d.get("cookie_file",""))
    extra_flags = build_extra_flags(d.get("rate_limit","No limit"), d.get("retries","5"))
    threading.Thread(target=_worker_playlist_one,
        args=(idx, out_dir, d.get("quality","1080p"), d.get("mode","video"), cookie_flag, extra_flags, client_id), daemon=True).start()
    return jsonify(ok=True)

@app.route("/api/playlist/download/range", methods=["POST"])
def api_dl_range():
    d = request.json; client_id = d.get("client_id","")
    out_dir = sanitize(d.get("save_dir", DEFAULT_DIR))
    cookie_flag = build_cookie_flag(d.get("browser","None"), d.get("cookie_file",""))
    extra_flags = build_extra_flags(d.get("rate_limit","No limit"), d.get("retries","5"))
    threading.Thread(target=_worker_playlist_range,
        args=(int(d.get("start",1)), int(d.get("end",1)), out_dir, d.get("quality","1080p"),
              d.get("mode","video"), d.get("skip_done",True), cookie_flag, extra_flags, client_id), daemon=True).start()
    return jsonify(ok=True)

@app.route("/api/playlist/download/all", methods=["POST"])
def api_dl_all():
    d = request.json; client_id = d.get("client_id","")
    out_dir = sanitize(d.get("save_dir", DEFAULT_DIR))
    cookie_flag = build_cookie_flag(d.get("browser","None"), d.get("cookie_file",""))
    extra_flags = build_extra_flags(d.get("rate_limit","No limit"), d.get("retries","5"))
    threading.Thread(target=_worker_playlist_all,
        args=(out_dir, d.get("quality","1080p"), d.get("mode","video"), d.get("skip_done",True),
              cookie_flag, extra_flags, client_id), daemon=True).start()
    return jsonify(ok=True)

@app.route("/api/playlist/reset", methods=["POST"])
def api_reset():
    global playlist_videos
    for v in playlist_videos: v["status"] = "queued"
    return jsonify(videos=playlist_videos)

# ── Convert ───────────────────────────────────────────────────────────────────
@app.route("/api/convert", methods=["POST"])
def api_convert():
    d = request.json; client_id = d.get("client_id","")
    src = d.get("path","").strip()
    if not src or not os.path.isfile(src): return jsonify(error="File not found"), 404
    threading.Thread(target=_worker_convert,
        args=(src, d.get("format","mp3"), d.get("bitrate","320k"), client_id), daemon=True).start()
    return jsonify(ok=True)

# ── Stop ──────────────────────────────────────────────────────────────────────
@app.route("/api/stop", methods=["POST"])
def api_stop():
    global stop_flag
    stop_flag.set()
    if active_process:
        try: active_process.terminate()
        except: pass
    return jsonify(ok=True)

# ── Settings ──────────────────────────────────────────────────────────────────
@app.route("/api/update_ytdlp", methods=["POST"])
def api_update():
    client_id = request.json.get("client_id","")
    def _run():
        push(client_id, f"[{ts()}] Updating yt-dlp…")
        result = subprocess.run([sys.executable,"-m","pip","install","--upgrade","yt-dlp"],
                                capture_output=True, text=True, timeout=120)
        for line in (result.stdout+result.stderr).splitlines():
            if line.strip(): push(client_id, line)
        push(client_id, f"[{ts()}] Done.")
        push_done(client_id, True)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify(ok=True)

@app.route("/api/check_tools")
def api_check_tools():
    results = []
    for tool, flag in [("yt-dlp","--version"),("ffmpeg","-version"),("python","--version")]:
        try:
            r = subprocess.run(f"{tool} {flag}", shell=True, capture_output=True, text=True)
            ver = (r.stdout or r.stderr).split("\n")[0][:60]
            results.append({"tool":tool,"status":"ok","version":ver})
        except:
            results.append({"tool":tool,"status":"error","version":"NOT FOUND"})
    results.append({"tool":"platform","status":"ok","version":f"{platform.system()} {platform.release()}"})
    return jsonify(results=results)

if __name__ == "__main__":
    import webbrowser
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5050")).start()
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
