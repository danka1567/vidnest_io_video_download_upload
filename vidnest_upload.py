#!/usr/bin/env python3
"""
vidnest_upload.py
=================
Download any media (M3U8, HLS, MP4, MKV, YouTube, etc.) at maximum speed
using yt-dlp + aria2c 16-thread engine, then upload locally to VidNest API.

Usage:
    python vidnest_upload.py <url> [--title "My Video"] [--folder 25] [--private] [--remote]

Default: local file upload (download first, then upload)
"""

import os
import sys
import time
import json
import argparse
import tempfile
import subprocess
from pathlib import Path

import requests

# ══════════════════════════════════════════════════════════════
#  HARDCODED CREDENTIALS
# ══════════════════════════════════════════════════════════════
API_KEY  = "15343rbnd51sbh9vc1h7p"
API_BASE = "https://vidnest.io/api"
PROFILE  = "https://vidnest.io/users/donkaboy"

# ══════════════════════════════════════════════════════════════
#  HTTP SESSION  (browser headers → avoids 403)
# ══════════════════════════════════════════════════════════════
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://vidnest.io/",
})


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def format_size(size_bytes) -> str:
    if not size_bytes or size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i, size = 0, float(size_bytes)
    while size >= 1024.0 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    return f"{size:.2f} {units[i]}"


def banner(text: str):
    bar = "═" * 70
    print(f"\n{bar}\n  {text}\n{bar}", flush=True)


def api_get(endpoint: str, params: dict) -> dict:
    params["key"] = API_KEY
    url = f"{API_BASE}/{endpoint}"
    print(f"  → GET {url}", flush=True)
    r = SESSION.get(url, params=params, timeout=30)
    print(f"  ← HTTP {r.status_code}", flush=True)
    r.raise_for_status()
    return r.json()


# ══════════════════════════════════════════════════════════════
#  DOWNLOAD  (yt-dlp + aria2c 16-thread engine)
# ══════════════════════════════════════════════════════════════

