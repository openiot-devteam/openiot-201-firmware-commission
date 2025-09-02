import paho.mqtt.client as mqtt
import json
import time
import threading
import socket
import ssl
import cv2
import numpy as np
import os
import re
import psutil
import subprocess
import shutil
import tempfile
from pyzbar import pyzbar
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
 
# import RPi.GPIO as GPIO
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstRtspServer', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstRtspServer, GObject, GstApp
from gpiozero import LED, Button  # type: ignore
from picamera2 import Picamera2, Preview  # type: ignore
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# --- Postprocess settings ---
postprocess_after_capture = True  # 캡처 종료 후 후처리 모드로 최종 영상 생성

# 전역 설정 변수들
current_gamma = 1.0
current_mode = 'rgb'  # 'rgb' 또는 'gray'
current_wb = 'none'  # 'none', 'grayworld', 'manual'
current_fps = 30
current_bitrate = 2000000
current_roi = (0, 0, 1920, 1080)  # (x, y, width, height)
current_frame = "1920x1080"

# (이전 호환) 폴리곤 ROI 저장값을 바운딩 박스로 이행하기 위한 변수
current_roi_poly = None
# 감마 LUT 캐시
gamma_lut = None
gamma_lut_for = None  # LUT가 반영된 감마 값 캐시
LED_PIN = LED(18)  # 사용할 GPIO 핀 번호
camera_thread = None
# 스케줄러 스레드 포인터
scheduler_thread_schedule = None
scheduler_thread_motion = None

# 모션 감지 세그먼트 전역 수집 (OF 모드에서 세그먼트 닫힐 때 경로 추가)
global_detection_segments = []
global_detection_segments_lock = threading.Lock()
# 병합 작업 중 파일 저장 억제 플래그
merge_in_progress = False
# 예약 병합 시 세션 내부 병합을 건너뛰기 위한 플래그
scheduled_merge_initiated = False
# 스케줄 캡처 활성 플래그(예약 시간에만 True)
schedule_capture_active = False

# 시간 설정(기본값: 테스트용 11:41 / 13:14)
SCHEDULE_DURATION_SEC = 60
SCHEDULE_MODE_HOUR = 11
SCHEDULE_MODE_MINUTE = 41
SCHEDULE_DAYS = [0, 1, 2, 3, 4, 5, 6]  # 0=Mon ... 6=Sun (datetime.weekday 기준)
MOTION_MODE_HOUR = 13
MOTION_MODE_MINUTE = 14
MOTION_DAYS = [0, 1, 2, 3, 4, 5, 6]

# 세션 중 파라미터 변경 타임라인 기록용 전역
session_active = False
session_epoch_time = 0.0  # time.time() 기준 세션 시작 시각
session_param_events = []  # [{"t_ns": int, "gamma": float, "wb": str, "mode": str}]

# RTSP 서버 전역 싱글톤
rtsp_server = None
rtsp_mounts = None
rtsp_factory = None
rtsp_loop = None
rtsp_appsrc_ref = {"appsrc": None}
rtsp_scale_caps_element = None
rtsp_vb_element = None  # RTSP용 videobalance 참조
gamma_element = None
rtsp_x264_element = None  # RTSP용 x264enc 참조
rtsp_path = "/test"
restart_lock = threading.Lock()
rtsp_last_stream_log_time = 0.0
# 스케줄러 깨우기 이벤트 (MQTT 시간 갱신 시 즉시 재계산)
schedule_wake_event = threading.Event()
schedule_update_event = threading.Event()
motion_wake_event = threading.Event()
motion_update_event = threading.Event()

# --- HLS/Recording/QR 전역 ---
# HLS 스트리밍 설정
hls_enabled = True
hls_pipeline = None
hls_appsrc = None
hls_dir = os.path.abspath(os.path.join(os.getcwd(), 'hls'))
hls_httpd_server = None
hls_httpd_thread = None
hls_http_port = 8090

# 수동 녹화 (main.py 호환)
manual_recording = False
manual_video_writer = None
manual_recording_start_time = None
manual_recording_filename = None

# QR 인식 상태 (main.py 호환)
camera_frame = None
last_qr_data = None
qr_detection_time = 0
cooldown_period = 3

def reset_rtsp_server():
    global rtsp_server, rtsp_mounts, rtsp_factory, rtsp_loop, rtsp_appsrc_ref, rtsp_vb_element, gamma_element
    try:
        if rtsp_loop is not None:
            try:
                rtsp_loop.quit()
            except Exception:
                pass
    except Exception:
        pass
    rtsp_server = None
    rtsp_mounts = None
    rtsp_factory = None
    rtsp_loop = None
    rtsp_appsrc_ref = {"appsrc": None}
    rtsp_vb_element = None
    gamma_element = None
    globals()['rtsp_x264_element'] = None

# Optical Flow 전역 토글 (MQTT로 제어)
of_enabled = True              # True: 옵티컬 플로우 계산/모션 판정 활성화
of_overlay_enabled = False     # True: RTSP에 옵티컬 플로우 벡터 오버레이 출력

# 세션 종료 제어 이벤트 (MQTT 'camera_off')
camera_stop_event = threading.Event()

# --- Persistence: 마지막 모드(JSON)에 저장/로드 ---
STATE_FILE = "/home/openiot/project/openiot-201-firmware-camera/raspberrypi_cam/camera_state.json"

# --- System Information Functions ---
def get_cpu_temp():
    """라즈베리파이 CPU 온도 가져오기"""
    global temp
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = int(f.readline()) / 1000.0
        return temp
    except FileNotFoundError:
        return None

def get_system_usage():
    """CPU 사용량, RAM 사용량 가져오기"""
    global cpu_usage, ram_usage
    cpu_usage = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    ram_usage = memory.percent
    return cpu_usage, ram_usage

# --- QR helpers (main.py 호환) ---
def get_client_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_mac_address():
    try:
        if os.name == 'posix':
            try:
                result = subprocess.run(['ip', 'link', 'show'], capture_output=True, text=True)
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if 'link/ether' in line:
                            mac = line.split('link/ether')[1].strip().split()[0]
                            return mac
            except Exception:
                pass
            try:
                for interface in os.listdir('/sys/class/net'):
                    if interface != 'lo':
                        mac_path = f'/sys/class/net/{interface}/address'
                        if os.path.exists(mac_path):
                            with open(mac_path, 'r') as f:
                                mac = f.read().strip()
                                if mac and mac != '00:00:00:00:00:00':
                                    return mac
            except Exception:
                pass
        elif os.name == 'nt':
            try:
                result = subprocess.run(['getmac', '/fo', 'csv'], capture_output=True, text=True)
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if 'Physical Address' not in line and line.strip():
                            parts = line.split(',')
                            if len(parts) >= 2:
                                mac = parts[1].strip().strip('"')
                                if mac and mac != '00-00-00-00-00-00':
                                    return mac.replace('-', ':')
            except Exception:
                pass
        import uuid as _uuid
        mac = ':'.join(['{:02x}'.format((_uuid.getnode() >> elements) & 0xff) for elements in range(0,2*6,2)][::-1])
        return mac
    except Exception:
        return "00:00:00:00:00:00"

def enhance_image_for_qr(frame: np.ndarray):
    if frame is None:
        return None
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    except Exception:
        return frame
    denoised = cv2.fastNlMeansDenoising(gray)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(denoised)
    kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
    sharpened = cv2.filter2D(enhanced, -1, kernel)
    return sharpened

def detect_qr_codes_enhanced(frame: np.ndarray):
    if frame is None:
        return []
    decoded_original = pyzbar.decode(frame)
    enhanced = enhance_image_for_qr(frame)
    decoded_enhanced = pyzbar.decode(enhanced) if enhanced is not None else []
    all_results = []
    for obj in decoded_original:
        all_results.append({ 'data': obj.data.decode('utf-8'), 'rect': obj.rect, 'polygon': obj.polygon, 'quality': 'original' })
    for obj in decoded_enhanced:
        data = obj.data.decode('utf-8')
        if not any(r['data'] == data for r in all_results):
            all_results.append({ 'data': data, 'rect': obj.rect, 'polygon': obj.polygon, 'quality': 'enhanced' })
    return all_results

def parse_server_info(qr_data: str):
    try:
        return json.loads(qr_data)
    except json.JSONDecodeError:
        parts = qr_data.split(':')
        if len(parts) >= 3:
            return { 'ip': parts[0], 'port': parts[1], 'key': parts[2] }
        return None

def send_commission_request(server_info: dict):
    try:
        client_ip = get_client_ip()
        server_url = f"http://{server_info['ip']}:{server_info['port']}/commission"
        req = { 'client_ip': client_ip }
        import requests as _requests
        resp = _requests.post(server_url, json=req, headers={'Content-Type':'application/json'}, timeout=10)
        print(f"[QR] commission 응답: {resp.status_code} {resp.text[:200]}")
        return resp.status_code == 200
    except Exception as e:
        print(f"[QR] commission 실패: {e}")
        return False

def send_pairing_request(endpoint_url: str):
    try:
        client_ip = get_client_ip()
        mac_address = get_mac_address()
        req = { 'ip': client_ip, 'mac_address': mac_address }
        import requests as _requests
        resp = _requests.post(endpoint_url, json=req, headers={'Content-Type':'application/json'}, timeout=10)
        print(f"[QR] pairing 응답: {resp.status_code} {resp.text[:200]}")
        return resp.status_code == 200
    except Exception as e:
        print(f"[QR] pairing 실패: {e}")
        return False
# --- Manual Recording (main.py 호환) ---
def start_recording_manual(frame: np.ndarray):
    global manual_recording, manual_video_writer, manual_recording_start_time, manual_recording_filename
    if manual_recording:
        return False, "이미 녹화 중입니다."
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        manual_recording_filename = f"recording_{timestamp}.mp4"
        h, w = frame.shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        manual_video_writer = cv2.VideoWriter(manual_recording_filename, fourcc, float(current_fps), (w, h))
        if not manual_video_writer.isOpened():
            manual_video_writer = None
            return False, "비디오 writer를 초기화할 수 없습니다."
        manual_recording = True
        manual_recording_start_time = time.time()
        print(f"✅ 수동 녹화 시작: {manual_recording_filename}")
        return True, f"녹화가 시작되었습니다: {manual_recording_filename}"
    except Exception as e:
        print(f"❌ 수동 녹화 시작 실패: {e}")
        return False, f"녹화 시작 실패: {e}"

def stop_recording_manual():
    global manual_recording, manual_video_writer, manual_recording_start_time, manual_recording_filename
    if not manual_recording:
        return False, "녹화 중이 아닙니다."
    try:
        manual_recording = False
        if manual_video_writer:
            manual_video_writer.release()
            manual_video_writer = None
        duration = time.time() - manual_recording_start_time if manual_recording_start_time else 0
        manual_recording_start_time = None
        if manual_recording_filename and os.path.exists(manual_recording_filename):
            size_mb = os.path.getsize(manual_recording_filename) / (1024 * 1024)
            print(f"✅ 수동 녹화 완료: {manual_recording_filename} ({duration:.1f}s, {size_mb:.2f}MB)")
            return True, f"녹화 완료: {manual_recording_filename} ({duration:.1f}초, {size_mb:.2f}MB)"
        return True, "녹화가 중지되었습니다."
    except Exception as e:
        print(f"❌ 수동 녹화 중지 실패: {e}")
        return False, f"녹화 중지 실패: {e}"

def write_frame_to_manual_recording(frame: np.ndarray):
    global manual_recording, manual_video_writer
    if manual_recording and manual_video_writer is not None and manual_video_writer.isOpened():
        try:
            manual_video_writer.write(frame)
        except Exception as e:
            print(f"❌ 프레임 녹화 실패: {e}")
            stop_recording_manual()

