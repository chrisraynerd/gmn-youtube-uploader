#!/usr/bin/env python3
import os
import sys
import json
import time
import glob
import shlex
import pathlib
import re
import datetime
import subprocess

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import google.oauth2.credentials

# Ensure UTF-8 stdout (Pi sometimes needs this)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------- CONFIG ----------
ARCHIVE_DIR   = "/home/rms/RMS_data/ArchivedFiles"   # <- now using the flat archive
CLIENT_SECRET = "/home/rms/youtube/client_secret.json"
TOKEN_PATH    = "/home/rms/youtube/token.json"
STATE_PATH    = "/home/rms/youtube/last_uploaded.json"
BACKGROUND_AUDIO = "/home/rms/youtube/background.mp3"  # set None to disable

PRIVACY_STATUS = "public"   # "public" | "unlisted" | "private"
CATEGORY_ID    = "28"       # Science & Technology
STATION_ID     = "UKXXXX"   # ADD YOIR STATION ID

TITLE_TEMPLATE = "Meteor Summary   {date}  {station}"
DESC_TEMPLATE  = (
    "Located in XXXXXXXXXX, the camera points XXX and uploads to the Global Meteor Network with station ID - {station}.\n"
    "Date: {date}\n"
    "#meteor #GMN #MeteorNetwork"
)
TAGS = ["meteor","GMN","RMS","meteor detection","night sky"]

# Image keywords (must appear in filename, in this order)
IMAGE_KEYWORDS = [
    "_meteors.jpg",
    "captured_stack.jpg",
    "report_astrometry.jpg",
    "fieldsums.png",
    "observing_periods.png",
    "radiants.png",
    "report_photometry.png",
    "photometry_variation.png",
    "calibration_variation.png",
]
# ----------------------------

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DRY_RUN = False  # set to False to actually upload

# ---------- Helpers for video & archive handling ----------
def ffprobe_duration(path: pathlib.Path) -> float:
    """Return duration in seconds using ffprobe; -1 if unavailable."""
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=nokey=1:noprint_wrappers=1 {shlex.quote(str(path))}'
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, text=True).strip()
        return float(out)
    except Exception:
        return -1.0

def probe_video_dims_fps(path):
    """Return (w,h,fps) for a video; fallback 1280x720@25 if probe fails."""
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-select_streams","v:0",
             "-show_entries","stream=width,height,r_frame_rate",
             "-of","default=nw=1:nk=1", str(path)],
            text=True
        ).strip().splitlines()
        w = int(out[0]); h = int(out[1]); fr = out[2]  # e.g. '25/1'
        num, den = fr.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 25.0
        if not (1 <= fps <= 120): fps = 25.0
        return w, h, fps
    except Exception:
        return 1280, 720, 25.0

_meteors_re = re.compile(r"(?:stack|stac)[_-]?(\d+)_meteors\.jpg$", re.IGNORECASE)

def has_meteors(night_dir: pathlib.Path):
    """Return (True, max_count) if any stack_X_meteors.jpg (X≥1) exists in the night dir tree; else (False, None)."""
    best = None
    for root, _, files in os.walk(night_dir):
        for name in files:
            if name.lower().endswith("_meteors.jpg"):
                m = _meteors_re.search(name)
                if m:
                    try:
                        x = int(m.group(1))
                        if x >= 1:
                            best = x if best is None else max(best, x)
                    except ValueError:
                        pass
    return (best is not None, best)

TIMELAPSE_GLOBS = [
    "images/timelapse*.mp4",
    "images/images*.mp4",
    "video/*timelapse*.mp4",
    "video/*.mp4",
    "*timelapse*.mp4",
    "*.mp4",
]