def high_speed_download(media_url: str, output_dir: str,
                        custom_name: str = None,
                        user_agent: str = None) -> Path:
    """
    Downloads M3U8 / HLS / MP4 / any stream using yt-dlp + aria2c
    16-connection multi-thread engine with automatic fallback.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    out_template = custom_name if custom_name else "%(title)s.%(ext)s"
    out_path_template = os.path.join(output_dir, out_template)

    banner("⚡ [1/2] ULTRA-FAST MULTI-THREADED MEDIA DOWNLOAD")
    print(f"  URL: {media_url}", flush=True)

    # Primary engine: yt-dlp + aria2c (16 connections, 16 HLS fragments)
    cmd_primary = [
        "yt-dlp",
        "--downloader", "aria2c",
        "--downloader-args",
            "aria2c:-x 16 -s 16 -k 1M "
            "--max-connection-per-server=16 "
            "--min-split-size=1M "
            "--optimize-concurrent-downloads=true "
            "--file-allocation=none",
        "--concurrent-fragments", "16",
        "--hls-use-mpegts",
        "--retries",          "10",
        "--fragment-retries", "10",
        "--no-check-certificates",
        "-o", out_path_template,
        media_url,
    ]
    if user_agent:
        cmd_primary += ["--user-agent", user_agent]

    # Fallback engine: native yt-dlp/ffmpeg (no aria2c needed)
    cmd_fallback = [
        "yt-dlp",
        "--concurrent-fragments", "16",
        "--retries",          "10",
        "--fragment-retries", "10",
        "--no-check-certificates",
        "-o", out_path_template,
        media_url,
    ]
    if user_agent:
        cmd_fallback += ["--user-agent", user_agent]

    start = time.time()
    try:
        print("[Engine] Launching aria2c + yt-dlp pipeline...", flush=True)
        subprocess.run(cmd_primary, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"[Warning] aria2c engine failed ({e}). Falling back to yt-dlp/ffmpeg...", flush=True)
        subprocess.run(cmd_fallback, check=True)

    elapsed = time.time() - start

    # Find the downloaded file (most recently modified)
    files = [f for f in Path(output_dir).iterdir()
             if f.is_file() and not f.name.startswith(".")]
    if not files:
        print("❌  ERROR: No downloaded file found!", file=sys.stderr)
        sys.exit(1)

    downloaded = max(files, key=lambda p: p.stat().st_mtime)
    size = downloaded.stat().st_size

    print(f"\n✅ [DOWNLOAD COMPLETE]", flush=True)
    print(f"  • File    : {downloaded.name}")
    print(f"  • Size    : {format_size(size)}")
    print(f"  • Duration: {elapsed:.2f}s")
    return downloaded


# ══════════════════════════════════════════════════════════════
#  UPLOAD PROGRESS WRAPPER
# ══════════════════════════════════════════════════════════════

class ProgressReader:
    """Wraps a file to print live upload progress."""
    def __init__(self, path: Path):
        self.total = path.stat().st_size
        self._f    = open(path, "rb")
        self.sent  = 0
        self._last = 0

    def read(self, size=-1):
        chunk = self._f.read(size)
        if chunk:
            self.sent += len(chunk)
            now = time.time()
            if now - self._last > 0.5 or self.sent == self.total:
                pct = (self.sent / self.total * 100) if self.total else 100
                print(
                    f"\r  🚀 Uploading: {format_size(self.sent)} / "
                    f"{format_size(self.total)} ({pct:.1f}%)",
                    end="", flush=True
                )
                self._last = now
        return chunk

    def __len__(self):      return self.total
    def __enter__(self):    return self
    def __exit__(self, *_): self._f.close()


# ══════════════════════════════════════════════════════════════
#  VIDNEST UPLOAD
# ══════════════════════════════════════════════════════════════

def get_upload_server() -> str:
    print("\n🔍  Getting VidNest upload server...", flush=True)
    resp = api_get("upload/server", {})
    server = resp.get("result", "")
    if not server:
        raise RuntimeError(f"Could not get upload server: {resp}")
    print(f"  ✅  Server: {server}", flush=True)
    return server


def upload_local(file_path: Path, title: str, folder_id: str, public: int) -> dict:
    """Multipart POST of local file to VidNest upload server (default)."""
    server = get_upload_server()

    banner(f"📤 [2/2] UPLOADING TO VIDNEST")
    print(f"  • File  : {file_path.name}")
    print(f"  • Size  : {format_size(file_path.stat().st_size)}")
    print(f"  • Server: {server}", flush=True)

    post_data = {
        "key":         API_KEY,
        "file_public": str(public),
    }
    if title:
        post_data["file_title"] = title
    if folder_id:
        post_data["fld_id"] = folder_id

    start = time.time()
    with ProgressReader(file_path) as reader:
        resp = SESSION.post(
            server,
            data=post_data,
            files={"file": (file_path.name, reader, "application/octet-stream")},
            timeout=7200,
        )
    elapsed = time.time() - start

    print(f"\n  Upload finished in {elapsed:.2f}s", flush=True)
    print(f"  ← HTTP {resp.status_code}", flush=True)
    print(f"  Raw: {resp.text[:500]}", flush=True)
    resp.raise_for_status()
    return resp.json()


def upload_remote(url: str, folder_id: str, public: int) -> dict:
    """Submit a URL for VidNest to fetch itself (non-default)."""
    print("\n📡  Submitting remote URL to VidNest...", flush=True)
    params = {"url": url, "file_public": public}
    if folder_id:
        params["fld_id"] = folder_id
    return api_get("upload/url", params)


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Download any media and upload to VidNest"
    )
    parser.add_argument("url",                   help="Media URL (m3u8, mp4, YouTube, etc.)")
    parser.add_argument("--title",   default="", help="File title on VidNest")
    parser.add_argument("--folder",  default="", help="VidNest folder ID")
    parser.add_argument("--name",    default=None, help="Custom output filename (e.g. video.mp4)")
    parser.add_argument("--user-agent", default=None, help="Custom User-Agent for download")
    parser.add_argument("--outdir",  default="./downloads", help="Local download directory")
    parser.add_argument("--private", action="store_true", help="Make uploaded file private")
    parser.add_argument("--remote",  action="store_true",
                        help="Skip download — let VidNest fetch URL directly "
                             "(direct mp4/mkv only, NOT default)")
    args = parser.parse_args()

    public = 0 if args.private else 1

    banner("🎬  VidNest Uploader")
    print(f"  URL    : {args.url}")
    print(f"  Title  : {args.title or '(none)'}")
    print(f"  Folder : {args.folder or 'root'}")
    print(f"  Public : {'yes' if public else 'no'}")
    print(f"  Method : {'remote URL (non-default)' if args.remote else 'local file upload (default)'}")

    # ── Remote upload (non-default) ──────────────────────────────────────────
    if args.remote:
        resp = upload_remote(args.url, args.folder, public)
        banner("📨  Remote Upload Queued")
        print(json.dumps(resp, indent=2))
        fc = (resp.get("result") or {}).get("filecode", "")
        if fc:
            print(f"\n  🔗  Future URL : https://vidnest.io/{fc}")
            print(f"  ⏳  VidNest will fetch asynchronously.")
        print(f"  👤  Profile    : {PROFILE}\n")
        return

    # ── Default: download → local upload ─────────────────────────────────────
    with tempfile.TemporaryDirectory(prefix="vidnest_") as tmpdir:
        downloaded = high_speed_download(
            media_url=args.url,
            output_dir=tmpdir,
            custom_name=args.name,
            user_agent=args.user_agent,
        )
        resp = upload_local(downloaded, args.title, args.folder, public)

    # ── Result ────────────────────────────────────────────────────────────────
    banner("✅  ALL DONE")
    print(json.dumps(resp, indent=2))
    files = resp.get("files", [])
    if files:
        fc = files[0].get("filecode", "")
        print(f"\n  🎬  File code : {fc}")
        print(f"  🔗  Watch URL : https://vidnest.io/{fc}")
    print(f"  👤  Profile  : {PROFILE}\n")


if __name__ == "__main__":
    main()