def load_last_mode_from_disk() -> None:
    """마지막 설정(of_enabled, color_mode, wb, frame, roi, schedule/motion time)을 디스크에서 로드하여 적용."""
    global of_enabled, current_mode, current_wb, current_frame, current_roi, current_gamma, current_bitrate
    global SCHEDULE_MODE_HOUR, SCHEDULE_MODE_MINUTE, SCHEDULE_DURATION_SEC, SCHEDULE_DAYS
    global MOTION_MODE_HOUR, MOTION_MODE_MINUTE, MOTION_DAYS
    try:
        if os.path.isfile(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                if 'camera_mode' in data:
                    # 호환: mode 문자열이 있으면 변환
                    val = str(data['camera_mode']).lower()
                    of_enabled = (val in ['motion', 'opt_flow', 'on', 'true', '1'])
                # 색상 모드(gray/rgb)
                if isinstance(data.get('color_mode'), str):
                    cm = str(data.get('color_mode', '')).lower()
                    if cm in ['gray', 'rgb']:
                        current_mode = cm
                # 화이트 밸런스(auto/none)
                if isinstance(data.get('wb'), str):
                    wbv = str(data.get('wb', '')).lower()
                    if wbv in ['auto', 'none']:
                        current_wb = wbv
                # 프레임 해상도 ("WxH")
                if isinstance(data.get('frame'), str):
                    fv = str(data.get('frame', '')).lower().replace(' ', '')
                    if 'x' in fv:
                        parts = fv.split('x', 1)
                        try:
                            _w = int(parts[0]); _h = int(parts[1])
                            if _w > 0 and _h > 0:
                                current_frame = f"{_w}x{_h}"
                        except Exception:
                            pass
                # 감마
                try:
                    if 'gamma' in data:
                        gval = float(data.get('gamma'))
                        if gval > 0:
                            current_gamma = gval
                except Exception:
                    pass
                # 비트레이트 (bps)
                try:
                    if 'bitrate' in data:
                        br = int(data.get('bitrate'))
                        if br > 0:
                            current_bitrate = br
                            
                except Exception:
                    pass
                # ROI 사각형 우선 적용
                roi_rect_val = data.get('roi') or data.get('roi_rect')
                try:
                    if isinstance(roi_rect_val, (list, tuple)) and len(roi_rect_val) == 4:
                        rx, ry, rw, rh = [int(v) for v in roi_rect_val]
                        current_roi = (rx, ry, rw, rh)
                except Exception:
                    pass
                # 스케줄/모션 시간 로드 ("HH:MM")
                try:
                    sched = data.get('schedule_time')
                    if isinstance(sched, str):
                        t = sched.strip().lower().replace(':', '')
                        if len(t) == 4 and t.isdigit():
                            SCHEDULE_MODE_HOUR = int(t[:2]) % 24
                            SCHEDULE_MODE_MINUTE = int(t[2:]) % 60
                except Exception:
                    pass
                # 스케줄 요일 로드
                try:
                    days = data.get('schedule_days')
                    if days is not None:
                        parsed = parse_schedule_days(days)
                        if parsed:
                            SCHEDULE_DAYS = parsed
                except Exception:
                    pass
                # 스케줄 동작 시간(초) 로드
                try:
                    dur = data.get('schedule_duration') or data.get('schedule_duration_sec')
                    if dur is not None:
                        secs = parse_duration_seconds(dur)
                        if isinstance(secs, int) and secs > 0:
                            SCHEDULE_DURATION_SEC = secs
                except Exception:
                    pass
                try:
                    mot = data.get('motion_time')
                    if isinstance(mot, str):
                        t = mot.strip().lower().replace(':', '')
                        if len(t) == 4 and t.isdigit():
                            MOTION_MODE_HOUR = int(t[:2]) % 24
                            MOTION_MODE_MINUTE = int(t[2:]) % 60
                except Exception:
                    pass
                # 모션 요일 로드
                try:
                    mdays = data.get('motion_days')
                    if mdays is not None:
                        parsed_md = parse_schedule_days(mdays)
                        if parsed_md:
                            MOTION_DAYS = parsed_md
                except Exception:
                    pass
            try:
                sched_str = f"{SCHEDULE_MODE_HOUR:02d}:{SCHEDULE_MODE_MINUTE:02d}"
                mot_str = f"{MOTION_MODE_HOUR:02d}:{MOTION_MODE_MINUTE:02d}"
            except Exception:
                sched_str = "--:--"; mot_str = "--:--"
            try:
                day_map = ['mon','tue','wed','thu','fri','sat','sun']
                days_str = ','.join([day_map[d] for d in SCHEDULE_DAYS if isinstance(d, int) and 0 <= d <= 6])
            except Exception:
                days_str = ''
            try:
                day_map2 = ['mon','tue','wed','thu','fri','sat','sun']
                mdays_str = ','.join([day_map2[d] for d in MOTION_DAYS if isinstance(d, int) and 0 <= d <= 6])
            except Exception:
                mdays_str = ''
            print(f"[STATE] 로드 완료: color_mode={current_mode}, wb={current_wb}, frame={current_frame}, gamma={current_gamma}, roi={current_roi}, schedule_time={sched_str}, schedule_days=[{days_str}], schedule_duration={SCHEDULE_DURATION_SEC}s, motion_time={mot_str}, motion_days=[{mdays_str}]")
    except Exception as e:
        print(f"[STATE] 로드 실패: {e}")

def save_last_mode_to_disk() -> None:
    """현재 설정(of_enabled, color_mode, wb, frame, roi, schedule/motion time)을 디스크에 저장."""
    try:
        day_map = ['mon','tue','wed','thu','fri','sat','sun']
        days_str_list = [day_map[d] for d in SCHEDULE_DAYS if isinstance(d, int) and 0 <= d <= 6]
        mdays_str_list = [day_map[d] for d in MOTION_DAYS if isinstance(d, int) and 0 <= d <= 6]
        payload = {
            'camera_mode': 'motion' if of_enabled else 'schedule',
            'color_mode': str(current_mode),
            'wb': str(current_wb),
            'frame': str(current_frame),
            'gamma': float(current_gamma),
            'bitrate': int(current_bitrate),
            'roi': [int(v) for v in current_roi] if isinstance(current_roi, (list, tuple)) and len(current_roi) == 4 else None,
            'schedule_time': f"{SCHEDULE_MODE_HOUR:02d}:{SCHEDULE_MODE_MINUTE:02d}",
            'schedule_days': days_str_list,
            'schedule_duration_sec': int(SCHEDULE_DURATION_SEC),
            'motion_time': f"{MOTION_MODE_HOUR:02d}:{MOTION_MODE_MINUTE:02d}",
            'motion_days': mdays_str_list,
            'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S')
        }
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[STATE] 저장 완료: {payload}")
    except Exception as e:
        print(f"[STATE] 저장 실패: {e}")

# --- HLS helpers ---
def ensure_hls_dir():
    try:
        os.makedirs(hls_dir, exist_ok=True)
    except Exception:
        pass

def start_hls_http_server(port: int = None):
    global hls_httpd_server, hls_httpd_thread
    if port is None:
        port = hls_http_port
    if hls_httpd_server is not None:
        return
    ensure_hls_dir()
    # 기본 index.html 자동 생성
    try:
        index_path = os.path.join(hls_dir, 'index.html')
        if (not os.path.exists(index_path)):
            html = """<!DOCTYPE html>
<html lang=\"ko\">
<head>
  <meta charset=\"UTF-8\"/>
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>
  <title>HLS 스트리밍</title>
  <style>
    body{margin:0;padding:20px;font-family:system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial}
    .wrap{max-width:960px;margin:0 auto}
    h1{margin:0 0 16px}
    video{width:100%;max-height:70vh;background:#000;border-radius:8px}
    .hint{margin-top:12px;color:#555}
  </style>
  <script src=\"https://cdn.jsdelivr.net/npm/hls.js@latest\"></script>
  </head>
<body>
  <div class=\"wrap\">
    <h1>HLS 스트리밍</h1>
    <video id=\"video\" controls autoplay muted playsinline></video>
    <div class=\"hint\">재생 URL: index.m3u8 (이 페이지는 mqtt_camera가 자동 생성했습니다)</div>
  </div>
  <script>
    const video = document.getElementById('video');
    const src = 'index.m3u8';
    if (Hls.isSupported()) {
      const hls = new Hls({maxBufferLength:10});
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, function(){ video.play(); });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = src;
      video.addEventListener('loadedmetadata', function() { video.play(); });
    } else {
      document.body.insertAdjacentHTML('beforeend', '<p>HLS를 재생할 수 없는 브라우저입니다.</p>');
    }
  </script>
</body>
</html>"""
            with open(index_path, 'w', encoding='utf-8') as f:
                f.write(html)
    except Exception:
        pass
    class HLSHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=hls_dir, **kwargs)
    try:
        hls_httpd_server = ThreadingHTTPServer(("0.0.0.0", int(port)), HLSHandler)
        hls_httpd_thread = threading.Thread(target=hls_httpd_server.serve_forever, daemon=True)
        hls_httpd_thread.start()
        print(f"[HLS] HTTP 서버 시작: http://0.0.0.0:{port}/index.m3u8")
    except Exception as e:
        print(f"[HLS] HTTP 서버 시작 실패: {e}")
        hls_httpd_server = None
        hls_httpd_thread = None

def stop_hls_http_server():
    global hls_httpd_server, hls_httpd_thread
    try:
        if hls_httpd_server is not None:
            hls_httpd_server.shutdown()
            hls_httpd_server.server_close()
    except Exception:
        pass
    hls_httpd_server = None
    hls_httpd_thread = None

def start_hls_pipeline(width: int, height: int, framerate: int, bitrate_kbps: int):
    global hls_pipeline, hls_appsrc
    if not hls_enabled:
        return
    if hls_pipeline is not None:
        return
    ensure_hls_dir()
    Gst.init(None)
    launch = (
        f"appsrc name=hls_src is-live=true format=time do-timestamp=true block=true "
        f"caps=video/x-raw,format=RGB,width={width},height={height},framerate={framerate}/1 ! "
        f"videoconvert ! video/x-raw,format=I420 ! "
        f"x264enc tune=zerolatency key-int-max=60 bitrate={bitrate_kbps} ! h264parse ! mpegtsmux ! "
        f"hlssink name=hlsink target-duration=2 max-files=10 playlist-location={os.path.join(hls_dir, 'index.m3u8')} location={os.path.join(hls_dir, 'segment_%05d.ts')}"
    )
    try:
        hls_pipeline = Gst.parse_launch(launch)
        hls_appsrc = hls_pipeline.get_by_name('hls_src')
        hls_pipeline.set_state(Gst.State.PLAYING)
        print("[HLS] 파이프라인 시작")
    except Exception as e:
        print(f"[HLS] 파이프라인 시작 실패: {e}")
        hls_pipeline = None
        hls_appsrc = None

def stop_hls_pipeline():
    global hls_pipeline, hls_appsrc
    try:
        if hls_pipeline is not None:
            hls_pipeline.set_state(Gst.State.NULL)
    except Exception:
        pass
    hls_pipeline = None
    hls_appsrc = None

# --- Schedule Helpers ---
def parse_schedule_days(days_value):
    """다양한 형식의 요일 입력을 [0..6] 리스트로 파싱. 0=Mon..6=Sun"""
    try:
        if days_value is None:
            return None
        name_to_idx = {
            'mon': 0, 'monday': 0, '월': 0, '월요일': 0,
            'tue': 1, 'tuesday': 1, '화': 1, '화요일': 1,
            'wed': 2, 'wednesday': 2, '수': 2, '수요일': 2,
            'thu': 3, 'thursday': 3, '목': 3, '목요일': 3,
            'fri': 4, 'friday': 4, '금': 4, '금요일': 4,
            'sat': 5, 'saturday': 5, '토': 5, '토요일': 5,
            'sun': 6, 'sunday': 6, '일': 6, '일요일': 6,
        }
        if isinstance(days_value, str):
            s = days_value.strip().lower()
            if s in ['all', 'everyday', 'every_day', 'daily', '매일']:
                return [0,1,2,3,4,5,6]
            if s in ['weekday', 'weekdays', '주중']:
                return [0,1,2,3,4]
            if s in ['weekend', 'weekends', '주말']:
                return [5,6]
            parts = [p.strip() for p in re.split(r'[\s,;]+', s) if p.strip()]
            out = []
            for p in parts:
                if p.isdigit():
                    idx = int(p)
                    if 0 <= idx <= 6:
                        out.append(idx)
                else:
                    idx = name_to_idx.get(p)
                    if idx is not None:
                        out.append(idx)
            out = sorted(list(dict.fromkeys(out)))
            return out if out else None
        if isinstance(days_value, (list, tuple)):
            out = []
            for v in days_value:
                if isinstance(v, int) and 0 <= v <= 6:
                    out.append(v)
                elif isinstance(v, str):
                    vv = v.strip().lower()
                    if vv.isdigit():
                        idx = int(vv)
                        if 0 <= idx <= 6:
                            out.append(idx)
                    else:
                        idx = name_to_idx.get(vv)
                        if idx is not None:
                            out.append(idx)
            out = sorted(list(dict.fromkeys(out)))
            return out if out else None
        return None
    except Exception:
        return None

def parse_duration_seconds(value) -> int:
    """정수(초), '1h30m', '45m', '900', '00:15:00', '15:00' 등 파싱하여 초 반환."""
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            secs = int(value)
            return secs if secs > 0 else None
        s = str(value).strip().lower()
        # HH:MM:SS 또는 MM:SS
        if ':' in s:
            parts = s.split(':')
            if len(parts) == 3:
                h, m, sec = [int(float(x)) for x in parts]
                total = h*3600 + m*60 + sec
                return total if total > 0 else None
            if len(parts) == 2:
                m, sec = [int(float(x)) for x in parts]
                total = m*60 + sec
                return total if total > 0 else None
        # 1h30m, 45m, 20s
        pattern = re.compile(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?')
        m = pattern.fullmatch(s)
        if m and m.group(0) != '':
            h = int(m.group(1) or 0)
            mi = int(m.group(2) or 0)
            sec = int(m.group(3) or 0)
            total = h*3600 + mi*60 + sec
            return total if total > 0 else None
        # 순수 숫자 문자열
        if s.isdigit():
            secs = int(s)
            return secs if secs > 0 else None
        return None
    except Exception:
        return None

# Full-frame ROI mask builder (rectangle only)
def build_roi_mask_full(frame_shape, roi_rect=None, roi_poly=None):
    h, w = frame_shape
    mask = None
    has_rect = roi_rect is not None and len(roi_rect) == 4
    # 폴리곤 지원 제거: has_poly는 항상 False
    has_poly = False
    if has_rect:
        mask = np.zeros((h, w), dtype=np.uint8)
        print("Rectangle ROI 사용")
        x, y, rw, rh = map(int, roi_rect)
        x = max(0, min(x, w - 1))
        y = max(0, min(y, h - 1))
        rw = max(1, min(int(rw), w - x))
        rh = max(1, min(int(rh), h - y))
        cv2.rectangle(mask, (x, y), (x + rw - 1, y + rh - 1), 255, -1)
    return mask

def measure_file_fps_gst(video_path: str) -> None:
    """저장된 파일을 디코드하며 fpsdisplaysink로 평균 FPS를 콘솔에 출력한다.

    video_path: 측정할 비디오 파일 경로
    """
    try:
        if not os.path.isfile(video_path):
            print(f"[FPS] 파일을 찾을 수 없습니다: {video_path}")
            return
        pipeline_str = (
            f"filesrc location={video_path} ! decodebin ! videoconvert ! "
            f"fpsdisplaysink name=post_fpssink video-sink=fakesink text-overlay=false silent=true signal-fps-measurements=true sync=true"
        )
        pipeline = Gst.parse_launch(pipeline_str)
        fpssink = pipeline.get_by_name('post_fpssink')
        try:
            # fps-measurements(fps, droprate, avg_fps) 시그널이 지원되면 연결
            def on_fps(_elem, fps, droprate, avg):
                try:
                    print(f"[POST-FPS] fps={fps} drop={droprate} avg={avg:.2f}")
                except Exception:
                    pass
            if fpssink is not None and hasattr(fpssink, 'connect'):
                try:
                    fpssink.connect('fps-measurements', on_fps)
                    print("[POST-FPS] fps-measurements 시그널이 지원되어 연결되었습니다.")
                except Exception:
                    print("[POST-FPS] fps-measurements 시그널을 지원하지 않습니다.")
            else:
                print("[POST-FPS] fps-measurements 시그널을 지원하지 않습니다.")

        except Exception:
            pass
        pipeline.set_state(Gst.State.PLAYING)
        bus = pipeline.get_bus()
        try:
            bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS)
        except Exception:
            pass
        try:
            pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
        print(f"[POST-FPS] 완료: {video_path}")
    except Exception as e:
        print(f"[FPS] 측정 실패: {e}")

