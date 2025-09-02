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

# GStreamer 버전을 먼저 지정
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

from picamera2 import Picamera2

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
    
    # 더 안정적인 파이프라인 구조
    pipeline_str = f"""
        appsrc name=src is-live=true format=time do-timestamp=true
               caps=video/x-raw,format=RGB,width={WIDTH},height={HEIGHT},framerate={FPS}/1 ! 
        videoconvert ! 
        video/x-raw,format=I420 ! 
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
    
    print(f"[DEBUG] Pipeline: {pipeline_str}")
    
    pipe = Gst.parse_launch(pipeline_str)
    src = pipe.get_by_name("src")
    if src is None:
        raise RuntimeError("Failed to build pipeline: 'src' not found")
    
    # 파이프라인 상태 변경을 위한 콜백 설정
    bus = pipe.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_bus_message, pipe)
    
    return pipe, src


def on_bus_message(bus, message, pipeline):
    """GStreamer 버스 메시지 처리"""
    msg_type = message.type
    
    if msg_type == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        print(f"[GStreamer ERROR] {err} | {debug}")
        stop_event.set()
    elif msg_type == Gst.MessageType.WARNING:
        warn, debug = message.parse_warning()
        print(f"[GStreamer WARNING] {warn} | {debug}")
    elif msg_type == Gst.MessageType.EOS:
        print("[GStreamer] End of stream")
        stop_event.set()
    elif msg_type == Gst.MessageType.STATE_CHANGED:
        old_state, new_state, pending_state = message.parse_state_changed()
        if message.src == pipeline:
            print(f"[DEBUG] Pipeline state changed: {old_state.value_name} -> {new_state.value_name}")


def start_camera():
    """Start Picamera2 in RGB format matching WIDTH/HEIGHT/FPS."""
    cam = Picamera2()
    preview_config = cam.create_preview_configuration(
        main={"size": (WIDTH, HEIGHT), "format": "RGB888"}
    )
    cam.configure(preview_config)
    cam.start()
    time.sleep(1.0)  # warm-up 시간 증가
    return cam


def push_frames_loop(cam):
    """Read frames from Picamera2 and push into appsrc at ~constant FPS."""
    global appsrc
    frame_interval = 1.0 / max(1, FPS)
    last_ts = time.time()
    frame_count = 0

    while not stop_event.is_set():
        try:
            frame = cam.capture_array("main")  # numpy array (H, W, 3) RGB888
            data = frame.tobytes()

            # Create Gst.Buffer and push (timestamps auto via do-timestamp=true)
            buf = Gst.Buffer.new_allocate(None, len(data), None)
            buf.fill(0, data)

            # 프레임 카운터 추가
            frame_count += 1
            if frame_count % FPS == 0:
                print(f"[DEBUG] Pushed {frame_count} frames")

            try:
                appsrc.emit("push-buffer", buf)
            except Exception as e:
                print(f"[WARN] push-buffer failed: {e}")
                break

            # Pace to target FPS
            elapsed = time.time() - last_ts
            to_sleep = frame_interval - elapsed
            if to_sleep > 0:
                time.sleep(to_sleep)
            last_ts = time.time()
            
        except Exception as e:
            print(f"[ERROR] Frame capture failed: {e}")
            break

    # Signal EOS
    try:
        print("[DEBUG] Signaling end-of-stream")
        appsrc.emit("end-of-stream")
    except Exception as e:
        print(f"[WARN] EOS signal failed: {e}")


def run():
    global pipeline, appsrc, producer_thread

    try:
        pipeline, appsrc = build_pipeline()
        
        # 파이프라인 상태를 PLAYING으로 설정
        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to set pipeline to PLAYING state")
        
        # 파이프라인이 PLAYING 상태가 될 때까지 대기
        ret = pipeline.get_state(Gst.CLOCK_TIME_NONE)
        if ret[0] != Gst.StateChangeReturn.SUCCESS:
            raise RuntimeError(f"Pipeline failed to reach PLAYING state: {ret[0]}")

        cam = start_camera()
        producer_thread = threading.Thread(target=push_frames_loop, args=(cam,), daemon=True)
        producer_thread.start()

        print(f"[INFO] Recording MP4 to: {MP4_PATH}")
        print(f"[INFO] HLS playlist: {HLS_PLAYLIST}")
        print(f"[INFO] HLS segments: {HLS_SEGMENT_PATTERN}")
        print(f"[INFO] Serve HLS with (dev): python3 -m http.server -d {HLS_DIR} 8000")
        print(f"[INFO] Then open: http://<host>:8000/stream.m3u8")

        # 메인 루프 - stop_event가 설정될 때까지 대기
        while not stop_event.is_set():
            time.sleep(0.1)
            
    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}")
    finally:
        cleanup()


def cleanup():
    """정리 작업"""
    global pipeline, producer_thread
    
    print("[INFO] Cleaning up...")
    stop_event.set()
    
    if producer_thread is not None:
        producer_thread.join(timeout=2)
        producer_thread = None
    
    if pipeline is not None:
        try:
            pipeline.set_state(Gst.State.NULL)
            pipeline = None
        except Exception as e:
            print(f"[WARN] Pipeline cleanup failed: {e}")


def _sigterm(_signo, _frame):
    print(f"[INFO] Received signal {_signo}")
    stop_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _sigterm)
    signal.signal(signal.SIGTERM, _sigterm)
    run()
