#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Single-process "Record + HLS" pipeline for Raspberry Pi (CM5/Raspbian)
- Captures frames via Picamera2
- Pushes frames into a GStreamer pipeline through AppSrc
- Splits (tee) into:
  (1) MP4 recording (faststart) and
  (2) HLS live playlist (m3u8 + .ts segments)

Requirements (apt):
  sudo apt update && sudo apt install -y \
    python3-gi gir1.2-gst-1.0 gstreamer1.0-tools \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
    python3-picamera2

Notes:
- Ensure 'hlssink' element is available (plugins-bad).
- Ensure 'x264enc' is available (plugins-ugly).
- Serve HLS directory via nginx or a static file server.
"""

import os
import time
import signal
import threading
from pathlib import Path
from datetime import datetime

from picamera2 import Picamera2
from gi.repository import Gst

# -------------- Configuration --------------
WIDTH = int(os.getenv("WIDTH", "1280"))
HEIGHT = int(os.getenv("HEIGHT", "720"))
FPS = int(os.getenv("FPS", "30"))
BITRATE_KBPS = int(os.getenv("BITRATE_KBPS", "4000"))  # video bitrate for H.264 (kbps)

# Recording target paths
BASE_DIR = Path(os.getenv("VIDEO_BASE", "/home/openiot/project/video")).expanduser()
BASE_DIR.mkdir(parents=True, exist_ok=True)
session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

# MP4 output (single file)
MP4_PATH = BASE_DIR / f"{session_id}.mp4"

# HLS output dir
HLS_DIR = BASE_DIR / f"hls_{session_id}"
HLS_DIR.mkdir(parents=True, exist_ok=True)
HLS_PLAYLIST = HLS_DIR / "stream.m3u8"
HLS_SEGMENT_PATTERN = str(HLS_DIR / "segment%05d.ts")

# HLS parameters
HLS_TARGET_DURATION = int(os.getenv("HLS_TARGET_DURATION", "2"))   # seconds/segment
HLS_PLAYLIST_LENGTH = int(os.getenv("HLS_PLAYLIST_LENGTH", "6"))   # segments in playlist
HLS_MAX_FILES = int(os.getenv("HLS_MAX_FILES", "12"))              # rolling window

# -------------- Globals --------------
stop_event = threading.Event()
producer_thread = None
pipeline = None
appsrc = None


def build_pipeline():
    """
    Build a single GStreamer pipeline:
      appsrc (RGB) -> videoconvert -> x264enc -> h264parse -> tee name=t
        t. -> queue -> mp4mux faststart=true -> filesink
        t. -> queue -> mpegtsmux -> hlssink (playlist + segments)
    """
    Gst.init(None)
    pipeline_str = f"""
        appsrc name=src is-live=true format=time do-timestamp=true
               caps=video/x-raw,format=RGB,width={WIDTH},height={HEIGHT},framerate={FPS}/1 !
        videoconvert !
        x264enc tune=zerolatency speed-preset=veryfast bitrate={BITRATE_KBPS} key-int-max={FPS*2} !
        h264parse config-interval=1 !
        tee name=t
            t. ! queue leaky=2 max-size-buffers=0 max-size-bytes=0 max-size-time=2000000000 !
                 mp4mux faststart=true !
                 filesink location="{MP4_PATH}" sync=false
            t. ! queue leaky=2 max-size-buffers=0 max-size-bytes=0 max-size-time=2000000000 !
                 mpegtsmux alignment=7 name=tsmux !
                 hlssink name=hsink max-files={HLS_MAX_FILES} playlist-length={HLS_PLAYLIST_LENGTH} target-duration={HLS_TARGET_DURATION}
                        location="{HLS_SEGMENT_PATTERN}" playlist-location="{HLS_PLAYLIST}"
    """
    pipe = Gst.parse_launch(pipeline_str)
    src = pipe.get_by_name("src")
    if src is None:
        raise RuntimeError("Failed to build pipeline: 'src' not found")
    return pipe, src


def start_camera():
    """Start Picamera2 in RGB format matching WIDTH/HEIGHT/FPS."""
    cam = Picamera2()
    preview_config = cam.create_preview_configuration(
        main={"size": (WIDTH, HEIGHT), "format": "RGB888"}
    )
    cam.configure(preview_config)
    cam.start()
    time.sleep(0.5)  # warm-up
    return cam


def push_frames_loop(cam):
    """Read frames from Picamera2 and push into appsrc at ~constant FPS."""
    global appsrc
    frame_interval = 1.0 / max(1, FPS)
    last_ts = time.time()

    while not stop_event.is_set():
        frame = cam.capture_array("main")  # numpy array (H, W, 3) RGB888
        data = frame.tobytes()

        # Create Gst.Buffer and push (timestamps auto via do-timestamp=true)
        buf = Gst.Buffer.new_allocate(None, len(data), None)
        buf.fill(0, data)

        try:
            appsrc.emit("push-buffer", buf)
        except Exception as e:
            print(f"[WARN] push-buffer failed: {e}")

        # Pace to target FPS
        elapsed = time.time() - last_ts
        to_sleep = frame_interval - elapsed
        if to_sleep > 0:
            time.sleep(to_sleep)
        last_ts = time.time()

    # Signal EOS
    try:
        appsrc.emit("end-of-stream")
    except Exception:
        pass


def run():
    global pipeline, appsrc, producer_thread

    pipeline, appsrc = build_pipeline()
    pipeline.set_state(Gst.State.PLAYING)

    cam = start_camera()
    producer_thread = threading.Thread(target=push_frames_loop, args=(cam,), daemon=True)
    producer_thread.start()

    print(f"[INFO] Recording MP4 to: {MP4_PATH}")
    print(f"[INFO] HLS playlist: {HLS_PLAYLIST}")
    print(f"[INFO] HLS segments: {HLS_SEGMENT_PATTERN}")
    print(f"[INFO] Serve HLS with (dev): python3 -m http.server -d {HLS_DIR} 8000")
    print(f"[INFO] Then open: http://<host>:8000/stream.m3u8")

    bus = pipeline.get_bus()

    try:
        while not stop_event.is_set():
            # Poll bus messages (handle ERROR/WARNING/EOS)
            msg = bus.timed_pop_filtered(
                100 * Gst.MSECOND,
                Gst.MessageType.ERROR | Gst.MessageType.WARNING | Gst.MessageType.EOS
            )
            if msg:
                if msg.type == Gst.MessageType.ERROR:
                    err, debug = msg.parse_error()
                    print(f"[GStreamer ERROR] {err} | {debug}")
                    break
                elif msg.type == Gst.MessageType.WARNING:
                    warn, debug = msg.parse_warning()
                    print(f"[GStreamer WARNING] {warn} | {debug}")
                elif msg.type == Gst.MessageType.EOS:
                    print("[GStreamer] End of stream")
                    break
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if producer_thread is not None:
            producer_thread.join(timeout=2)
        try:
            pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass


def _sigterm(_signo, _frame):
    stop_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _sigterm)
    signal.signal(signal.SIGTERM, _sigterm)
    run()