def begin_session_timeline(epoch_time: float) -> None:
    """세션 시작 시 타임라인 초기화 및 초기 이벤트 기록."""
    global session_active, session_epoch_time, session_param_events
    session_active = True
    session_epoch_time = float(epoch_time)
    session_param_events = []
    # 초기 상태 이벤트(t=0)
    session_param_events.append({
        "t_ns": 0,
        "gamma": float(current_gamma),
        "wb": str(current_wb),
        "mode": str(current_mode),
    })

def end_session_timeline() -> None:
    """세션 종료 표시."""
    global session_active
    session_active = False

def record_param_change_event() -> None:
    """감마/화이트밸런스/모드 변경 시 타임라인에 이벤트 기록(세션 중일 때만)."""
    if not session_active:
        return
    try:
        now_ns = int((time.time() - session_epoch_time) * 1e9)
    except Exception:
        now_ns = 0
    # 동일 값 반복 기록 방지: 마지막 이벤트와 비교
    if session_param_events:
        last = session_param_events[-1]
        if (
            float(last.get("gamma", 0.0)) == float(current_gamma)
            and str(last.get("wb", "")) == str(current_wb)
            and str(last.get("mode", "")) == str(current_mode)
        ):
            return
    session_param_events.append({
        "t_ns": now_ns,
        "gamma": float(current_gamma),
        "wb": str(current_wb),
        "mode": str(current_mode),
    })

# 간단 화이트 밸런스(Gray-World) 적용: RGB 프레임 입력 → RGB 프레임 출력
def apply_simple_wb_rgb(rgb_frame: np.ndarray) -> np.ndarray:
    try:
        if rgb_frame is None or rgb_frame.ndim != 3 or rgb_frame.shape[2] != 3:
            return rgb_frame
        avg_r = float(np.mean(rgb_frame[:, :, 0]))
        avg_g = float(np.mean(rgb_frame[:, :, 1]))
        avg_b = float(np.mean(rgb_frame[:, :, 2]))
        avg_gray = (avg_r + avg_g + avg_b) / 3.0
        if avg_r <= 1e-6 or avg_g <= 1e-6 or avg_b <= 1e-6:
            return rgb_frame
        scale_r = avg_gray / avg_r
        scale_g = avg_gray / avg_g
        scale_b = avg_gray / avg_b
        out = rgb_frame.astype(np.float32)
        out[:, :, 0] *= scale_r
        out[:, :, 1] *= scale_g
        out[:, :, 2] *= scale_b
        out = np.clip(out, 0, 255).astype(np.uint8)
        return out
    except Exception:
        return rgb_frame

def render_merged_raw_from_segments(segment_paths, output_path):
    """세그먼트들을 디코드→단순 병합→재인코딩하여 하나의 mp4 생성(후처리 없음).

    안정성과 호환성을 위해 final 병합과 동일한 방식으로 프레임 단위 병합하되,
    감마/화이트밸런스/모드 변경 등 후처리는 적용하지 않습니다.
    """
    if not segment_paths:
        print("[병합-raw] 세그먼트가 없습니다.")
        return
    try:
        Gst.init(None)

        # 첫 세그먼트에서 캡스 정보 취득
        first_path = segment_paths[0]
        in_pipeline_str = (
            f"filesrc location={first_path} ! qtdemux name=demux "
            f"h264parse name=parser ! avdec_h264 name=decoder ! videoconvert ! video/x-raw,format=RGB ! "
            f"appsink name=in_snk emit-signals=true sync=false drop=false max-buffers=1"
        )
        in_pipeline = Gst.parse_launch(in_pipeline_str)
        demux = in_pipeline.get_by_name('demux')
        parser = in_pipeline.get_by_name('parser')
        in_sink = in_pipeline.get_by_name('in_snk')
        def on_pad_added(demux_elem, pad):
            sinkpad = parser.get_static_pad('sink')
            if not sinkpad.is_linked():
                try:
                    pad.link(sinkpad)
                except Exception:
                    pass
        demux.connect('pad-added', on_pad_added)
        in_pipeline.set_state(Gst.State.PLAYING)

        first_sample = in_sink.emit('pull-sample')
        if first_sample is None:
            in_pipeline.set_state(Gst.State.NULL)
            raise RuntimeError('첫 세그먼트에서 샘플을 읽지 못했습니다')
        caps = first_sample.get_caps()
        s = caps.get_structure(0)
        width = s.get_value('width')
        height = s.get_value('height')
        num, den = (30, 1)
        if s.has_field('framerate'):
            try:
                num, den = s.get_fraction('framerate')
            except Exception:
                num, den = (30, 1)
        frame_duration_ns = int(1e9 * (den / max(1, num)))

        # 출력 파이프라인 생성 (후처리 없음)
        out_pipeline_str = (
            f"appsrc name=out_src is-live=false format=time do-timestamp=false caps=video/x-raw,format=RGB,width={width},height={height},framerate={num}/{den} ! "
            f"videoconvert ! x264enc quantizer=20 pass=qual qp-min=20 qp-max=40 key-int-max=60 speed-preset=ultrafast ! h264parse config-interval=-1 ! mp4mux faststart=true ! filesink location={output_path}"
        )
        out_pipeline = Gst.parse_launch(out_pipeline_str)
        out_src = out_pipeline.get_by_name('out_src')
        out_pipeline.set_state(Gst.State.PLAYING)

        cumulative_pts = 0
        def push_frame(sample):
            nonlocal cumulative_pts
            buf = sample.get_buffer()
            ok, mapinfo = buf.map(Gst.MapFlags.READ)
            if not ok:
                return
            try:
                data = bytes(mapinfo.data)
            finally:
                buf.unmap(mapinfo)
            out_buf = Gst.Buffer.new_allocate(None, len(data), None)
            out_buf.fill(0, data)
            out_buf.pts = cumulative_pts
            out_buf.dts = cumulative_pts
            out_buf.duration = frame_duration_ns
            out_src.emit('push-buffer', out_buf)
            cumulative_pts += frame_duration_ns

        # 첫 샘플 처리
        push_frame(first_sample)
        while True:
            sample = in_sink.emit('pull-sample')
            if sample is None:
                break
            push_frame(sample)
        in_pipeline.set_state(Gst.State.NULL)

        # 나머지 세그먼트 처리
        for p in segment_paths[1:]:
            in_pipeline_str = (
                f"filesrc location={p} ! qtdemux name=demux "
                f"h264parse name=parser ! avdec_h264 name=decoder ! videoconvert ! video/x-raw,format=RGB ! "
                f"appsink name=in_snk emit-signals=true sync=false drop=false max-buffers=1"
            )
            in_pipeline = Gst.parse_launch(in_pipeline_str)
            demux = in_pipeline.get_by_name('demux')
            parser = in_pipeline.get_by_name('parser')
            in_sink = in_pipeline.get_by_name('in_snk')
            def on_pad_added2(demux_elem, pad):
                sinkpad = parser.get_static_pad('sink')
                if not sinkpad.is_linked():
                    try:
                        pad.link(sinkpad)
                    except Exception:
                        pass
            demux.connect('pad-added', on_pad_added2)
            in_pipeline.set_state(Gst.State.PLAYING)

            while True:
                sample = in_sink.emit('pull-sample')
                if sample is None:
                    break
                push_frame(sample)
            in_pipeline.set_state(Gst.State.NULL)

        out_src.emit('end-of-stream')
        bus_out = out_pipeline.get_bus()
        bus_out.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS)
        out_pipeline.set_state(Gst.State.NULL)
        print(f"[병합-raw] 완료: {output_path}")
    except Exception as e:
        print(f"[병합-raw] 실패: {e}")
    
