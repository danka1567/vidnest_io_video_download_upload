#!/usr/bin/env python3
"""
vidnest_upload.py
=================
Download any media (HLS/m3u8, mp4, YouTube, etc.) at maximum speed
and upload it to VidNest via local file upload.

Usage:
    python vidnest_upload.py <url> [--title "My Video"] [--folder 25] [--private]

Defaults:
    - Upload method : local file upload  (default)
    - Remote upload : disabled by default, use --remote flag
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ── Hardcoded API key ─────────────────────────────────────────────────────────
API_KEY  = "15343rbnd51sbh9vc1h7p"
API_BASE = "https://vidnest.io/api"
PROFILE  = "https://vidnest.io/users/donkaboy"

# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, stream output, raise on failure."""
    print(f"\n$ {' '.join(cmd)}\n", flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def api_get(endpoint: str, params: dict) -> dict:
    """Simple GET request to VidNest API, returns parsed JSON."""
    params["key"] = API_KEY
    qs  = urllib.parse.urlencode(params)
    url = f"{API_BASE}/{endpoint}?{qs}"
    print(f"  → GET {url}", flush=True)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def human_size(path: str) -> str:
    size = os.path.getsize(path)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def detect_type(url: str) -> str:
    """Return 'hls', 'direct', or 'ytdlp'."""
    lower = url.lower().split("?")[0]
    if ".m3u8" in lower or ".m3u" in lower:
        return "hls"
    direct_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv",
                   ".flv", ".webm", ".ts", ".mpeg", ".mpg", ".m4v"}
    if any(lower.endswith(e) for e in direct_exts):
        return "direct"
    return "ytdlp"


# ── Downloaders ───────────────────────────────────────────────────────────────

def download_hls(url: str, outdir: str) -> str:
    """ffmpeg — fastest HLS merge with multi-threading."""
    outfile = os.path.join(outdir, "output.mp4")
    run([
        "ffmpeg", "-y",
        "-protocol_whitelist", "file,crypto,data,https,http,tcp,tls",
        "-reconnect",          "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max","10",
        "-i", url,
        "-c", "copy",
        "-movflags", "+faststart",
        "-threads", "0",
        outfile
    ])
    return outfile


def download_direct(url: str, outdir: str) -> str:
    """aria2c — 16 parallel connections for max speed."""
    raw_name = os.path.basename(url.split("?")[0])
    filename = re.sub(r"[^\w.\-]", "_", raw_name) or "output.mp4"
    run([
        "aria2c",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--max-concurrent-downloads=1",
        "--continue=true",
        "--file-allocation=none",
        "--retry-wait=5",
        "--max-tries=10",
        "--timeout=120",
        "--connect-timeout=30",
        "--piece-length=1M",
        "--disk-cache=64M",
        "--async-dns=true",
        "--log-level=warn",
        f"--dir={outdir}",
        f"--out={filename}",
        url
    ])
    return os.path.join(outdir, filename)


def download_ytdlp(url: str, outdir: str) -> str:
    """yt-dlp — 16 concurrent fragments, best quality."""
    outtemplate = os.path.join(outdir, "output.%(ext)s")
    run([
        "yt-dlp",
        "--format",            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "--merge-output-format","mp4",
        "--concurrent-fragments","16",
        "--buffer-size",       "16K",
        "--http-chunk-size",   "10M",
        "--retries",           "10",
        "--fragment-retries",  "10",
        "--retry-sleep",       "5",
        "--no-playlist",
        "--output",            outtemplate,
        url
    ])
    files = sorted(Path(outdir).glob("output.*"))
    if not files:
        raise FileNotFoundError("yt-dlp produced no output file")
    return str(files[0])


def download(url: str, outdir: str) -> str:
    """Route URL to the best downloader, return local file path."""
    kind = detect_type(url)
    print(f"\n{'─'*56}")
    print(f"  📥  URL type detected: {kind.upper()}")
    print(f"{'─'*56}")

    if kind == "hls":
        return download_hls(url, outdir)

    if kind == "direct":
        return download_direct(url, outdir)

    # ytdlp — try it; fall back to aria2c if it can't handle the URL
    try:
        result = subprocess.run(
            ["yt-dlp", "--simulate", url],
            capture_output=True, timeout=20
        )
        if result.returncode == 0:
            return download_ytdlp(url, outdir)
    except Exception:
        pass

    print("  ⚠️  yt-dlp can't handle this URL — falling back to aria2c direct")
    return download_direct(url, outdir)


# ── Upload ────────────────────────────────────────────────────────────────────