def find_timelapse(night_dir: pathlib.Path):
    """Return the most plausible timelapse for the given night folder."""
    candidates = []
    for pat in TIMELAPSE_GLOBS:
        candidates += [pathlib.Path(p) for p in glob.glob(str(night_dir / pat))]
    # de-dupe and keep existing
    seen, uniq = set(), []
    for v in candidates:
        if v.exists() and v not in seen:
            uniq.append(v); seen.add(v)
    if not uniq:
        return None
    # Prefer in images/, then by size
    uniq.sort(key=lambda p: (0 if "images" in p.as_posix() else 1, -p.stat().st_size))
    return uniq[0]

_date_in_name = re.compile(r"^[A-Z0-9]+_(\d{8})_")

def group_night_folders_by_date(archive_dir: pathlib.Path):
    """Return dict {'YYYYMMDD': [night_dir1, night_dir2, ...]} for flat ArchivedFiles."""
    groups = {}
    for p in archive_dir.iterdir():
        if not p.is_dir():
            continue
        m = _date_in_name.match(p.name)
        if not m:
            continue
        yyyymmdd = m.group(1)
        groups.setdefault(yyyymmdd, []).append(p)
    return groups

def pick_best_folder_for_date(archive_dir: pathlib.Path, yyyymmdd: str, require_meteors=True):
    """Return (night_folder, video_path, meteors_count or None) for a given date."""
    groups = group_night_folders_by_date(archive_dir)
    candidates = groups.get(yyyymmdd, [])
    best = None  # (duration, size, mtime, folder, video, meteors_count)
    for d in candidates:
        v = find_timelapse(d)
        if not v:
            continue
        ok, mc = has_meteors(d)
        if require_meteors and not ok:
            continue
        dur = ffprobe_duration(v)
        size = v.stat().st_size
        mtime = v.stat().st_mtime
        key = (dur if dur > 0 else 0, size, mtime)
        if best is None or key > best[:3]:
            best = (key[0], key[1], key[2], d, v, mc)
    if best:
        return best[3], best[4], best[5]
    return None, None, None

def pick_latest_night(archive_dir: pathlib.Path, require_meteors=True):
    """Pick the most recent date that has a valid folder/video (and meteors if required)."""
    groups = group_night_folders_by_date(archive_dir)
    if not groups:
        return None, None, None, None  # (yyyymmdd, folder, video, meteors_count)
    for yyyymmdd in sorted(groups.keys(), reverse=True):
        folder, video, mc = pick_best_folder_for_date(archive_dir, yyyymmdd, require_meteors=require_meteors)
        if folder and video:
            return yyyymmdd, folder, video, mc
    return None, None, None, None

# ---------- Auth & state ----------
def get_credentials():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = google.oauth2.credentials.Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds

def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)

# ---------- Video building ----------
def build_slideshow(images, output_path, target_w=1280, target_h=720, target_fps=25.0):
    """Create a slideshow from stills (3s each), scaled/letterboxed to target size/fps."""
    if not images:
        return None
    inputs, filters = [], []
    for i, img in enumerate(images):
        inputs += ["-loop","1","-t","3","-i", str(img)]
        filters.append(
            f"[{i}:v]fps={target_fps},scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]"
        )
    n = len(images)
    filter_complex = ";".join(filters) + ";" + "".join(f"[v{i}]" for i in range(n)) \
                     + f"concat=n={n}:v=1:a=0,format=yuv420p[vout]"
    cmd = ["ffmpeg","-y"] + inputs + ["-filter_complex", filter_complex,
           "-map","[vout]","-r", f"{target_fps}", "-c:v","libx264","-pix_fmt","yuv420p", str(output_path)]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return output_path