def ffmpeg_concat_mp4(segment_paths, output_path) -> bool:
    """
    ffmpeg concat demuxer로 mp4 파일들을 무재인코딩 병합(스트림 카피) 시도.
    모든 입력이 동일 코덱/프로필/타임베이스이어야 성공. 실패 시 False.
    """
    try:
        if not segment_paths:
            print("[concat] 입력 세그먼트가 없습니다")
            return False
        # ffmpeg 확인
        ffmpeg_bin = shutil.which('ffmpeg')
        if ffmpeg_bin is None:
            print("[concat] ffmpeg를 찾을 수 없습니다")
            return False
        # 임시 파일 목록 생성
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.txt', encoding='utf-8') as tf:
            list_path = tf.name
            for p in segment_paths:
                if isinstance(p, str) and os.path.isfile(p):
                    tf.write(f"file '{p}'\n")
        # concat demuxer + stream copy
        cmd = [ffmpeg_bin, '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list_path, '-c', 'copy', '-movflags', '+faststart', output_path]
        print(f"[concat] 실행: {' '.join(cmd)}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            os.unlink(list_path)
        except Exception:
            pass
        if res.returncode == 0 and os.path.isfile(output_path):
            print(f"[concat] 성공: {output_path}")
            return True
        print(f"[concat] 실패: rc={res.returncode}, err={res.stderr[:200]}")
        return False
    except Exception as e:
        print(f"[concat] 예외: {e}")
        return False

def ffmpeg_concat_mp4(segment_paths, output_path) -> bool:
    """
    ffmpeg concat demuxer로 mp4 파일들을 무재인코딩 병합(스트림 카피) 시도.
    모든 입력이 동일 코덱/프로필/타임베이스이어야 성공. 실패 시 False.
    """
    try:
        if not segment_paths:
            print("[concat] 입력 세그먼트가 없습니다")
            return False
        # ffmpeg, ffprobe 확인
        ffmpeg_bin = shutil.which('ffmpeg')
        if ffmpeg_bin is None:
            print("[concat] ffmpeg를 찾을 수 없습니다")
            return False
        # 임시 파일 목록 생성
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.txt', encoding='utf-8') as tf:
            list_path = tf.name
            for p in segment_paths:
                if isinstance(p, str) and os.path.isfile(p):
                    tf.write(f"file '{p}'\n")
        # concat demuxer + stream copy
        cmd = [ffmpeg_bin, '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list_path, '-c', 'copy', '-movflags', '+faststart', output_path]
        print(f"[concat] 실행: {' '.join(cmd)}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            os.unlink(list_path)
        except Exception:
            pass
        if res.returncode == 0 and os.path.isfile(output_path):
            print(f"[concat] 성공: {output_path}")
            return True
        print(f"[concat] 실패: rc={res.returncode}, err={res.stderr[:200]}")
        return False
    except Exception as e:
        print(f"[concat] 예외: {e}")
        return False

# --- RTSP Server ---
def ensure_rtsp_server(width: int, height: int, framerate: int, bitrate_kbps: int, service_port: str = "8554"):
    global rtsp_server, rtsp_mounts, rtsp_factory, rtsp_loop, rtsp_appsrc_ref, current_mode, rtsp_vb_element, current_gamma, file_gamma, current_frame
    if rtsp_server is not None:
        return
    Gst.init(None)
    # target scale size from current_frame
    try:
        tw, th = str(current_frame).lower().replace(' ', '').split('x', 1)
        target_w = int(tw); target_h = int(th)
    except Exception:
        target_w, target_h = width, height
    rtsp_server = GstRtspServer.RTSPServer()
    try:
        rtsp_server.set_service(service_port)
    except Exception:
        pass
    rtsp_mounts = rtsp_server.get_mount_points()
    rtsp_factory = GstRtspServer.RTSPMediaFactory()
    rtsp_factory.set_shared(True)
    # gamma 요소를 파이프라인에 추가 + videoscale 로 target 해상도로 스케일링
    launch_str = (
        f"( appsrc name=rtsp_src is-live=true block=true do-timestamp=true format=time "
        f"caps=video/x-raw,format=RGB,width={width},height={height},framerate={framerate}/1 ! "
        f"videoscale name=rtsp_vscale add-borders=true ! capsfilter name=rtsp_scale_caps caps=video/x-raw,format=RGB,width={target_w},height={target_h} ! "
        f"videobalance name=rtsp_vb ! "
        f"gamma name=rtsp_gamma gamma={current_gamma} ! "
        f"videoconvert ! video/x-raw,format=I420 ! "
        f"x264enc tune=zerolatency qp-min=25 qp-max=35 noise-reduction=30 b-adapt=false vbv-buf-capacity=400 speed-preset=superfast key-int-max=30 bitrate={bitrate_kbps} ! "
        f"h264parse ! rtph264pay name=pay0 pt=96 config-interval=2 )"
    )
    rtsp_factory.set_launch(launch_str)
    

    def on_media_configure(factory, media):  # 콜백함수 설정
        element = media.get_element()
        src = element.get_by_name("rtsp_src")
        if src:
            rtsp_appsrc_ref["appsrc"] = src
            print("[DEBUG] RTSP appsrc ready")  
        global rtsp_vb_element, gamma_element
        vb = element.get_by_name("rtsp_vb")
        gamma_element = element.get_by_name("rtsp_gamma")
        try:
            # x264enc는 동적으로 이름이 pay0 이전에 생성되므로 파이프라인에서 탐색
            x264 = None
            try:
                x264 = element.get_by_name('x264enc0') or element.get_by_name('x264enc')
            except Exception:
                x264 = None
            globals()['rtsp_x264_element'] = x264
        except Exception:
            globals()['rtsp_x264_element'] = None
        try:
            globals()['rtsp_scale_caps_element'] = element.get_by_name('rtsp_scale_caps')
        except Exception:
            globals()['rtsp_scale_caps_element'] = None
        try:
            globals()['rtsp_videobox_element'] = element.get_by_name('rtsp_videobox')
        except Exception:
            globals()['rtsp_videobox_element'] = None
        # no dynamic scale caps element anymore
        if vb:
            # 초기 설정값 설정
            rtsp_vb_element = vb

            # gstreamer에서 조정 가능한 카메라 설정값 예시
            # vb.set_property('brightness', 0.0)  # 밝기 -1 ~ 1
            # vb.set_property('contrast', 0.0)    # 대비 0 ~ 2
            # vb.set_property('hue', 0.0)        # 휘도 -1 ~ 1
            # vb.set_property('saturation', 0.0) # 채도 0 ~ 2
            # vb.set_property('gamma', 1.0)      # 감마 0.5~ 2.5 (실제 범위 0 ~ 4)
            try:
                vb.set_property('saturation', 0.0 if current_mode == 'gray' else 1.0)
            except Exception:
                pass
        if gamma_element:
            # 초기 감마값 적용
            try:
                gamma_element.set_property('gamma', current_gamma)
            except Exception:
                pass

    rtsp_factory.connect("media-configure", on_media_configure)
    # 마운트 추가 (이미 존재하면 무시)
    try:
        rtsp_mounts.add_factory(rtsp_path, rtsp_factory)
    except Exception as e:
        print(f"[DEBUG] mount add warning: {e}")
    rtsp_server.attach(None)
    # GLib MainLoop 백그라운드 실행
    rtsp_loop = GObject.MainLoop()
    import threading
    threading.Thread(target=rtsp_loop.run, daemon=True).start()
    print(f"[DEBUG] RTSP server running at rtsp://127.0.0.1:{service_port}{rtsp_path}")

def set_gamma(gamma_value: float):
    """
    RTSP 스트림의 gamma 값을 동적으로 변경합니다.
    """
    global gamma_element
    try:
        if file_gamma is not None:
            file_gamma.set_property('gamma', gamma_value)
        if gamma_element is not None:
            gamma_element.set_property('gamma', gamma_value)
            print(f"[RTSP] gamma 값이 {gamma_value}로 변경되었습니다.")
        else:
            print("[RTSP] gamma element가 아직 준비되지 않았습니다.")
    except Exception as e:
        print(f"[RTSP] gamma 변경 실패: {e}")
# --- Remote MQTT Client Class ---
class RemoteMQTTClient:
    def __init__(self, client_id, broker_host='127.0.0.1', broker_port=8883):
        self.client_id = client_id
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.thing_name = 'thing_name'
        self.client = mqtt.Client(client_id=client_id)
        
        # TLS 인증서 경로 설정
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        self.ca_certfile = "/home/openiot/project/openiot-201-firmware-camera/raspberrypi_cam/certs/ca.crt" 
        # self.client_certfile = os.path.join(base_dir, 'mqtt_tls_demo', 'certs', 'client.crt')
        # self.client_keyfile = os.path.join(base_dir, 'mqtt_tls_demo', 'certs', 'client.key')
        # TLS 설정
        self.client.tls_set(
            ca_certs=self.ca_certfile,
            # certfile=self.client_certfile,
            # keyfile=self.client_keyfile,
            tls_version=ssl.PROTOCOL_TLSv1_2
        )
        self.client.tls_insecure_set(False)

        # 연결 상태 추적
        self.connected = False
        self.connection_event = threading.Event()
        self.running = False

        # 로컬 IP 주소 가져오기
        self.local_ip = self.get_local_ip()

        # 콜백 함수 설정
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe
        self.client.on_publish = self.on_publish

        # GPIO.setmode(GPIO.BCM)
        # GPIO.setup(17, GPIO.IN)
        LED_PIN.off()


    def get_local_ip(self):
        """로컬 IP 주소 가져오기"""
        try:
            # 외부 연결을 통해 로컬 IP 확인
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except Exception:
            try:
                # 대안 방법
                hostname = socket.gethostname()
                local_ip = socket.gethostbyname(hostname)
                return local_ip
            except Exception:
                return "unknown"

    def on_connect(self, client, userdata, flags, rc):
        """연결 콜백"""
        if rc == 0:
            print(f"클라이언트 {self.client_id}가 서버 {self.broker_host}:{self.broker_port}에 연결되었습니다.")
            print(f"로컬 IP: {self.local_ip}")
            self.connected = True

            # 현재 디바이스의 큐 토픽 구독
            queue_topic = f"{self.thing_name}/queue"
            client.subscribe(queue_topic, qos=1)
            print(f"토픽 구독: {queue_topic}")
            
            # 상태 요청 토픽 구독
            status_req_topic = f"things/{self.thing_name}/status/req"
            client.subscribe(status_req_topic, qos=1)
            print(f"토픽 구독: {status_req_topic}")
            
            # 명령 요청 토픽 구독
            command_req_topic = f"things/{self.thing_name}/command/req"
            client.subscribe(command_req_topic, qos=1)
            print(f"토픽 구독: {command_req_topic}")

            self.connection_event.set()
        else:
            print(f"연결 실패. 코드: {rc}")
            self.connected = False
            self.connection_event.set()

    def on_disconnect(self, client, userdata, rc):
        """연결 해제 콜백"""
        print(f"클라이언트 {self.client_id}가 연결 해제되었습니다.")
        self.connected = False

    def extract_json_from_raw(self, raw_string):
        """raw 문자열에서 중괄호 밖의 모든 것을 제거하고 JSON만 추출"""
        try:
            # 중괄호 시작과 끝 위치 찾기
            start_brace = raw_string.find('{')
            end_brace = raw_string.rfind('}')
            
            if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
                # 중괄호 안의 내용만 추출
                json_part = raw_string[start_brace:end_brace + 1]
                return json_part
            else:
                print(f"JSON 형식을 찾을 수 없습니다: {raw_string}")
                return None
        except Exception as e:
            print(f"JSON 추출 중 오류: {e}")
            return None
        
    def process_command_request(self, data):
        """명령 요청 처리 및 응답"""
        try:
            request_id = data.get('request_id')
            command_data = {k: v for k, v in data.items() if k not in ['request_id', 'timestamp', 'source']}
            
            print(f"=== 명령 요청 수신: {request_id} ===")
            print(f"명령 데이터: {command_data}")
            
            # 명령 처리 결과
            command_result = {
                'request_id': request_id,
                'thing_name': self.thing_name,
                'command_received': True,
                'command_data': command_data,
                'timestamp': datetime.now().isoformat(),
                'status': 'executed',
                'result': 'success'
            }
            
            # 명령 타입에 따른 처리
            command_type = command_data.get('command', 'unknown')
            
            if command_type == 'restart':
                command_result['result'] = 'restart_scheduled'
                command_result['message'] = '디바이스 재시작이 예약되었습니다.'
                print("재시작 명령 처리: 재시작이 예약되었습니다.")
                
            elif command_type == 'status':
                command_result['result'] = 'status_updated'
                command_result['message'] = '상태 정보가 업데이트되었습니다.'
                print("상태 업데이트 명령 처리: 상태 정보가 업데이트되었습니다.")
                
            elif command_type == 'stop_processing':
                # 현재 처리 중인 메시지들을 중단
                stopped_count = len(self.processing_messages)
                command_result['result'] = 'processing_stopped'
                command_result['message'] = f'{stopped_count}개의 처리 중인 메시지가 중단되었습니다.'
                command_result['stopped_messages'] = list(self.processing_messages.keys())
                print(f"처리 중단 명령 처리: {stopped_count}개의 메시지 처리 중단")
                
            # elif command_type == 'update_status':
            #     # status.json 파일 수정
            #     update_result = self.update_status_json(command_data)
            #     command_result['result'] = update_result['result']
            #     command_result['message'] = update_result['message']
            #     if 'updated_fields' in update_result:
            #         command_result['updated_fields'] = update_result['updated_fields']
            #     print(f"상태 파일 수정 처리: {update_result['message']}")
                
            # else:
            #     # 일반적인 필드 업데이트 명령 처리
            #     if 'updates' in command_data:
            #         update_result = self.update_status_json(command_data)
            #         command_result['result'] = update_result['result']
            #         command_result['message'] = update_result['message']
            #         if 'updated_fields' in update_result:
            #             command_result['updated_fields'] = update_result['updated_fields']
            #         print(f"필드 업데이트 처리: {update_result['message']}")
            #     else:
            #         command_result['result'] = 'unknown_command'
            #         command_result['message'] = f'알 수 없는 명령: {command_type}'
            #         print(f"알 수 없는 명령 처리: {command_type}")
            
            # 명령 응답 전송
            response_topic = f"things/{self.thing_name}/command/res"
            response_message = json.dumps(command_result, ensure_ascii=False)
            
            result = self.client.publish(response_topic, response_message, qos=1)
            print(f"명령 응답 전송: success")
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                print(f"✓ 명령 응답 전송 성공: {response_topic}")
                print(f"응답 내용: {response_message}")
            else:
                print(f"✗ 명령 응답 전송 실패: {result.rc}")
                
        except Exception as e:
            print(f"명령 요청 처리 중 오류: {e}")
            import traceback
            traceback.print_exc()
            
            # 오류 발생 시에도 응답 전송
            try:
                error_result = {
                    'request_id': data.get('request_id'),
                    'thing_name': self.thing_name,
                    'command_received': True,
                    'command_data': data,
                    'timestamp': datetime.now().isoformat(),
                    'status': 'error',
                    'result': 'error',
                    'error_message': str(e)
                }
                
                response_topic = f"things/{self.thing_name}/command/res"
                response_message = json.dumps(error_result, ensure_ascii=False)
                
                result = self.client.publish(response_topic, response_message, qos=1)
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    print(f"✓ 오류 응답 전송 성공: {response_topic}")
                else:
                    print(f"✗ 오류 응답 전송 실패: {result.rc}")
            except Exception as send_error:
                print(f"오류 응답 전송 중 추가 오류: {send_error}")
    
    def process_status_request(self, data):
        """상태 요청 처리 및 응답"""
        try:
            request_id = data.get('request_id')
            print(f"=== 상태 요청 수신 ===")
            print(f"요청 ID: {request_id}")
            print(f"요청 데이터: {data}")
            print(f"디바이스: {self.thing_name}")
            
            # status.json 파일에서 상태 정보 읽기
            status_file_path = os.path.join(os.path.dirname(__file__), 'camera_state.json')
            status_info = {}
            
            try:
                with open(status_file_path, 'r', encoding='utf-8') as f:
                    status_info = json.load(f)
                print(f"✓ status.json 파일 읽기 성공: {status_file_path}")
                print(f"파일 내용: {status_info}")
            except FileNotFoundError:
                print(f"✗ status.json 파일을 찾을 수 없습니다: {status_file_path}")
                # 기본 상태 정보 생성
                status_info = {
                    'thing_name': self.thing_name,
                    'status': 'online',
                    'timestamp': datetime.now().isoformat(),
                    'processing_messages_count': 0,
                    'processing_messages': [],
                    'version': '1.0.0',
                    'capabilities': ['queue_processing', 'status_reporting', 'command_execution']
                }
                print(f"기본 상태 정보 생성: {status_info}")
            except json.JSONDecodeError as e:
                print(f"✗ status.json 파일 파싱 오류: {e}")
                # 기본 상태 정보 생성
                status_info = {
                    'thing_name': self.thing_name,
                    'status': 'online',
                    'timestamp': datetime.now().isoformat(),
                    'processing_messages_count': 0,
                    'processing_messages': [],
                    'version': '1.0.0',
                    'capabilities': ['queue_processing', 'status_reporting', 'command_execution']
                }
                print(f"기본 상태 정보 생성: {status_info}")
            
            # 시스템 정보 가져오기
            cpu_temp = get_cpu_temp()
            cpu_usage, ram_usage = get_system_usage()
            
            # 동적 정보 업데이트
            status_info.update({
                'request_id': request_id,
                'thing_name': self.thing_name,
                'timestamp': datetime.now().isoformat(),
                # 'processing_messages_count': len(self.processing_messages),
                # 'processing_messages': list(self.processing_messages.keys()),
                'cpu_temperature': cpu_temp,
                'cpu_usage': cpu_usage,
                'ram_usage': ram_usage
            })
            
            print(f"최종 상태 정보: {status_info}")
            
            # 상태 응답 전송
            response_topic = f"things/{self.thing_name}/status/res"
            response_message = json.dumps(status_info, ensure_ascii=False)
            
            print(f"응답 토픽: {response_topic}")
            print(f"응답 메시지: {response_message}")
            
            result = self.client.publish(response_topic, response_message, qos=1)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                print(f"✓ 상태 응답 전송 성공: {response_topic}")
                print(f"응답 내용: {response_message}")
            else:
                print(f"✗ 상태 응답 전송 실패: {result.rc}")
                
        except Exception as e:
            print(f"✗ 상태 요청 처리 중 오류: {e}")
            import traceback
            traceback.print_exc()
    
    def process_queue_message(self, data):
        """MQTT로 받은 큐 메시지 처리"""
        try:
            thing_name = data.get('thingName')
            filepath = data.get('filepath')
            message_id = data.get('message_id')
            
            print(f"=== 큐 작업 시작: {thing_name} - {filepath} ===")
            print(f"메시지 ID: {message_id}")
            print(f"작업 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 처리 중인 메시지로 등록
            self.processing_messages[message_id] = {
                'start_time': datetime.now(),
                'data': data,
                'thing_name': thing_name,
                'filepath': filepath,
                'message_id': message_id,
                'type': 'queue'
            }
            
            # 15분 카운팅
            def complete_work():
                print(f"=== 15분 카운팅 시작: {thing_name} - {filepath} ===")
                total_seconds = 15*60  # 15분 = 900초
                for i in range(total_seconds, 0, -1):
                    minutes = i // 60
                    seconds = i % 60
                    print(f"   카운팅: {minutes:02d}:{seconds:02d} 남음...")
                    time.sleep(1)
                
                print(f"=== 큐 작업 완료: {thing_name} - {filepath} ===")
                print(f"작업 완료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                
                # 큐에서 메시지 제거 (message_id 사용)
                success = self.dequeue_message(message_id)
                
                if success:
                    # 처리 중인 메시지에서 제거
                    if message_id in self.processing_messages:
                        del self.processing_messages[message_id]
                    print(f"✓ 큐 작업 완료 및 메시지 제거 성공: {thing_name} - {filepath}")
                else:
                    print(f"✗ 메시지 제거 실패: {thing_name} - {filepath}")
            
            # 별도 스레드에서 작업 완료 처리
            work_thread = threading.Thread(target=complete_work)
            work_thread.daemon = True
            work_thread.start()
            
        except Exception as e:
            print(f"큐 메시지 처리 중 오류: {e}")
    

    def on_message(self, client, userdata, msg):
        """메시지 수신 시 처리"""
        global current_gamma, current_mode, current_wb, current_roi, current_bitrate, of_enabled, current_frame, current_fps, camera_thread
        global SCHEDULE_MODE_HOUR, SCHEDULE_MODE_MINUTE, MOTION_MODE_HOUR, MOTION_MODE_MINUTE, SCHEDULE_DAYS, MOTION_DAYS, SCHEDULE_DURATION_SEC

        raw = msg.payload.decode('utf-8', 'ignore')
        print(f'수신 [{msg.topic}]: {raw}')

        # JSON 명령 파싱
        try:
            cmd = json.loads(self.extract_json_from_raw(raw))
        except Exception:
            cmd = None

        # JSON 파싱 시도
        data = cmd
        
        if data is not None:
            # 토픽에 따라 메시지 처리
            if msg.topic == f"{self.thing_name}/queue":
                # 큐 메시지 처리
                self.process_queue_message(data)
            elif msg.topic == f"things/{self.thing_name}/status/req":
                # 상태 요청 처리
                self.process_status_request(data)
            elif msg.topic == f"things/{self.thing_name}/command/req":
                # 간단 명령 직접 처리 (HLS/녹화), 기타는 기존 처리기로 전달
                cmd = str(data.get('command', '')).lower() if isinstance(data, dict) else ''
                if cmd in ['hls_on', 'hls_start']:
                    try:
                        w, h = [int(v) for v in str(current_frame).split('x')]
                    except Exception:
                        w, h = 1280, 720
                    start_hls_http_server()
                    start_hls_pipeline(w, h, int(current_fps), int(current_bitrate//1000))
                    print('[CMD] HLS 시작')
                elif cmd in ['hls_off', 'hls_stop']:
                    # 항상 켜짐: 중지 요청 무시하고 유지
                    try:
                        w, h = [int(v) for v in str(current_frame).split('x')]
                    except Exception:
                        w, h = 1280, 720
                    try:
                        start_hls_http_server()
                        start_hls_pipeline(w, h, int(current_fps), int(current_bitrate//1000))
                    except Exception:
                        pass
                    print('[CMD] HLS 항상 켜짐: 중지 요청 무시하고 유지')
                elif cmd in ['start_recording', 'record_on']:
                    try:
                        if camera_frame is not None:
                            ok, msg_text = start_recording_manual(camera_frame)
                            print(f"[CMD] 녹화 시작: {ok} {msg_text}")
                    except Exception as e:
                        print(f"[CMD] 녹화 시작 실패: {e}")
                elif cmd in ['stop_recording', 'record_off']:
                    ok, msg_text = stop_recording_manual()
                    print(f"[CMD] 녹화 중지: {ok} {msg_text}")
                elif cmd in ['camera_on']:
                    try:
                        if camera_thread is None or not camera_thread.is_alive():
                            camera_stop_event.clear()
                            camera_thread = threading.Thread(target=lambda: camera_on(), daemon=True)
                            camera_thread.start()
                            print('[CMD] camera_on 시작')
                    except Exception as e:
                        print(f"[CMD] camera_on 실패: {e}")
                elif cmd in ['camera_off']:
                    try:
                        camera_stop_event.set()
                        print('[CMD] camera_off 요청')
                    except Exception as e:
                        print(f"[CMD] camera_off 실패: {e}")
                else:
                    # 기존 처리기로 전달
                    self.process_command_request(data)
            else:
                print(f"알 수 없는 토픽: {msg.topic}")
        else:
            print(f"JSON 파싱 실패: {cmd}")
            print(f"원본 메시지 (hex): {msg.payload.hex()}")

        # 설정 업데이트 (최소 변수만 사용)
        # updates 키가 있으면 그 안의 dict를 cmd로 사용, 아니면 기존대로
        update_dict = None
        if isinstance(cmd, dict):
            if 'device_settings' in cmd and isinstance(cmd['device_settings'], dict):
                update_dict = cmd['device_settings']
            else:
                update_dict = cmd

        if isinstance(update_dict, dict):
            if 'mode' in update_dict:
                try:
                    mode_val = str(update_dict['mode']).lower()
                    if mode_val in ['gray', 'rgb']:
                        current_mode = mode_val
                        print(f"Mode set to: {current_mode}")
                        try:
                            save_last_mode_to_disk()
                        except Exception:
                            pass
                        # 파일/RTSP 파이프라인 videobalance에 동기 적용
                        try:
                            if 'file_vb_element' in globals() and file_vb_element is not None:
                                file_vb_element.set_property('saturation', 0.0 if current_mode == 'gray' else 1.0)
                        except Exception:
                            pass
                        try:
                            if rtsp_vb_element is not None:
                                rtsp_vb_element.set_property('saturation', 0.0 if current_mode == 'gray' else 1.0)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"모드 값 파싱 실패: {e}")
            # 화이트밸런스 실시간 적용 (auto/none)
            if 'wb' in update_dict:
                try:
                    wb_val = str(update_dict['wb']).lower()
                    if wb_val in ['auto', 'none']:
                        current_wb = wb_val
                        print(f"WB mode set to: {current_wb}")
                        try:
                            save_last_mode_to_disk()
                        except Exception:
                            pass
                        # 파일/RTSP 모두 프레임 푸시 직전 경로에서 apply_simple_wb_rgb()로 반영하므로
                        # 여기서는 상태만 업데이트하면 즉시 반영됨
                except Exception as e:  
                    print(f"WB 값 파싱 실패: {e}")
            if 'gamma' in update_dict:
                try:
                    current_gamma = float(update_dict['gamma'])
                    set_gamma(current_gamma)
                    print(f"Gamma set to: {current_gamma}")
                    try:
                        save_last_mode_to_disk()
                    except Exception:
                        pass
                except Exception as e:
                    print(f"감마 값 파싱 실패: {e}")
            if 'frame' in update_dict:
                # 해상도 문자열 "WxH" 파싱 후 즉시 반영 준비
                try:
                    frame_val = str(update_dict['frame']).lower().replace(' ', '')
                    if 'x' in frame_val:
                        w_str, h_str = frame_val.split('x', 1)
                        w_new = int(w_str)
                        h_new = int(h_str)
                        current_frame = f"{w_new}x{h_new}"
                        print(f"Frame set to: {current_frame}")
                        try:
                            save_last_mode_to_disk()
                        except Exception:
                            pass
                        # 파이프라인 재생성 없이, scale caps/videobox 속성만 갱신
                        # 파일 파이프라인은 그대로 유지 (요청에 따라 미변경)
                        try:
                            # RTSP 파이프라인: RGB caps 갱신
                            if 'rtsp_scale_caps_element' in globals() and rtsp_scale_caps_element is not None:
                                caps_str = f"video/x-raw,format=RGB,pixel-aspect-ratio=1/1,width={w_new},height={h_new}"
                                new_caps = Gst.Caps.from_string(caps_str)
                                rtsp_scale_caps_element.set_property('caps', new_caps)
                                print(f"[RTSP] videoscale caps updated: {caps_str}")
                        except Exception as e_caps2:
                            print(f"[RTSP] videoscale caps update failed: {e_caps2}")
                except Exception as e:
                    print(f"Frame 값 파싱 실패: {e}")
            # (삭제됨) Crop 설정
            if 'fps' in update_dict:
                current_fps = int(update_dict['fps'])
                print(f"FPS set to: {current_fps}")
            if 'bitrate' in update_dict:
                try:
                    current_bitrate = int(update_dict['bitrate'])
                    print(f"Bitrate set to: {current_bitrate}")
                    try:
                        save_last_mode_to_disk()
                    except Exception:
                        pass
                    # RTSP 인코더 런타임 반영
                    try:
                        if 'rtsp_x264_element' in globals() and rtsp_x264_element is not None:
                            rtsp_x264_element.set_property('bitrate', max(1, int(current_bitrate // 1000)))
                            print(f"[RTSP] x264enc bitrate updated: {current_bitrate//1000} kbps")
                    except Exception as e:
                        print(f"[RTSP] bitrate update failed: {e}")
                except Exception as e:
                    print(f"Bitrate 값 파싱 실패: {e}")
            # Optical Flow 토글
            if 'opt_flow' in update_dict:
                try:
                    val = str(update_dict['opt_flow']).lower()
                    from_values = {'on': True, 'off': False, 'true': True, 'false': False, '1': True, '0': False}
                    prev = bool(of_enabled)
                    of_enabled = from_values.get(val, bool(update_dict['opt_flow']))
                    save_last_mode_to_disk()
                    print(f"Optical Flow enabled: {of_enabled}")
                    # 스케줄 모드 → 모션 감지 모드 전환 시, 카메라 즉시 시작
                    if (not prev) and of_enabled:
                        try:
                            if camera_thread is None or not camera_thread.is_alive():
                                camera_stop_event.clear()
                                camera_thread = threading.Thread(target=lambda: camera_on(), daemon=True)
                                camera_thread.start()
                                print('[모션 모드] 전환 감지: 카메라 즉시 시작')
                        except Exception as e:
                            print(f"[모션 모드] 전환 시 시작 오류: {e}")
                    # 모션 → 스케줄 전환 시에는 세션 중지(예약 시간만 동작)
                    if prev and (not of_enabled):
                        try:
                            camera_stop_event.set()
                            print('[스케줄 모드] 전환 감지: 현재 세션 정지 요청')
                        except Exception:
                            pass
                except Exception as e:
                    print(f"Optical Flow 토글 파싱 실패: {e}")
            # ROI 설정: 기본은 사각형 [x, y, w, h]
            # 폴리곤이 오면 바운딩 박스로 변환해 사각형으로 저장 (후방 호환)
            if 'roi' in update_dict:
                try:
                    raw_roi = update_dict.get('roi')
                    if isinstance(raw_roi, (list, tuple)) and len(raw_roi) == 4:
                        rx, ry, rw, rh = [int(v) for v in raw_roi]
                        if rw <= 0 or rh <= 0:
                            raise ValueError('w,h는 양수여야 합니다')
                        current_roi = (rx, ry, rw, rh)
                        print(f"Rectangle ROI set to: {current_roi}")
                        try:
                            save_last_mode_to_disk()
                        except Exception:
                            pass
                    else:
                        print("ROI 무시: [x,y,w,h] 형식이어야 합니다")
                except Exception as e:
                    print(f"ROI 파싱 실패: {e}")
            # schedule_time은 반드시 4자리 숫자 문자열(예: "0940", "2345")로만 받도록 수정
            if 'schedule_time' in update_dict:
                try:
                    t = str(update_dict.get('schedule_time')).strip()
                    if len(t) == 4 and t.isdigit():
                        SCHEDULE_MODE_HOUR = int(t[:2]) % 24
                        SCHEDULE_MODE_MINUTE = int(t[2:]) % 60
                        print(f"[스케줄] 시간 갱신 → {SCHEDULE_MODE_HOUR:02d}:{SCHEDULE_MODE_MINUTE:02d}")
                        try:
                            schedule_update_event.set()
                        except Exception:
                            pass
                        save_last_mode_to_disk()
                    else:
                        print("[스케줄] 시간 파싱 실패: 4자리 숫자 문자열(예: '0940')만 허용됩니다.")
                except Exception as e:
                    print(f"[스케줄] 시간 파싱 실패: {e}")
            if 'schedule_days' in update_dict:
                try:
                    parsed = parse_schedule_days(update_dict.get('schedule_days'))
                    if parsed:
                        SCHEDULE_DAYS = parsed
                        print(f"[스케줄] 요일 갱신 → {SCHEDULE_DAYS}")
                        try:
                            schedule_update_event.set()
                        except Exception:
                            pass
                        save_last_mode_to_disk()
                except Exception as e:
                    print(f"[스케줄] 요일 파싱 실패: {e}")
            if 'schedule_duration' in update_dict or 'schedule_duration_sec' in update_dict:
                try:
                    dur_val = update_dict.get('schedule_duration', update_dict.get('schedule_duration_sec'))
                    secs = parse_duration_seconds(dur_val)
                    if isinstance(secs, int) and secs > 0:
                        SCHEDULE_DURATION_SEC = secs
                        print(f"[스케줄] 동작시간 갱신 → {SCHEDULE_DURATION_SEC}s")
                        try:
                            schedule_update_event.set()
                        except Exception:
                            pass
                        save_last_mode_to_disk()
                except Exception as e:
                    print(f"[스케줄] 동작시간 파싱 실패: {e}")
            # 모션감지 시간도 "0920"과 같이 4자리 숫자 문자열로만 받도록 수정
            if 'motion_time' in update_dict:
                try:
                    t = str(update_dict.get('motion_time')).strip()
                    if len(t) == 4 and t.isdigit():
                        MOTION_MODE_HOUR = int(t[:2]) % 24
                        MOTION_MODE_MINUTE = int(t[2:]) % 60
                        print(f"[모션] 시간 갱신 → {MOTION_MODE_HOUR:02d}:{MOTION_MODE_MINUTE:02d}")
                        # 모션러에게 즉시 재계산 알림
                        try:
                            motion_update_event.set()
                        except Exception:
                            pass
                        save_last_mode_to_disk()
                    else:
                        print("[모션] 시간 파싱 실패: 4자리 숫자 문자열(예: '0920')만 허용됩니다.")
                except Exception as e:
                    print(f"[모션] 시간 파싱 실패: {e}")
            if 'motion_days' in update_dict:
                try:
                    parsed_md = parse_schedule_days(update_dict.get('motion_days'))
                    if parsed_md:
                        MOTION_DAYS = parsed_md
                        print(f"[모션] 요일 갱신 → {MOTION_DAYS}")
                        try:
                            motion_update_event.set()
                        except Exception:
                            pass
                        save_last_mode_to_disk()
                except Exception as e:
                    print(f"[모션] 요일 파싱 실패: {e}")

        # 요구사항: 수동 camera_on/off 비활성화 → 예약 기반만 동작
                
    def on_subscribe(self, client, userdata, mid, granted_qos):
        """구독 콜백"""
        print(f"구독 완료. QoS: {granted_qos}")

    def on_publish(self, client, userdata, mid):
        """발행 콜백"""
        print(f"메시지 발행 완료. 메시지 ID: {mid}")

    def connect(self):
        """서버에 연결"""
        try:
            print(f"클라이언트 {self.client_id} 연결 시도 중...")
            print(f"서버 주소: {self.broker_host}:{self.broker_port}")
            self.client.connect(self.broker_host, self.broker_port, 60)
            self.client.loop_start()

            # 연결 완료 대기 (최대 15초)
            if self.connection_event.wait(timeout=15):
                if self.connected:
                    print(f"클라이언트 {self.client_id} 연결 성공!")
                    return True
                else:
                    print(f"클라이언트 {self.client_id} 연결 실패")
                    return False
            else:
                print("연결 시간 초과")
                return False
        except Exception as e:
            print(f"연결 실패: {e}")
            return False

    def disconnect(self):
        """서버에서 연결 해제"""
        self.running = False
        self.client.loop_stop()
        self.client.disconnect()

    def subscribe(self, topic, qos=0):
        """토픽 구독"""
        if self.connected:
            result = self.client.subscribe(topic, qos)
            print(f"토픽 {topic} 구독 요청")
            return result
        else:
            print("연결되지 않음")
            return None

    def unsubscribe(self, topic):
        """토픽 구독 해제"""
        if self.connected:
            result = self.client.unsubscribe(topic)
            print(f"토픽 {topic} 구독 해제 요청")
            return result
        else:
            print("연결되지 않음")
            return None

    def publish(self, topic, message, qos=0):
        """메시지 발행"""
        if self.connected:
            result = self.client.publish(topic, message, qos)
            print(f"토픽 {topic}에 메시지 발행: {message}")
            return result
        else:
            print("연결되지 않음")
            return None
# --- Remote MQTT Client Subscriber ---
def remote_subscriber_test():
    """원격 구독자 테스트"""

    server_ip = "192.168.0.162" # 라즈베리파이에서 서버를 열었을 때 사용
    subscriber = RemoteMQTTClient("remote_subscriber", server_ip, 8883)

    print("원격 구독자 테스트 시작...")

    if subscriber.connect():
        print("원격 구독자 연결 성공!")
        print("연결 상태:", subscriber.connected)

        # 토픽 구독
        topics = ["sensor/digital_value", "sensor/temperature", "camera/opt_flow", "camera/gamma", "camera/mode", "camera/wb"]
        for topic in topics:
            subscriber.subscribe(topic)

        print("구독 완료. 메시지 수신 대기 중입니다. (Ctrl+C로 종료)")
        print("메시지를 받으려면 다른 기기에서 발행자 테스트를 실행하세요.")

        button = Button(17, pull_up=False)

        try:
            while True:
                last_input = False
                publish_flag = False
                while True:
                    time.sleep(0.05)  # 폴링 간격을 짧게
                    current_input = button.is_pressed
                    if current_input == True and last_input == False and publish_flag == False:
                        # HIGH로 바뀐 순간에만 동작
                        value_to_publish = current_input  # 필요시 실제 값을 저장
                        remote_publisher_test()
                        publish_flag = True
                        # 퍼블리시 후, 입력이 LOW로 다시 내려갈 때까지 대기
                        while button.is_pressed == False:
                            time.sleep(0.05)
                    last_input = current_input
                    publish_flag = False
            print("\n사용자에 의해 구독이 중지되었습니다.")
        finally:
            subscriber.disconnect()
            print("원격 구독자 테스트 완료")
    else:
        print("원격 구독자 연결에 실패했습니다.")
        print("서버 IP 주소와 포트를 확인하세요.")
# --- Remote MQTT Client Publisher ---
def remote_publisher_test():
    """원격 발행자 테스트 (디지털 입력 값이 1일 때마다 퍼블리시, 반복 제한 없음)"""
    # 디지털 입력을 위한 GPIO 핀 설정
    # DIGITAL_PIN = 17  # 사용할 GPIO 핀 번호 (BCM 모드 기준, 필요시 변경)

    server_ip = "192.168.0.162" # 라즈베리파이에서 서버를 열었을 때 사용
    publisher = RemoteMQTTClient("remote_publisher", server_ip, 8883)

    # GPIO 설정
    # GPIO.setmode(GPIO.BCM)
    # GPIO.setup(DIGITAL_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    if publisher.connect():
        print("원격 발행자 연결 성공!")

        # 메시지 발행 토픽
        digital_topic = "sensor/digital_value"

        message = json.dumps({
        "command": "camera_on",
        "timestamp": time.time(),
        "source": "remote",
        "client_ip": publisher.local_ip
        })
        publisher.publish(digital_topic, message)
        time.sleep(2)

    else:
        print("원격 발행자 연결에 실패했습니다.")
        print("서버 IP 주소와 포트를 확인하세요.")
# --- Camera On ---

def camera_on(SCHEDULE_DURATION_SEC=None):
    """
    Picamera2 + GStreamer appsrc로 영상을 저장하고 RTSP를 송출합니다.
    ROI(Region of Interest)를 사용하여 해당 영역 내에서만 모션 감지를 수행합니다.
    ROI는 사각형 [x, y, w, h]으로 지정합니다.
    SCHEDULE_DURATION_SEC 값을 넣으면 해당 시간(초)만큼 동작하고, 값을 넣지 않으면 무한 동작합니다.
    """
    global file_vb_element, current_fps, current_bitrate, current_roi_poly, current_frame, file_gamma

    gi.require_version('Gst', '1.0') # GStreamer 사용을 위해 필요
    gi.require_version('GstApp', '1.0') # APPSRC 사용을 위해 필요
    
    # 전역 설정값 사용
    width, height = current_frame.split('x')
    framerate = current_fps
    bitrate = current_bitrate
    # duration: None이면 무한, 값이 있으면 해당 초만큼 동작
    duration = int(SCHEDULE_DURATION_SEC) if SCHEDULE_DURATION_SEC is not None else None
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # 출력 디렉터리
    output_dir = '/home/openiot/project/video'# '/home/openiot/project/video'#'/mnt/video'
    os.makedirs(output_dir, exist_ok=True)
    segment_index = 0
    # 세그먼트 원본 파일 + 세션 기준 시작/종료 시간(ns)
    segment_infos = []  # [{"path": str, "start_ns": int, "end_ns": Optional[int]}]
    
    # Picamera2 구성 (RGB 포맷으로 사용)
    picam2 = Picamera2()
    cfg = picam2.create_video_configuration(
        main={'size': (int(width), int(height)), 'format': 'RGB888'}
    )
    # 카메라 세션 시작
    picam2.configure(cfg)
    picam2.set_controls({"FrameRate": current_fps})
    picam2.set_controls({"AfMode": 2})   # 0=Manual, 1=Auto, 2=Continuous
    picam2.start()
    # RTSP 서버를 유지하고, 존재하지 않을 때만 생성 (스케일링으로 출력만 조정)
    ensure_rtsp_server(int(width), int(height), framerate, bitrate // 1000)
    # HLS 서버/파이프라인 시작 (옵션)
    try:
        start_hls_http_server()
        start_hls_pipeline(int(width), int(height), int(framerate), int(bitrate // 1000))
    except Exception as e:
        print(f"[HLS] 시작 실패(무시): {e}")
    bitrate_kbps = bitrate // 1000

    # 1) RTSP 서버 구성 (GstRtspServer) - 클라이언트 RTSP 서버에 접속 시에만 활성화됨 - 설정한 파이프라인 사용
    # ensure_rtsp_server에서 설정됨

    # 2) 파일 저장 파이프라인: 모션 발생 시에만 동적으로 생성/종료
    file_pipeline = None
    file_appsrc = None
    segment_open = False
    segment_start_ns = 0

    # LED은 녹화 중에만 ON
    LED_PIN.off()
    # 프레임을 appsrc로 푸시
    session_start_time = time.time()
    begin_session_timeline(session_start_time)
    try:
        camera_stop_event.clear()
    except Exception:
        pass
    frame_duration_ns = int(1 / framerate * 1e9)
    session_postprocess = postprocess_after_capture
    prev_gray = None
    last_motion_time = 0.0
    # 세션 시작과 동시에 파일 저장 파이프라인 오픈
    # 스케줄 모드(비-OF)에서는 1분(60초) 단위로 분할 저장 후 병합
    schedule_segment_length_sec = 60
    output_file_h264 = (
        os.path.join(output_dir, f'video_{timestamp}_seg{segment_index:03d}_raw.mp4')
        if not of_enabled else os.path.join(output_dir, f'video_{timestamp}_raw.mp4')
    )
    if os.path.exists(output_file_h264):
        try:
            os.remove(output_file_h264)
        except Exception:
            pass
    # 감마는 RGB 색공간에서만 적용되므로 RGB 구간을 삽입한 뒤 재변환
    file_pipeline_str = (
        f"appsrc name=file_src is-live=true format=time do-timestamp=true block=true "
        f"caps=video/x-raw,format=I420,width={current_frame.split('x')[0]},height={current_frame.split('x')[1]},framerate={current_fps}/1 ! "
        f"queue max-size-buffers=0 max-size-time=0 max-size-bytes=0 ! "
        f"videoconvert ! video/x-raw,format=RGB ! "
        f"gamma name=gamma gamma={current_gamma} ! "
        f"videobalance name=file_vb ! "
        f"videoconvert ! video/x-raw,format=I420 ! "
        f"videorate ! video/x-raw,width={current_frame.split('x')[0]},height={current_frame.split('x')[1]},framerate={current_fps}/1 !"
        f"x264enc sliced-threads=true quantizer=20 pass=qual qp-min=20 qp-max=40 key-int-max=60 speed-preset=ultrafast ! h264parse ! mp4mux faststart=true ! filesink name=file_sink location={output_file_h264} sync=false"
    )
    file_pipeline = Gst.parse_launch(file_pipeline_str)
    file_gamma = file_pipeline.get_by_name('gamma')  # 일단은 초기에 설정을 하면 반영됨
    # 파일 파이프라인용 videobalance 참조 보관 (gray/rgb 모드 전환용)
    try:
        file_vb_element = file_pipeline.get_by_name('file_vb')
        if file_vb_element is not None:
            try:
                file_vb_element.set_property('saturation', 0.0 if current_mode == 'gray' else 1.0)
            except Exception:
                pass
    except Exception:
        pass
    # print(f"current_gamma: {current_gamma}")
    # if file_gamma is not None:
    #     file_gamma.set_property('gamma', current_gamma)
    file_appsrc = file_pipeline.get_by_name('file_src')
    try:
        globals()['file_x264_element'] = file_pipeline.get_by_name('file_x264')
    except Exception:
        globals()['file_x264_element'] = None
    # no dynamic file scale caps element anymore
    # OF 모드에서는 시작 시 파일 파이프라인을 열지 않음(모션 발생 시 오픈)
    if of_enabled:
        segment_open = False
        segment_start_ns = 0
    else:
        file_pipeline.set_state(Gst.State.PLAYING)
        segment_open = True
        segment_start_ns = 0
        segment_infos.append({"path": output_file_h264, "start_ns": int(segment_start_ns), "end_ns": None})
        LED_PIN.on()
    # --- Optical Flow 설정 및 상태 ---
    of_target_width = 640
    of_interval_frames = 1
    of_redetect_interval = 30
    of_max_corners = 200
    of_quality_level = 0.003
    of_min_distance = 7
    of_block_size = 3
    of_win_size = (25, 25)
    of_max_level = 2
    of_term_criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
    of_min_mag = 1.0
    of_fb_thresh = 4.0 # 1.5
    of_min_moving_pts = 8
    of_idle_timeout = 2.0  # 모션이 사라진 뒤 이 시간(초) 지나면 세그먼트 종료
    # 오버레이 옵션 (RTSP 프레임에 벡터 표시)
    of_draw_overlay = True
    of_thickness = 3
    of_color = (255, 255, 255)
    of_alpha = 0.6
    # 오버레이용 벡터/스케일 버퍼
    of_last_vectors = None  # [(x1, y1, x0, y0), ...]
    of_last_scale = (1.0, 1.0)
    prev_gray_small = None
    prev_pts = None
    prev_motion_mask_small = None
    since_redetect = 0
    frame_idx_of = 0
    motion = False
    detection_segments: list[str] = []
    merged_index = 0

    # --- ROI: 사각형만 사용 ---
    try:
        # 모드별 종료 조건: OF 모드 → camera_off 수신 전까지, 스케줄 모드 → duration까지
        camera_stop_event.clear()
        # HLS PTS 누적
        hls_pts_ns = 0
        while True:
            # duration이 지정된 경우, 해당 시간(초)만큼만 동작
            if duration is not None:
                if (time.time() - session_start_time) >= duration:
                    print(f"[카메라] 지정된 동작 시간({duration}초) 경과, 세션 종료")
                    break
            # t_loop0 = time.time()
            # t0 = time.time()
            frame = picam2.capture_array()  # RGB 포맷 (caps와 일치)
            # t1 = time.time()
            # 종료 조건: 외부 stop 이벤트로만 제어
            if camera_stop_event.is_set():
                break
            # 최신 프레임 보관 (수동 녹화/HLS/QR 용)
            try:
                globals()['camera_frame'] = frame.copy()
            except Exception:
                pass

            # QR 인식 및 처리 (쿨다운)
            try:
                now_t = time.time()
                results = detect_qr_codes_enhanced(frame)
                for res in results:
                    data = res.get('data') if isinstance(res, dict) else None
                    if not data:
                        continue
                    if (data != last_qr_data) or (now_t - qr_detection_time > cooldown_period):
                        try:
                            qr_json = json.loads(data)
                            if 'endpoint' in qr_json:
                                endpoint_url = qr_json['endpoint']
                                threading.Thread(target=send_pairing_request, args=(endpoint_url,), daemon=True).start()
                            else:
                                print("[QR] endpoint 없음")
                        except json.JSONDecodeError:
                            server_info = parse_server_info(data)
                            if server_info:
                                threading.Thread(target=send_commission_request, args=(server_info,), daemon=True).start()
                        except Exception:
                            pass
                        globals()['last_qr_data'] = data
                        globals()['qr_detection_time'] = now_t
            except Exception:
                pass

            # 수동 녹화 프레임 쓰기
            try:
                if manual_recording:
                    write_frame_to_manual_recording(frame)
            except Exception:
                pass

            if of_enabled:
                # ROI 사각형 추출 (프레임 경계 안전 클램프)
                h_total, w_total = frame.shape[:2]
                # 기본값: 전체 프레임
                x, y, w, h = 0, 0, w_total, h_total
                try:
                    if isinstance(current_roi, (list, tuple)) and len(current_roi) == 4:
                        rx, ry, rw, rh = [int(v) for v in current_roi]
                        rx = max(0, min(rx, w_total - 1))
                        ry = max(0, min(ry, h_total - 1))
                        rw = max(1, min(int(rw), w_total - rx))
                        rh = max(1, min(int(rh), h_total - ry))
                        x, y, w, h = rx, ry, rw, rh
                        print(f"Rectangle ROI 사용: {(x, y, w, h)}")
                    else:
                        print("Rectangle ROI 미설정: 전체 프레임 사용")
                except Exception:
                    print("Rectangle ROI 파싱 실패: 전체 프레임 사용")

                # 모션 감지 (RGB -> GRAY)
                # 입력이 RGB라고 가정하여 RGB2GRAY 사용 (예외 시 BGR2GRAY fallback)
                try:
                    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
                except Exception:
                    print("RGB2GRAY 변환 false")
                    gray = frame

                # 전체 프레임 기준 사각형 ROI 마스크 생성/적용
                full_mask = build_roi_mask_full((gray.shape[0], gray.shape[1]), (x, y, w, h), None)
                if full_mask is not None:
                    roi_gray = cv2.bitwise_and(gray, gray, mask=full_mask)
                else:
                    roi_gray = gray

                # prev_gray도 ROI로 맞춰서 관리
                if prev_gray is not None and full_mask is not None:
                    prev_gray_roi = cv2.bitwise_and(prev_gray, prev_gray, mask=full_mask)
                else:
                    prev_gray_roi = prev_gray

                # --- 옵티컬 플로우 기반 모션 감지 ---
                # 1) 다운스케일 그레이/마스크 준비
                h_total, w_total = gray.shape[:2]
                if w_total != of_target_width:
                    scale = of_target_width / float(w_total)
                    new_w = int(w_total * scale)
                    new_h = int(h_total * scale)
                    gray_small = cv2.resize(roi_gray if full_mask is not None else gray, (new_w, new_h), interpolation=cv2.INTER_AREA)
                    roi_mask_small = cv2.resize(full_mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST) if full_mask is not None else None
                else:
                    gray_small = roi_gray if full_mask is not None else gray
                    roi_mask_small = full_mask

                frame_idx_of += 1
                do_flow = (of_interval_frames <= 1) or (frame_idx_of % of_interval_frames == 0)
                # 옵티컬 플로우 계산
                if do_flow:
                    if prev_gray_small is None or prev_pts is None or since_redetect >= of_redetect_interval:
                        prev_pts = (
                            cv2.goodFeaturesToTrack(
                                gray_small,
                                maxCorners=of_max_corners,
                                qualityLevel=of_quality_level,
                                minDistance=of_min_distance,
                                blockSize=of_block_size,
                                mask=roi_mask_small,
                            )
                        )
                        prev_gray_small = gray_small
                        prev_motion_mask_small = roi_mask_small
                        since_redetect = 0
                        # 첫 프레임은 모션 판정하지 않음
                        motion = False
                    else:
                        motion = False
                        if prev_pts is not None and len(prev_pts) > 0:
                            prev_pts_f32 = prev_pts.astype(np.float32)
                            next_pts, status, err = cv2.calcOpticalFlowPyrLK(
                                prev_gray_small, gray_small, prev_pts_f32, None,
                                winSize=of_win_size,
                                maxLevel=of_max_level,
                                criteria=of_term_criteria,
                            )
                            if next_pts is not None and status is not None:
                                status_flat = status.ravel()
                                good_new = next_pts[status_flat == 1]
                                good_old = prev_pts_f32[status_flat == 1]

                                # Forward-Backward 체크
                                if good_new is not None and len(good_new) > 0:
                                    back_pts, back_status, _ = cv2.calcOpticalFlowPyrLK(
                                        gray_small, prev_gray_small, good_new.reshape(-1, 1, 2).astype(np.float32), None,
                                        winSize=of_win_size,
                                        maxLevel=of_max_level,
                                        criteria=of_term_criteria,
                                    )
                                    if back_pts is not None and back_status is not None:
                                        back_status = back_status.ravel()
                                        back_pts = np.reshape(back_pts, (-1, 2))
                                        fb_err = np.linalg.norm(np.reshape(good_old, (-1, 2)) - back_pts, axis=1)
                                        fb_mask = fb_err <= of_fb_thresh
                                        good_new = np.reshape(good_new, (-1, 2))[fb_mask]
                                        good_old = np.reshape(good_old, (-1, 2))[fb_mask]

                                # 이동량 기반 모션 판정
                                if good_new is not None and len(good_new) > 0:
                                    disp = good_new - good_old
                                    mag = np.linalg.norm(disp, axis=1)
                                    moving_count = int(np.sum(mag >= of_min_mag))
                                    motion = moving_count >= of_min_moving_pts
                                    # 오버레이용 벡터 저장 (다운스케일 좌표 기준)
                                    try:
                                        if of_draw_overlay:
                                            vectors = []
                                            for (npt, opt) in zip(good_new, good_old):
                                                a, b = float(npt[0]), float(npt[1])
                                                c, d = float(opt[0]), float(opt[1])
                                                if np.hypot(a - c, b - d) >= of_min_mag:
                                                    vectors.append((a, b, c, d))
                                            # 다운스케일→원본 스케일
                                            sx = float(w_total) / float(gray_small.shape[1]) if gray_small.shape[1] > 0 else 1.0
                                            sy = float(h_total) / float(gray_small.shape[0]) if gray_small.shape[0] > 0 else 1.0
                                            of_last_vectors = vectors
                                            of_last_scale = (sx, sy)
                                    except Exception:
                                        pass
                                else:
                                    of_last_vectors = None

                                # 상태 업데이트
                                prev_gray_small = gray_small
                                if good_new is not None and len(good_new) > 0:
                                    prev_pts = good_new.reshape(-1, 1, 2)
                                else:
                                    prev_pts = None
                                prev_motion_mask_small = roi_mask_small
                                since_redetect += 1
                            else:
                                # 추적 실패 → 재검출
                                prev_pts = cv2.goodFeaturesToTrack(
                                    gray_small,
                                    maxCorners=of_max_corners,
                                    qualityLevel=of_quality_level,
                                    minDistance=of_min_distance,
                                    blockSize=of_block_size,
                                    mask=roi_mask_small,
                                )
                                prev_gray_small = gray_small
                                prev_motion_mask_small = roi_mask_small
                                since_redetect = 0
                                of_last_vectors = None

                # 프레임 차이 로직 사용 안 함: prev_gray는 유지만 함
                if prev_gray is None:
                    prev_gray = gray.copy()
                else:
                    prev_gray = gray
            else:
                print("of_enabled false only raw file")

            now_ns = int((time.time() - session_start_time) * 1e9)
            if motion:
                # 모션 발생: 시간 갱신 및 OF 모드에서 세그먼트 오픈
                last_motion_time = time.time()
                if of_enabled and not segment_open:
                    # 새 세그먼트 파일명
                    print("motion detected")
                    output_file_h264 = os.path.join(output_dir, f'video_{timestamp}_seg{segment_index:03d}_raw.mp4')
                    # 기존 파일 삭제 없이 진행
                    # 파이프라인을 반복문에서 새로 만들지 않고, 이미 생성된 file_pipeline을 재활용
                    # 단, 파일 경로만 동적으로 변경 (filesink location property만 변경)
                    # 기존 파일 삭제 없이 진행
                    try:
                        # 파이프라인 정지
                        file_pipeline.set_state(Gst.State.NULL)
                        # filesink location만 변경 + caps(해상도/프레임레이트) 반영
                        filesink = file_pipeline.get_by_name('file_sink') or file_pipeline.get_by_name('filesink0') or file_pipeline.get_by_name('filesink')
                        if filesink is not None:
                            filesink.set_property('location', output_file_h264)
                        # no dynamic file caps update; next session will apply current_frame
                        # gamma 값도 필요시 갱신
                        file_gamma = file_pipeline.get_by_name('gamma')
                        if file_gamma is not None:
                            try:
                                file_gamma.set_property('gamma', current_gamma)
                            except Exception:
                                pass
                        # appsrc 재참조
                        file_appsrc = file_pipeline.get_by_name('file_src')
                        # 파이프라인 재시작
                        file_pipeline.set_state(Gst.State.PLAYING)
                        segment_open = True
                        segment_start_ns = int((time.time() - session_start_time) * 1e9)
                        segment_infos.append({"path": output_file_h264, "start_ns": int(segment_start_ns), "end_ns": None})
                        segment_index += 1
                        LED_PIN.on()
                    except Exception:
                        pass

            # 파일 파이프라인(appsrc)은 I420를 기대하므로, RGB에서 간단 WB 적용 후 I420로 변환
            try:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                if str(current_wb).lower() == 'auto':
                    frame_rgb = apply_simple_wb_rgb(frame_rgb)
                i420 = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2YUV_I420)
                data = i420.tobytes()
            except Exception:
                # 변환 실패 시 안전하게 기존 RGB로 대체(파이프라인 caps와 불일치 가능)
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                data = frame_rgb.tobytes()
            buf = Gst.Buffer.new_allocate(None, len(data), None)
            buf.fill(0, data)
            # 병합 중에는 파일 저장을 중단하고, 아니면 세그먼트가 열려 있을 때에만 파일 파이프라인으로 푸시
            if (not merge_in_progress) and segment_open and file_appsrc is not None:
                pts = max(0, now_ns - segment_start_ns)
                buf.pts = pts
                buf.duration = frame_duration_ns
                file_appsrc.emit('push-buffer', buf)

            # HLS로 프레임 푸시 (항상)
            try:
                if hls_appsrc is not None:
                    hls_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    hls_bytes = hls_rgb.tobytes()
                    hls_buf = Gst.Buffer.new_allocate(None, len(hls_bytes), None)
                    hls_buf.fill(0, hls_bytes)
                    hls_buf.pts = int(hls_pts_ns)
                    hls_buf.duration = frame_duration_ns
                    hls_appsrc.emit('push-buffer', hls_buf)
                    hls_pts_ns += frame_duration_ns
            except Exception:
                pass

            # 스케줄 모드: 1분 경과 시 세그먼트 로테이션 (OF 모드 아님)
            if (not of_enabled) and segment_open:
                try:
                    if (now_ns - int(segment_start_ns)) >= int(schedule_segment_length_sec * 1e9):
                        # 현재 세그먼트 종료
                        try:
                            file_appsrc.emit('end-of-stream')
                            bus_file = file_pipeline.get_bus()
                            bus_file.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS)
                        except Exception:
                            pass
                        try:
                            file_pipeline.set_state(Gst.State.NULL)
                        except Exception:
                            pass
                        # 종료 시간 기록 및 병합 후보 추가
                        try:
                            if len(segment_infos) > 0 and segment_infos[-1].get("end_ns") is None:
                                segment_infos[-1]["end_ns"] = int(now_ns)
                        except Exception:
                            pass

                        # 다음 세그먼트 준비
                        output_file_h264 = os.path.join(output_dir, f'video_{timestamp}_seg{segment_index+1:03d}_raw.mp4')
                        try:
                            filesink = file_pipeline.get_by_name('file_sink') or file_pipeline.get_by_name('filesink0') or file_pipeline.get_by_name('filesink')
                            if filesink is not None:
                                filesink.set_property('location', output_file_h264)
                            file_gamma = file_pipeline.get_by_name('gamma')
                            if file_gamma is not None:
                                try:
                                    file_gamma.set_property('gamma', current_gamma)
                                except Exception:
                                    pass
                            file_appsrc = file_pipeline.get_by_name('file_src')
                            file_pipeline.set_state(Gst.State.PLAYING)
                            segment_open = True
                            segment_start_ns = int(now_ns)
                            segment_index += 1
                            segment_infos.append({"path": output_file_h264, "start_ns": int(segment_start_ns), "end_ns": None})
                            LED_PIN.on()
                        except Exception:
                            pass
                except Exception:
                    pass
            # t2 = time.time()
            # OF 모드: 모션 idle 시 세그먼트 종료 및 3회 병합 처리
            if of_enabled and segment_open and (time.time() - last_motion_time) > float(of_idle_timeout):
                try:
                    file_appsrc.emit('end-of-stream')
                    bus_file = file_pipeline.get_bus()
                    bus_file.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS)
                except Exception:
                    pass
                try:
                    file_pipeline.set_state(Gst.State.NULL)
                except Exception:
                    pass
                segment_open = False
                try:
                    if len(segment_infos) > 0 and segment_infos[-1].get("end_ns") is None:
                        segment_infos[-1]["end_ns"] = int((time.time() - session_start_time) * 1e9)
                        # 막 닫힌 세그먼트 경로를 병합 후보에 추가
                        detection_segments.append(segment_infos[-1]["path"])
                        try:
                            with global_detection_segments_lock:
                                global_detection_segments.append(segment_infos[-1]["path"])
                        except Exception:
                            pass
                except Exception:
                    pass
                LED_PIN.off()
                # 실시간 병합은 수행하지 않음. 세션 종료 시 한 번에 병합.
                # (세그먼트 경로는 detection_segments에 누적)
            # print(
            #     f"capture: {(t1 - t0)*1000:.2f} ms, "
            #     f"loop: {(t1 - t_loop0)*1000:.2f} ms"
            # )
            # RTSP appsrc 준비 시 푸시: 스케줄 모드에서는 항상 송출, 모션 감지 모드에서는 모션이 없을 때만 송출
            if rtsp_appsrc_ref["appsrc"] is not None and ((not of_enabled and not segment_open) or (of_enabled and not motion)):
                try:
                    rtsp_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    # 간단 화이트 밸런스 (auto일 때만)
                    if str(current_wb).lower() == 'auto':
                        rtsp_frame = apply_simple_wb_rgb(rtsp_frame)
                    # gamma는 RTSP 파이프라인 요소(rtsp_gamma)가 적용. 여기서는 중복 적용 안 함
                    rtsp_data = rtsp_frame.tobytes()
                    rtsp_buf = Gst.Buffer.new_allocate(None, len(rtsp_data), None)
                    rtsp_buf.fill(0, rtsp_data)
                    # rtsp_pts_ns = int((time.time() - session_start_time) * 1e9)
                    # rtsp_buf.pts = rtsp_pts_ns
                    # rtsp_buf.dts = rtsp_pts_ns
                    rtsp_buf.duration = frame_duration_ns
                    rtsp_appsrc_ref["appsrc"].emit('push-buffer', rtsp_buf)
                    # 스트리밍 상태 로그 (5초 간격)
                    try:
                        now_t = time.time()
                        if now_t - globals().get('rtsp_last_stream_log_time', 0.0) >= 5.0:
                            print("스트리밍 중")
                            globals()['rtsp_last_stream_log_time'] = now_t
                    except Exception:
                        pass
                except Exception:
                    pass
        # 남아있는 세그먼트 정리
        if segment_open and file_appsrc is not None and file_pipeline is not None:
            try:
                file_appsrc.emit('end-of-stream')
                bus_file = file_pipeline.get_bus()
                bus_file.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS)
            except Exception:
                pass
            # 세그먼트 종료 시간 기록
            try:
                if len(segment_infos) > 0 and segment_infos[-1].get("end_ns") is None:
                    segment_infos[-1]["end_ns"] = int((time.time() - session_start_time) * 1e9)
                    # 병합 대상에 마지막 세그먼트 추가
                    try:
                        detection_segments.append(segment_infos[-1]["path"])
                        try:
                            with global_detection_segments_lock:
                                global_detection_segments.append(segment_infos[-1]["path"])
                        except Exception:
                            pass
                    except Exception:
                        pass
            except Exception:
                pass
        if rtsp_appsrc_ref["appsrc"] is not None:
            try:
                rtsp_appsrc_ref["appsrc"].emit('end-of-stream')
            except Exception:
                pass
    finally:
        # 정리 및 스레드 상태 초기화
        if file_pipeline is not None:
            try:
                file_pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
        LED_PIN.off()
        try:
            picam2.stop()
        except Exception:
            pass
        picam2.close()
        # HLS는 항상 켜짐: 세션 종료 시에도 유지
        if len(segment_infos) > 0:
            print(f'▶ 세션 종료: {len(segment_infos)}개 세그먼트 저장 완료, RTSP: rtsp://127.0.0.1:8554{rtsp_path}')
        else:
            print(f'▶ 세션 종료: 저장된 세그먼트 없음, RTSP: rtsp://127.0.0.1:8554{rtsp_path}')
        # 다음 호출을 위해 스레드 포인터 초기화
        global camera_thread
        camera_thread = None
        # 후처리 모드: 저장된 원본 영상에 대해 오프라인 처리 수행
        try:
            if len(segment_infos) > 0 and not scheduled_merge_initiated:
                # 세션 종료 시, 분할 생성된 세그먼트를 하나의 merge_raw로 병합
                try:
                    seg_pattern = re.compile(r"_seg\d+_raw\.mp4$")
                    # 스케줄 모드/모션 모드 공통 병합: segment_infos 기준으로 수집
                    segments_to_merge = [s["path"] for s in segment_infos if isinstance(s, dict) and os.path.isfile(s.get("path", "")) and seg_pattern.search(os.path.basename(s.get("path", "")))]
                    merged_success = False
                    if segments_to_merge:
                        # 날짜와 시간을 파일명에 추가
                        now = datetime.now(ZoneInfo("Asia/Seoul"))
                        now_str = now.strftime("%Y%m%d_%H%M%S")
                        merged_raw = os.path.join(output_dir, f'video_{now_str}_merge_file.mp4')
                        try:
                            if os.path.exists(merged_raw):
                                os.remove(merged_raw)
                        except Exception:
                            pass
                        # concat 시도 후 실패 시 디코드/재인코딩 병합으로 폴백
                        if not ffmpeg_concat_mp4(segments_to_merge, merged_raw):
                            render_merged_raw_from_segments(segments_to_merge, merged_raw)
                        merged_success = True
                        print(f"[병합] 최종 merge_raw 생성: {merged_raw} (세그먼트 {len(segments_to_merge)}개)")
                    else:
                        print("[병합] 병합할 세그먼트가 없습니다.")
                except Exception as e_merge:
                    print(f"[병합] 실패: {e_merge}")

                # # FPS 측정: 개별 파일만 대상으로 수행
                # try:
                #     for p in [s["path"] for s in segment_infos if os.path.isfile(s["path"])]:
                #         measure_file_fps_gst(p)
                # except Exception:
                #     pass

                # 병합 성공 시 병합에 사용된 세그먼트 원본 삭제
                try:
                    if 'merged_success' in locals() and merged_success and 'segments_to_merge' in locals():
                        for p in segments_to_merge:
                            try:
                                if os.path.isfile(p):
                                    os.remove(p)
                                    print(f"[정리] 세그먼트 삭제: {p}")
                            except Exception as e_del:
                                print(f"[정리] 세그먼트 삭제 실패: {p}, {e_del}")
                except Exception:
                    pass
        except Exception as e:
            print(f'[후처리] 실패: {e}')
        finally:
            end_session_timeline()
# --- Main ---
def main():
    # GPIO 및 카메라 초기화
    # GPIO.setmode(GPIO.BCM)
    # GPIO.setup(LED_PIN, GPIO.OUT)
    LED_PIN.off()  # 시작 시 LED 꺼짐
    # 마지막 모드 로드 (재부팅 후에도 유지)
    load_last_mode_from_disk()

    # 프로그램 시작 시 HLS 항상 켜기
    try:
        w, h = [int(v) for v in str(current_frame).split('x')]
    except Exception:
        w, h = 1280, 720
    try:
        start_hls_http_server()
        start_hls_pipeline(int(w), int(h), int(current_fps), int(current_bitrate // 1000))
        print('[BOOT] HLS 항상 켜짐: 서버/파이프라인 시작')
    except Exception as e:
        print(f"[BOOT] HLS 시작 실패(무시): {e}")

    # 모션 감지 모드일 경우, 프로그램 시작 시 즉시 카메라 세션 시작
    try:
        global camera_thread
        if of_enabled:
            if camera_thread is None or not camera_thread.is_alive():
                camera_thread = threading.Thread(target=lambda: camera_on(), daemon=True)
                camera_thread.start()
    except Exception:
        pass

    # 스케줄러(스케줄 모드 전용): 매일 KST 지정 시각에 60초 촬영
    def schedule_mode():
        global SCHEDULE_MODE_HOUR, SCHEDULE_MODE_MINUTE, SCHEDULE_DAYS, SCHEDULE_DURATION_SEC
        tz = None
        try:
            if ZoneInfo is not None:
                tz = ZoneInfo('Asia/Seoul')
        except Exception:
            tz = None
        while True:
            try:
                # target 재계산 루프: 이벤트 기반으로 즉시 깨어남
                reached_target = False
                while True:
                    now = datetime.now(tz if tz else None)
                    # 오늘/다음 허용 요일 중 가장 가까운 target 찾기
                    base_target = now.replace(hour=SCHEDULE_MODE_HOUR, minute=SCHEDULE_MODE_MINUTE, second=0, microsecond=0)
                    candidate = base_target if now < base_target else base_target + timedelta(days=1)
                    # 허용 요일에 맞춰 앞으로 이동
                    try:
                        allowed = set(SCHEDULE_DAYS) if isinstance(SCHEDULE_DAYS, (list, tuple, set)) else set([0,1,2,3,4,5,6])
                    except Exception:
                        allowed = set([0,1,2,3,4,5,6])
                    safety = 0
                    while candidate.weekday() not in allowed and safety < 14:
                        candidate = candidate + timedelta(days=1)
                        safety += 1
                    target = candidate
                    wait_seconds = max(0.0, (target - now).total_seconds())

                    # 타깃까지 1초 단위 대기하되, 중간에 업데이트 이벤트 발생 시 즉시 재계산
                    slept = 0.0
                    while slept < wait_seconds:
                        if schedule_update_event.is_set():
                            print("[스케줄] 새 시간 설정 감지 → target 재계산")
                            schedule_update_event.clear()
                            break
                        # 기존 wake 이벤트도 겸용 지원
                        if schedule_wake_event.is_set():
                            schedule_wake_event.clear()
                            break
                        time.sleep(1.0)
                        slept += 1.0
                    else:
                        # 정상적으로 목표 시각 도달
                        reached_target = True
                        break
                    # 이벤트로 깨어난 경우: 즉시 재계산으로 루프 계속
                    if slept < wait_seconds:
                        continue
                if not reached_target:
                    # 방어적 체크: 도달 플래그가 없으면 다음 루프로
                    continue
                # 시간 도달: 60초 촬영 트리거 (스케줄 모드 전용) - 예약 시간에는 무조건 동작
                global camera_thread, of_enabled
                if not of_enabled:
                    # 기존 세션이 살아있으면 먼저 종료 요청 후 대기
                    try:
                        if camera_thread is not None and camera_thread.is_alive():
                            print('[스케줄] 예약 시간 도달: 기존 세션 종료 요청')
                            camera_stop_event.set()
                            waited = 0.0
                            while camera_thread.is_alive() and waited < 10.0:
                                time.sleep(0.2)
                                waited += 0.2
                    except Exception:
                        pass
                    # 새 세션 시작
                    try:
                        camera_stop_event.clear()
                    except Exception:
                        pass
                    duration_to_use = int(SCHEDULE_DURATION_SEC) 
                    camera_thread = threading.Thread(target=lambda: camera_on(duration_to_use), daemon=True)
                    camera_thread.start()
                    # 60초 후 자동 종료
                    def _stop_after(duration_sec: int):
                        try:
                            time.sleep(int(duration_sec))
                            camera_stop_event.set()
                            print(f"[스케줄] {int(duration_sec)}초 경과: 세션 종료 요청")
                        except Exception:
                            pass
                    threading.Thread(target=lambda: _stop_after(duration_to_use), daemon=True).start()
            except Exception as e:
                try:
                    print(f"[스케줄러] 오류: {e}")
                except Exception:
                    pass
                time.sleep(5)

    # 스케줄러(모션 감지 모드 전용): 매일 KST 지정 시각에 세그먼트 병합 및 업로드 로그
    def motion_mode():
        global MOTION_MODE_HOUR, MOTION_MODE_MINUTE, MOTION_DAYS
        tz = None
        try:
            if ZoneInfo is not None:
                tz = ZoneInfo('Asia/Seoul')
        except Exception:
            tz = None
        while True:
            try:
                # 예약 시각을 매초 재평가하여 MQTT 갱신이 즉시 반영되도록 함
                reached_target = False
                while True:
                    now = datetime.now(tz if tz else None)
                    base_target = now.replace(hour=MOTION_MODE_HOUR, minute=MOTION_MODE_MINUTE, second=0, microsecond=0)
                    candidate = base_target if now < base_target else base_target + timedelta(days=1)
                    try:
                        allowed = set(MOTION_DAYS) if isinstance(MOTION_DAYS, (list, tuple, set)) else set([0,1,2,3,4,5,6])
                    except Exception:
                        allowed = set([0,1,2,3,4,5,6])
                    safety = 0
                    while candidate.weekday() not in allowed and safety < 14:
                        candidate = candidate + timedelta(days=1)
                        safety += 1
                    target = candidate
                    wait_seconds = max(0.0, (target - now).total_seconds())

                    # 타깃까지 1초 단위 대기하되, 중간에 업데이트 이벤트 발생 시 즉시 재계산
                    slept = 0.0
                    while slept < wait_seconds:
                        if motion_update_event.is_set():
                            print("[모션] 새 시간 설정 감지 → target 재계산")
                            motion_update_event.clear()
                            break
                        # 기존 wake 이벤트도 겸용 지원
                        if motion_wake_event.is_set():
                            motion_wake_event.clear()
                            break
                        time.sleep(1.0)
                        slept += 1.0
                    else:
                        # 정상적으로 목표 시각 도달
                        reached_target = True
                        break
                    # 이벤트로 깨어난 경우: 즉시 재계산으로 루프 계속
                    if slept < wait_seconds:
                        continue
                if not reached_target:
                    continue
                # 시간 도달: 모션 감지 모드일 때만 병합 수행
                global of_enabled, merge_in_progress, scheduled_merge_initiated, camera_thread
                if of_enabled:
                    try:
                        # 1) 카메라 정지 요청
                        scheduled_merge_initiated = True
                        try:
                            camera_stop_event.set()
                        except Exception:
                            pass
                        # 2) 세션 종료 대기 (최대 15초)
                        try:
                            if camera_thread is not None and camera_thread.is_alive():
                                waited = 0.0
                                while camera_thread.is_alive() and waited < 15.0:
                                    time.sleep(0.2)
                                    waited += 0.2
                        except Exception:
                            pass

                        # 3) 병합만 수행 (저장 중단 보장)
                        merge_in_progress = True
                        with global_detection_segments_lock:
                            seg_pattern = re.compile(r"_seg\d+_raw\.mp4$")
                            segments_to_merge = [p for p in global_detection_segments if isinstance(p, str) and os.path.isfile(p) and seg_pattern.search(os.path.basename(p))]
                            global_detection_segments.clear()
                        if segments_to_merge:
                            now_str = time.strftime("%Y%m%d_%H%M%S")
                            output_dir = '/home/openiot/project/video'# '/home/openiot/project/video'#'/mnt/video'
                            os.makedirs(output_dir, exist_ok=True)
                            merged_raw = os.path.join(output_dir, f'video_{now_str}_merge_raw.mp4')
                            try:
                                if os.path.exists(merged_raw):
                                    os.remove(merged_raw)
                            except Exception:
                                pass
                            if not ffmpeg_concat_mp4(segments_to_merge, merged_raw):
                                render_merged_raw_from_segments(segments_to_merge, merged_raw)
                            print("영상 병합 성공")
                            print("sFTP 서버로 영상 전송 진행 중")
                            # 병합 완료 후, 사용된 세그먼트 원본 삭제
                            try:
                                for seg_path in segments_to_merge:
                                    try:
                                        if isinstance(seg_path, str) and os.path.isfile(seg_path):
                                            os.remove(seg_path)
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                        # 4) 병합 후 재시작: 모션 모드일 때만 재시작 (스케줄 모드 전환 시 재시작 금지)
                        try:
                            if of_enabled:
                                if camera_thread is None or not camera_thread.is_alive():
                                    camera_stop_event.clear()
                                    camera_thread = threading.Thread(target=lambda: camera_on(), daemon=True)
                                    camera_thread.start()
                                    print("[스케줄러-모션-병합] 병합 후 카메라 재시작 완료")
                            else:
                                print("[스케줄러-모션-병합] 모션 모드 아님 → 병합 후 재시작 생략")
                        except Exception as e:
                            print(f"[스케줄러-모션-병합] 병합 후 재시작 오류: {e}")
                    except Exception as e:
                        try:
                            print(f"[스케줄러-모션-병합] 오류: {e}")
                        except Exception:
                            pass
                    finally:
                        merge_in_progress = False
                        scheduled_merge_initiated = False
            except Exception as e:
                try:
                    print(f"[스케줄러] 오류: {e}")
                except Exception:
                    pass
                time.sleep(5)

    globals()['scheduler_thread_schedule'] = threading.Thread(target=schedule_mode, daemon=True)
    scheduler_thread_schedule.start()
    globals()['scheduler_thread_motion'] = threading.Thread(target=motion_mode, daemon=True)
    scheduler_thread_motion.start()

    # 기존 원격 구독 루프 시작
    remote_subscriber_test()


if __name__ == "__main__":
    main()