def get_upload_server() -> str:
    print("\n🔍  Getting upload server...")
    resp = api_get("upload/server", {})
    server = resp.get("result", "")
    if not server:
        raise RuntimeError(f"Could not get upload server: {resp}")
    print(f"  ✅  Server: {server}")
    return server


def upload_local(filepath: str, title: str, folder_id: str, public: int) -> dict:
    """POST file directly to VidNest upload server (default method)."""
    server = get_upload_server()

    print(f"\n{'─'*56}")
    print(f"  📤  Uploading: {filepath}  ({human_size(filepath)})")
    print(f"{'─'*56}\n")

    cmd = [
        "curl", "-X", "POST",
        "--max-time", "7200",
        "--retry", "3",
        "--retry-delay", "10",
        "--progress-bar",
        "-F", f"key={API_KEY}",
        "-F", f"file=@{filepath};filename={os.path.basename(filepath)}",
        "-F", f"file_public={public}",
    ]
    if title:
        cmd += ["-F", f"file_title={title}"]
    if folder_id:
        cmd += ["-F", f"fld_id={folder_id}"]
    cmd.append(server)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    raw = result.stdout.strip() or result.stderr.strip()
    print(f"\n  Raw response: {raw}")
    return json.loads(raw)


def upload_remote(url: str, folder_id: str, public: int) -> dict:
    """Submit a remote URL for VidNest to fetch (non-default)."""
    print("\n📡  Submitting remote URL to VidNest...")
    params = {
        "url":         url,
        "file_public": public,
    }
    if folder_id:
        params["fld_id"] = folder_id
    return api_get("upload/url", params)


# ── Main ──────────────────────────────────────────────────────────────────────

def banner(text: str):
    bar = "━" * 56
    print(f"\n{bar}\n  {text}\n{bar}")


def main():
    parser = argparse.ArgumentParser(
        description="Download any media and upload to VidNest"
    )
    parser.add_argument("url",               help="Media URL (m3u8, mp4, YouTube, etc.)")
    parser.add_argument("--title",  default="", help="File title on VidNest")
    parser.add_argument("--folder", default="", help="VidNest folder ID")
    parser.add_argument("--private",action="store_true", help="Make file private")
    parser.add_argument("--remote", action="store_true",
                        help="Skip download — submit URL for VidNest to fetch instead "
                             "(direct mp4/mkv only, NOT default)")
    args = parser.parse_args()

    public = 0 if args.private else 1

    banner("🎬  VidNest Uploader")
    print(f"  URL    : {args.url}")
    print(f"  Title  : {args.title or '(none)'}")
    print(f"  Folder : {args.folder or 'root'}")
    print(f"  Public : {'yes' if public else 'no'}")
    print(f"  Method : {'remote URL' if args.remote else 'local file upload (default)'}")

    # ── Remote upload (non-default) ──────────────────────────────────────────
    if args.remote:
        resp = upload_remote(args.url, args.folder, public)
        banner("📨  Remote Upload Response")
        print(json.dumps(resp, indent=2))
        filecode = resp.get("result", {}).get("filecode", "")
        if filecode:
            print(f"\n  🔗  Future URL: https://vidnest.io/{filecode}")
            print(f"  ⏳  VidNest will fetch the file asynchronously.")
        print(f"  👤  Profile : {PROFILE}\n")
        return

    # ── Default: download then local upload ──────────────────────────────────
    with tempfile.TemporaryDirectory(prefix="vidnest_") as tmpdir:
        # 1. Download
        banner("📥  Downloading Media")
        t0 = time.time()
        local_file = download(args.url, tmpdir)

        if not os.path.isfile(local_file):
            # Search for any file that appeared in tmpdir
            found = list(Path(tmpdir).glob("*"))
            if not found:
                print("❌  Download produced no file!", file=sys.stderr)
                sys.exit(1)
            local_file = str(found[0])

        elapsed = time.time() - t0
        size    = human_size(local_file)
        print(f"\n  ✅  Downloaded: {local_file}  [{size}]  in {elapsed:.1f}s")

        # 2. Upload
        banner("📤  Uploading to VidNest")
        resp = upload_local(local_file, args.title, args.folder, public)

    # 3. Result
    banner("✅  Upload Complete")
    print(json.dumps(resp, indent=2))

    files = resp.get("files", [])
    if files:
        filecode = files[0].get("filecode", "")
        print(f"\n  🎬  File code : {filecode}")
        print(f"  🔗  Watch URL : https://vidnest.io/{filecode}")
    print(f"  👤  Profile  : {PROFILE}\n")


if __name__ == "__main__":
    main()