def concat_videos(main_video, extra_video, output_path, target_fps=25.0, bg_audio=None):
    """Concat main+slideshow, then optionally add background audio."""
    temp_out = str(output_path) + ".noaudio.mp4"
    cmd = [
        "ffmpeg","-y",
        "-i", str(main_video),
        "-i", str(extra_video),
        "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
        "-map","[v]","-r", f"{target_fps}",
        "-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart",
        temp_out
    ]
    subprocess.run(cmd, check=True)

    if bg_audio and os.path.exists(bg_audio):
        cmd = [
            "ffmpeg","-y",
            "-i", temp_out,
            "-stream_loop","-1","-i", bg_audio,
            "-filter_complex","[1:a]volume=0.6[aud]",
            "-shortest",
            "-map","0:v:0","-map","[aud]",
            "-c:v","copy","-c:a","aac","-b:a","192k",
            str(output_path)
        ]
        subprocess.run(cmd, check=True)
        os.remove(temp_out)
    else:
        os.rename(temp_out, output_path)

    return output_path

# ---------- YouTube upload ----------
def upload_video(youtube, filepath: pathlib.Path, title: str, desc: str):
    media = MediaFileUpload(str(filepath), mimetype="video/mp4", resumable=True, chunksize=8*1024*1024)
    body = {
        "snippet": {
            "title": title,
            "description": desc,
            "tags": TAGS,
            "categoryId": CATEGORY_ID,
        },
        "status": {
            "privacyStatus": PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False
        },
    }
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress()*100)}%")
    print("Uploaded video id:", response["id"])
    return response["id"]

# ---------- Main ----------
def main():
    archive = pathlib.Path(ARCHIVE_DIR)

    # 1) Choose latest night with >=1 meteor; handle duplicate night folders by picking the longest timelapse
    yyyymmdd, night_folder, latest_video, meteor_count = pick_latest_night(archive, require_meteors=True)
    if not latest_video:
        print("No suitable night found (no timelapse or no meteors).")
        sys.exit(1)

    # 2) Duplicate guard (skip if we already uploaded this exact file)
    state = load_state()
    if state.get("last_filepath") and os.path.exists(state["last_filepath"]):
        try:
            if os.path.samefile(state["last_filepath"], latest_video):
                print("Latest file already uploaded. Exiting.")
                return
        except Exception:
            pass

    # 3) Gather images from the chosen night folder in your preferred order
    folder = latest_video.parent
    images = []
    for kw in IMAGE_KEYWORDS:
        try:
            match = next((f for f in folder.iterdir() if kw in f.name), None)
        except FileNotFoundError:
            match = None
        if match:
            images.append(match)

    # 4) Ensure the video is not still being written
    size1 = latest_video.stat().st_size
    time.sleep(3)
    size2 = latest_video.stat().st_size
    if size2 != size1:
        print("Newest timelapse is still growing. Try again later.")
        sys.exit(2)

    # 5) Build slideshow and concat
    tw, th, tfps = probe_video_dims_fps(latest_video)
    final_video = latest_video
    if images:
        slideshow = build_slideshow(images, folder / "images.mp4",
                                    target_w=tw, target_h=th, target_fps=tfps)
        final_video = concat_videos(latest_video, slideshow, folder / "final_with_images.mp4",
                                    target_fps=tfps, bg_audio=BACKGROUND_AUDIO)

    # 6) Prepare metadata (use date from folder name yyyymmdd for reliability)
    up_date = datetime.datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%d-%m-%Y")
    meteors = meteor_count  # may be None if not found, but we required ≥1 so usually an int

    if meteors is not None:
        title = f"Meteor Camera on {up_date} with {meteors} Meteors Detected  {STATION_ID}"
    else:
        title = f"Meteor Camera on {up_date}  {STATION_ID}"

    desc = DESC_TEMPLATE.format(date=up_date, station=STATION_ID, filename=final_video.name)
    if meteors is not None:
        desc += f"\nMeteors detected: {meteors}"

    # 7) Upload (or dry-run)
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    if DRY_RUN:
        print("WOULD UPLOAD FILE:", final_video)
        print("WOULD UPLOAD WITH TITLE:", title)
        print("DESC:\n", desc)
        return

    vid = upload_video(youtube, final_video, title, desc)
    state["last_filepath"] = str(latest_video.resolve())
    state["last_video_id"] = vid
    save_state(state)
    print("Done.")

if __name__ == "__main__":
    main()
