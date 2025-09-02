#!/usr/bin/env python3
"""
웹 기반 카메라 스트리밍 및 QR 코드 인식 시스템
Flask를 사용하여 웹 브라우저에서 카메라 화면을 실시간으로 확인할 수 있습니다.
"""

import cv2
import requests
import json
import socket
import re
import numpy as np
from pyzbar import pyzbar
import time
import threading
from picamera2 import Picamera2
from flask import Flask, render_template, Response, jsonify
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import base64
from io import BytesIO
from PIL import Image
import os
import uuid
import subprocess
from datetime import datetime
import paho.mqtt.client as mqtt

app = Flask(__name__)

# 전역 변수
camera_frame = None
qr_detection_results = []
camera_active = False
last_qr_data = None
qr_detection_time = 0
cooldown_period = 3

# 녹화 관련 전역 변수
recording = False
video_writer = None
recording_start_time = None
recording_filename = None

# HLS 관련 전역 변수
hls_enabled = True
hls_pipeline = None
hls_appsrc = None
hls_dir = os.path.abspath(os.path.join(os.getcwd(), 'hls'))
hls_httpd_server = None
hls_httpd_thread = None
hls_http_port = 8090

# --- MQTT 설정 ---
MQTT_BROKER_HOST = os.getenv('MQTT_BROKER_HOST', '192.168.0.76')
MQTT_BROKER_PORT = int(os.getenv('MQTT_BROKER_PORT', '8883'))
# 쉼표로 구분된 토픽 목록 환경변수 지원. 기본 예시 토픽들.
MQTT_TOPICS = [t.strip() for t in os.getenv('MQTT_TOPICS', 'things/+/command/req,things/+/status/req,+/queue').split(',') if t.strip()]
_mqtt_client = None

def extract_json_from_raw(raw_string: str):
    try:
        start_brace = raw_string.find('{')
        end_brace = raw_string.rfind('}')
        if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
            return raw_string[start_brace:end_brace + 1]
        return None
    except Exception:
        return None

def mqtt_on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[MQTT] 연결 성공 → {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        for topic in MQTT_TOPICS:
            try:
                client.subscribe(topic, qos=1)
                print(f"[MQTT] 구독: {topic}")
            except Exception as e:
                print(f"[MQTT] 구독 실패: {topic} → {e}")
    else:
        print(f"[MQTT] 연결 실패: rc={rc}")

def mqtt_on_message(client, userdata, msg):
    global camera_frame
    try:
        raw = msg.payload.decode('utf-8', 'ignore')
    except Exception:
        raw = str(msg.payload)
    print(f"[MQTT] 수신 [{msg.topic}]: {raw}")

    # JSON 파싱 시도 (중괄호만 추출 후 로드)
    data = None
    try:
        json_part = extract_json_from_raw(raw)
        if json_part:
            data = json.loads(json_part)
    except Exception:
        data = None

    topic = msg.topic or ''
    topic_lower = topic.lower()

    # things/{thing_name}/command/req 패턴의 간단 명령 처리
    if topic_lower.endswith('/command/req'):
        cmd = ''
        if isinstance(data, dict):
            cmd = str(data.get('command', '')).lower()

        # HLS 시작
        if cmd in ['hls_on', 'hls_start']:
            # width/height 추정: payload의 frame(예: "1280x720") → camera_frame → 기본값
            width, height = 1280, 720
            try:
                if isinstance(data, dict) and 'frame' in data:
                    frame_val = str(data['frame']).lower().replace(' ', '')
                    if 'x' in frame_val:
                        w_str, h_str = frame_val.split('x', 1)
                        width, height = int(w_str), int(h_str)
                elif camera_frame is not None:
                    height, width = camera_frame.shape[:2]
            except Exception:
                pass

            # FPS 결정: payload의 fps → 기본 20
            fps = 20
            try:
                if isinstance(data, dict) and 'fps' in data:
                    fps = max(1, int(data['fps']))
            except Exception:
                pass

            try:
                start_hls_http_server()
                start_hls_pipeline(int(width), int(height), int(fps))
                print('[CMD] HLS 시작')
            except Exception as e:
                print(f"[CMD] HLS 시작 실패: {e}")
            return

        # HLS 중지 요청은 무시하고 항상 유지(참고 동작)
        if cmd in ['hls_off', 'hls_stop']:
            try:
                # 항상 켜짐: 요청이 와도 유지하도록 보장
                if camera_frame is not None:
                    h, w = camera_frame.shape[:2]
                    start_hls_http_server()
                    start_hls_pipeline(int(w), int(h), 20)
                else:
                    start_hls_http_server()
                    start_hls_pipeline(1280, 720, 20)
            except Exception:
                pass
            print('[CMD] HLS 항상 켜짐: 중지 요청 무시하고 유지')
            return

        if cmd in ['start_recording', 'record_on']:
            try:
                if camera_frame is not None:
                    ok, msg_text = start_recording(camera_frame)
                    print(f"[CMD] 녹화 시작: {ok} {msg_text}")
                else:
                    print('[CMD] 녹화 시작 실패: 카메라 프레임 없음')
            except Exception as e:
                print(f"[CMD] 녹화 시작 실패: {e}")
            return

        if cmd in ['stop_recording', 'record_off']:
            ok, msg_text = stop_recording()
            print(f"[CMD] 녹화 중지: {ok} {msg_text}")
            return

        # 아직 지원하지 않는 명령들 (camera_on/off 등)
        if cmd:
            print(f"[CMD] 미지원 명령: {cmd}")
        else:
            print('[CMD] command 필드가 없습니다.')
        return

    # 상태/큐 요청은 현재 로깅만 수행
    if topic_lower.endswith('/status/req'):
        print('[STATUS] 상태 요청 수신 (응답 로직은 미구현)')
        return
    if topic_lower.endswith('/queue'):
        print('[QUEUE] 큐 메시지 수신 (처리 로직은 미구현)')
        return

def start_mqtt_subscriber():
    global _mqtt_client
    try:
        client_id = f"main_subscriber_{socket.gethostname()}"
    except Exception:
        client_id = f"main_subscriber_{uuid.uuid4()}"
    client = mqtt.Client(client_id=client_id)
    client.on_connect = mqtt_on_connect
    client.on_message = mqtt_on_message
    try:
        client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        client.loop_start()
        print(f"[MQTT] 브로커 연결 시도: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        _mqtt_client = client
    except Exception as e:
        print(f"[MQTT] 연결 실패: {e}")
        _mqtt_client = None

def get_client_ip():
    """클라이언트 IP 주소를 가져오는 함수"""
    try:
        # 외부 서버에 연결하여 공인 IP 확인
        response = requests.get('https://api.ipify.org', timeout=5)
        return response.text
    except:
        try:
            # 로컬 IP 확인
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

def get_mac_address():
    """MAC 주소를 가져오는 함수"""
    try:
        # Linux/Unix 시스템에서 MAC 주소 가져오기
        if os.name == 'posix':
            # 네트워크 인터페이스에서 MAC 주소 가져오기
            try:
                result = subprocess.run(['ip', 'link', 'show'], capture_output=True, text=True)
                if result.returncode == 0:
                    lines = result.stdout.split('\n')
                    for line in lines:
                        if 'link/ether' in line:
                            mac = line.split('link/ether')[1].strip().split()[0]
                            return mac
            except:
                pass
            
            # 대안: /sys/class/net에서 MAC 주소 가져오기
            try:
                for interface in os.listdir('/sys/class/net'):
                    if interface != 'lo':  # loopback 제외
                        mac_path = f'/sys/class/net/{interface}/address'
                        if os.path.exists(mac_path):
                            with open(mac_path, 'r') as f:
                                mac = f.read().strip()
                                if mac and mac != '00:00:00:00:00:00':
                                    return mac
            except:
                pass
        
        # Windows 시스템에서 MAC 주소 가져오기
        elif os.name == 'nt':
            try:
                result = subprocess.run(['getmac', '/fo', 'csv'], capture_output=True, text=True)
                if result.returncode == 0:
                    lines = result.stdout.split('\n')
                    for line in lines:
                        if 'Physical Address' not in line and line.strip():
                            parts = line.split(',')
                            if len(parts) >= 2:
                                mac = parts[1].strip().strip('"')
                                if mac and mac != '00-00-00-00-00-00':
                                    return mac.replace('-', ':')
            except:
                pass
        
        # 대안: UUID를 사용하여 MAC 주소 생성 (마지막 수단)
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> elements) & 0xff) for elements in range(0,2*6,2)][::-1])
        return mac
        
    except Exception as e:
        print(f"MAC 주소 가져오기 실패: {e}")
        # 기본 MAC 주소 반환
        return "00:00:00:00:00:00"

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
    <div class=\"hint\">재생 URL: index.m3u8</div>
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

def start_hls_pipeline(width: int, height: int, framerate: int):
    global hls_pipeline, hls_appsrc
    if not hls_enabled:
        return
    if hls_pipeline is not None:
        return
    ensure_hls_dir()
    Gst.init(None)
    kbps = 2000
    launch = (
        f"appsrc name=hls_src is-live=true format=time do-timestamp=true block=true "
        f"caps=video/x-raw,format=RGB,width={width},height={height},framerate={framerate}/1 ! "
        f"videoconvert ! video/x-raw,format=I420 ! "
        f"x264enc tune=zerolatency key-int-max=60 bitrate={kbps} ! h264parse ! mpegtsmux ! "
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

def parse_server_info(qr_data):
    """QR 코드 데이터에서 서버 정보를 파싱하는 함수"""
    try:
        # JSON 형태로 파싱 시도
        server_info = json.loads(qr_data)
        return server_info
    except json.JSONDecodeError:
        # JSON이 아닌 경우 다른 형식으로 파싱 시도
        parts = qr_data.split(':')
        if len(parts) >= 3:
            return {
                "ip": parts[0],
                "port": parts[1],
                "key": parts[2]
            }
        else:
            print(f"지원하지 않는 QR 코드 형식입니다: {qr_data}")
            return None

def send_commission_request(server_info):
    """커미션 요청을 서버에 보내는 함수"""
    try:
        # 클라이언트 IP 가져오기
        client_ip = get_client_ip()
        print(f"클라이언트 IP: {client_ip}")
        
        # 서버 URL 구성
        server_url = f"http://{server_info['ip']}:{server_info['port']}/commission"
        
        # 요청 데이터 준비
        request_data = {
            "client_ip": client_ip
        }
        
        # API 요청 보내기
        print(f"서버에 요청 보내는 중: {server_url}")
        response = requests.post(
            server_url,
            json=request_data,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        
        print(f"응답 상태 코드: {response.status_code}")
        print(f"응답 내용: {response.text}")
        
        if response.status_code == 200:
            print("커미션 요청이 성공적으로 전송되었습니다.")
            return True
        else:
            print(f"커미션 요청 실패: {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"네트워크 오류: {e}")
        return False
    except Exception as e:
        print(f"오류 발생: {e}")
        return False

def send_pairing_request(endpoint_url):
    """QR 코드에서 인식된 endpoint로 페어링 요청을 보내는 함수"""
    try:
        # 클라이언트 IP와 MAC 주소 가져오기
        client_ip = get_client_ip()
        mac_address = get_mac_address()
        
        print(f"클라이언트 IP: {client_ip}")
        print(f"MAC 주소: {mac_address}")
        print(f"페어링 요청을 보내는 중: {endpoint_url}")
        
        # 요청 데이터 준비
        request_data = {
            "ip": client_ip,
            "mac_address": mac_address
        }
        
        # API 요청 보내기
        response = requests.post(
            endpoint_url,
            json=request_data,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        
        print(f"응답 상태 코드: {response.status_code}")
        print(f"응답 내용: {response.text}")
        
        if response.status_code == 200:
            print("✅ 페어링 요청이 성공적으로 전송되었습니다.")
            return True
        else:
            print(f"❌ 페어링 요청 실패: {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"네트워크 오류: {e}")
        return False
    except Exception as e:
        print(f"오류 발생: {e}")
        return False

def enhance_image_for_qr(frame):
    """QR 코드 인식을 위한 이미지 향상"""
    if frame is None:
        return None
    
    # 그레이스케일 변환
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    
    # 노이즈 제거
    denoised = cv2.fastNlMeansDenoising(gray)
    
    # 대비 향상
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(denoised)
    
    # 선명도 향상
    kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
    sharpened = cv2.filter2D(enhanced, -1, kernel)
    
    return sharpened

def detect_qr_codes_enhanced(frame):
    """향상된 QR 코드 감지"""
    if frame is None:
        return []
    
    # 원본 이미지로 QR 코드 감지
    decoded_original = pyzbar.decode(frame)
    
    # 향상된 이미지로 QR 코드 감지
    enhanced = enhance_image_for_qr(frame)
    decoded_enhanced = pyzbar.decode(enhanced)
    
    # 결과 합치기
    all_results = []
    
    # 원본 결과 추가
    for obj in decoded_original:
        all_results.append({
            'data': obj.data.decode('utf-8'),
            'rect': obj.rect,
            'polygon': obj.polygon,
            'quality': 'original'
        })
    
    # 향상된 결과 추가 (중복 제거)
    for obj in decoded_enhanced:
        data = obj.data.decode('utf-8')
        # 중복 확인
        is_duplicate = any(result['data'] == data for result in all_results)
        if not is_duplicate:
            all_results.append({
                'data': data,
                'rect': obj.rect,
                'polygon': obj.polygon,
                'quality': 'enhanced'
            })
    
    return all_results

def check_system_status():
    """시스템 상태 확인"""
    print("=== 시스템 상태 확인 ===")
    
    # 비디오 장치 확인
    video_devices = []
    for i in range(10):
        if os.path.exists(f'/dev/video{i}'):
            video_devices.append(i)
    
    print(f"비디오 장치: {video_devices}")
    
    # 권한 확인
    for device in video_devices:
        try:
            stat = os.stat(f'/dev/video{device}')
            mode = oct(stat.st_mode)[-3:]
            print(f"  /dev/video{device}: 권한 {mode}")
        except:
            print(f"  /dev/video{device}: 권한 확인 불가")
    
    # 메모리 상태 확인
    try:
        with open('/proc/meminfo', 'r') as f:
            mem_info = f.read()
            mem_total = [line for line in mem_info.split('\n') if 'MemTotal' in line]
            if mem_total:
                print(f"메모리: {mem_total[0]}")
    except:
        print("메모리 정보 확인 불가")
    
    print()

def camera_stream():
    """카메라 스트리밍 함수 - CM5 + IO 보드 최적화 + QR 인식 향상"""
    global camera_frame, qr_detection_results, last_qr_data, qr_detection_time, camera_active
    
    print("카메라 스트리밍을 시작합니다...")
    print("CM5 + IO 보드 환경에서 Pi Camera 3를 초기화합니다...")
    print("QR 코드 인식 최적화 기능이 활성화되었습니다.")
    
    # 시스템 상태 확인
    check_system_status()
    
    camera_type = None
    picam2 = None
    cap = None
    
    # 1단계: Picamera2 시도 (Pi Camera 3 전용) - 제공된 코드 방식 참고
    try:
        print("1단계: Picamera2 초기화 시도 중...")
        picam2 = Picamera2()
        
        # 제공된 코드와 동일한 방식으로 설정
        cfg = picam2.create_video_configuration(
            main={'size': (1280, 720), 'format': 'RGB888'}
        )
        
        print("카메라 설정 적용 중...")
        picam2.configure(cfg)
        
        print("카메라 시작 중...")
        picam2.start()
        
        # 자동 초점 설정 (제공된 코드와 동일)
        picam2.set_controls({"FrameRate": 20})
        picam2.set_controls({"AfMode": 2})  # 0=Manual, 1=Auto, 2=Continuous
        
        print("✅ 자동 초점이 활성화되었습니다.")
        
        # 카메라 안정화를 위한 대기
        print("카메라 안정화 대기 중...")
        time.sleep(2)
        
        # 초기 프레임으로 카메라 상태 확인 (제공된 코드와 동일한 방식)
        print("초기 프레임 캡처 테스트...")
        test_frame = picam2.capture_array()
        if test_frame is not None:
            print(f"✅ Picamera2 초기화 성공! 프레임 크기: {test_frame.shape}")
            camera_type = "Picamera2"
        else:
            print("❌ 초기 프레임 캡처 실패")
            raise Exception("초기 프레임 캡처 실패")
            
    except Exception as e:
        print(f"Picamera2 초기화 실패: {e}")
        print("2단계: OpenCV로 대안 시도 중...")
        
        # 2단계: OpenCV 시도
        try:
            # CM5 + IO 보드에서 사용 가능한 카메라 장치 찾기
            camera_devices = []
            for i in range(5):  # video0부터 video4까지 시도
                if os.path.exists(f'/dev/video{i}'):
                    camera_devices.append(i)
            
            print(f"발견된 비디오 장치: {camera_devices}")
            
            if not camera_devices:
                print("❌ 사용 가능한 비디오 장치가 없습니다.")
                print("CM5 + IO 보드 설정을 확인하세요.")
                return
            
            # 각 장치로 카메라 열기 시도
            for device_index in camera_devices:
                print(f"비디오 장치 {device_index}로 카메라 열기 시도...")
                cap = cv2.VideoCapture(device_index)
                
                if cap.isOpened():
                    # 제공된 코드와 동일한 방식으로 설정
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    
                    # 안정적인 해상도 설정
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    cap.set(cv2.CAP_PROP_FPS, 20)
                    
                    # 설정 적용을 위한 대기
                    time.sleep(1)
                    
                    # 자동 초점 설정
                    try:
                        cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
                        print("✅ OpenCV 자동 초점 활성화")
                    except Exception as e:
                        print(f"⚠️  OpenCV 자동 초점 설정 실패: {e}")
                    
                    # 카메라 정보 확인
                    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    
                    print(f"✅ OpenCV 카메라가 열렸습니다! (장치: {device_index})")
                    print(f"  해상도: {width}x{height}, FPS: {fps}")
                    
                    # 제공된 코드와 동일한 방식으로 프레임 읽기 테스트
                    print("테스트 프레임 읽기 시작...")
                    ret, test_frame = cap.read()
                    if ret and test_frame is not None:
                        print(f"✅ 테스트 프레임 성공: {test_frame.shape}")
                        camera_type = "OpenCV"
                        break
                    else:
                        print("❌ 테스트 프레임 실패")
                        cap.release()
                        cap = None
                else:
                    print(f"  장치 {device_index} 열기 실패")
                    if cap:
                        cap.release()
                        cap = None
            
            if not camera_type:
                print("❌ 모든 비디오 장치에서 카메라를 열 수 없습니다.")
                print("\n💡 문제 해결 방법:")
                print("1. 카메라 하드웨어 연결 확인")
                print("2. sudo apt-get install v4l-utils")
                print("3. v4l2-ctl --list-devices 실행")
                print("4. sudo chmod 666 /dev/video*")
                print("5. 시스템 재부팅")
                return
                
        except Exception as e:
            print(f"OpenCV 카메라 초기화 실패: {e}")
            return
    
    if not camera_type:
        print("❌ 카메라를 초기화할 수 없습니다.")
        return
    
    print(f"✅ {camera_type} 카메라가 성공적으로 시작되었습니다!")
    camera_active = True
    frame_count = 0
    
    try:
        while camera_active:
            try:
                # 프레임 캡처
                if camera_type == "Picamera2":
                    frame = picam2.capture_array()
                    if frame is not None:
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    else:
                        print("❌ Picamera2에서 프레임을 읽을 수 없습니다.")
                        time.sleep(0.1)
                        continue
                else:
                    # 제공된 코드와 동일한 방식으로 프레임 캡처
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        print("❌ OpenCV에서 프레임을 읽을 수 없습니다.")
                        time.sleep(0.1)
                        continue
                
                frame_count += 1
                
                # 프레임 크기 조정 (웹 스트리밍 최적화)
                if frame.shape[0] > 720 or frame.shape[1] > 1280:
                    frame = cv2.resize(frame, (1280, 720))
                
                # 향상된 QR 코드 디코딩
                qr_results = detect_qr_codes_enhanced(frame)
                
                current_time = time.time()
                new_qr_results = []
                
                for result in qr_results:
                    qr_data = result['data']
                    
                    # 새로운 QR 코드이거나 쿨다운이 지난 경우에만 처리
                    if (qr_data != last_qr_data or 
                        current_time - qr_detection_time > cooldown_period):
                        
                        print(f"\n🎯 QR 코드 감지됨: {qr_data} (품질: {result['quality']})")
                        
                        # QR 코드 데이터 파싱 시도
                        try:
                            qr_json = json.loads(qr_data)
                            print(f"📡 QR 코드 데이터 파싱 성공: {qr_json}")
                            
                            # endpoint가 있는지 확인
                            if 'endpoint' in qr_json:
                                endpoint_url = qr_json['endpoint']
                                print(f"🎯 페어링 endpoint 발견: {endpoint_url}")
                                
                                # 별도 스레드에서 페어링 요청 전송
                                pairing_thread = threading.Thread(
                                    target=send_pairing_request, 
                                    args=(endpoint_url,)
                                )
                                pairing_thread.start()
                                
                                new_qr_results.append({
                                    "data": qr_data,
                                    "endpoint": endpoint_url,
                                    "timestamp": current_time,
                                    "quality": result['quality'],
                                    "status": "페어링 요청 전송됨"
                                })
                            else:
                                print("⚠️  QR 코드에 endpoint 정보가 없습니다.")
                                new_qr_results.append({
                                    "data": qr_data,
                                    "timestamp": current_time,
                                    "quality": result['quality'],
                                    "status": "endpoint 정보 없음"
                                })
                                
                        except json.JSONDecodeError:
                            print("❌ QR 코드 데이터가 JSON 형식이 아닙니다.")
                            # 기존 서버 정보 파싱 방식으로 폴백
                            server_info = parse_server_info(qr_data)
                            if server_info:
                                print(f"📡 서버 정보: {server_info}")
                                
                                # 별도 스레드에서 API 호출
                                api_thread = threading.Thread(
                                    target=send_commission_request, 
                                    args=(server_info,)
                                )
                                api_thread.start()
                                
                                new_qr_results.append({
                                    "data": qr_data,
                                    "server_info": server_info,
                                    "timestamp": current_time,
                                    "quality": result['quality']
                                })
                            else:
                                print("❌ QR 코드 데이터를 파싱할 수 없습니다.")
                                new_qr_results.append({
                                    "data": qr_data,
                                    "timestamp": current_time,
                                    "quality": result['quality'],
                                    "status": "파싱 실패"
                                })
                        
                        last_qr_data = qr_data
                        qr_detection_time = current_time
                    
                    # QR 코드 영역에 사각형 그리기 (품질에 따른 색상)
                    points = result['polygon']
                    if len(points) > 4:
                        hull = cv2.convexHull(np.array([point for point in points], dtype=np.float32))
                        points = hull
                    
                    n = len(points)
                    # 품질에 따른 색상 설정
                    color = (0, 255, 0) if result['quality'] == 'original' else (0, 255, 255)  # 녹색 또는 노란색
                    
                    for j in range(n):
                        cv2.line(frame, tuple(points[j]), tuple(points[(j+1) % n]), color, 3)
                    
                    # QR 코드 데이터 텍스트 표시
                    x, y, w, h = result['rect']
                    cv2.putText(frame, f"{qr_data} ({result['quality']})", (x, y-10), 
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                
                # 상태 정보를 화면에 표시
                status_text = f"Frame: {frame_count} | QR Detected: {len(qr_results)}"
                cv2.putText(frame, status_text, (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                
                # 카메라 타입과 장치 정보 표시
                camera_info = f"Camera: {camera_type}"
                if camera_type == "OpenCV" and cap:
                    device_index = int(cap.get(cv2.CAP_PROP_POS_FRAMES) if cap.get(cv2.CAP_PROP_POS_FRAMES) >= 0 else 0)
                    camera_info += f" (Device: {device_index})"
                
                cv2.putText(frame, camera_info, (10, 60), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
                
                # CM5 + IO 보드 정보 표시
                cv2.putText(frame, "CM5 + IO Board", (10, 90), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
                
                # QR 인식 최적화 정보 표시
                cv2.putText(frame, "QR Enhanced", (10, 120), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                
                # 녹화 중인 경우 프레임을 녹화 파일에 쓰기
                if recording:
                    write_frame_to_recording(frame)
                
                # HLS 프레임 푸시 (RGB)
                try:
                    if hls_appsrc is not None:
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        data = rgb.tobytes()
                        buf = Gst.Buffer.new_allocate(None, len(data), None)
                        buf.fill(0, data)
                        # 간단 duration만 설정 (타임스탬프는 omit)
                        buf.duration = int(1e9/20)
                        hls_appsrc.emit('push-buffer', buf)
                except Exception:
                    pass

                # 전역 변수 업데이트
                camera_frame = frame.copy()
                if new_qr_results:
                    qr_detection_results.extend(new_qr_results)
                    # 최근 10개 결과만 유지
                    qr_detection_results = qr_detection_results[-10:]
                
                # 웹 스트리밍을 위해 약간의 지연
                time.sleep(0.05)  # 지연 시간을 줄여서 성능 향상
                
            except Exception as e:
                print(f"프레임 처리 오류: {e}")
                time.sleep(0.1)
                
    except KeyboardInterrupt:
        print("카메라 스트리밍이 중단되었습니다.")
    except Exception as e:
        print(f"카메라 스트리밍 오류: {e}")
    finally:
        camera_active = False
        
        # 녹화 중인 경우 녹화 중지
        if recording:
            stop_recording()
        
        if camera_type == "Picamera2" and picam2:
            picam2.stop()
            picam2.close()
        elif camera_type == "OpenCV" and cap:
            cap.release()
        print("카메라가 종료되었습니다.")

def get_frame_base64():
    """현재 프레임을 base64로 인코딩하여 반환"""
    global camera_frame
    
    if camera_frame is None:
        # 기본 이미지 생성
        default_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(default_frame, "Camera not available", (200, 240), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        camera_frame = default_frame
    
    # JPEG로 인코딩
    _, buffer = cv2.imencode('.jpg', camera_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    jpg_as_text = base64.b64encode(buffer).decode('utf-8')
    return jpg_as_text

@app.route('/')
def index():
    """메인 페이지"""
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    """비디오 스트림 (MJPEG) - 성능 최적화"""
    def generate():
        last_frame = None
        while True:
            if camera_frame is not None and camera_active:
                # 프레임이 변경되었을 때만 인코딩
                if last_frame is not camera_frame:
                    try:
                        # JPEG 품질을 낮춰서 성능 향상
                        _, buffer = cv2.imencode('.jpg', camera_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                        frame_bytes = buffer.tobytes()
                        last_frame = camera_frame
                        
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                    except Exception as e:
                        print(f"프레임 인코딩 오류: {e}")
                        time.sleep(0.1)
                        continue
                else:
                    time.sleep(0.05)  # 프레임이 변경되지 않았을 때는 짧게 대기
            else:
                # 카메라가 없을 때는 기본 이미지 표시
                default_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(default_frame, "Camera not available", (200, 240), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                
                _, buffer = cv2.imencode('.jpg', default_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                frame_bytes = buffer.tobytes()
                
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                time.sleep(1)  # 기본 이미지는 1초마다 업데이트
    
    return Response(generate(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/get_frame')
def get_frame():
    """현재 프레임을 base64로 반환 (AJAX용) - 성능 최적화"""
    global camera_frame
    
    if camera_frame is None or not camera_active:
        # 카메라가 없을 때는 기본 이미지 반환
        default_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(default_frame, "Camera not available", (200, 240), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        _, buffer = cv2.imencode('.jpg', default_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
        frame_base64 = base64.b64encode(buffer).decode('utf-8')
    else:
        # JPEG 품질을 낮춰서 성능 향상
        _, buffer = cv2.imencode('.jpg', camera_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
        frame_base64 = base64.b64encode(buffer).decode('utf-8')
    
    return jsonify({
        'frame': frame_base64,
        'qr_results': qr_detection_results,
        'camera_active': camera_active,
        'timestamp': time.time()
    })

@app.route('/start_camera')
def start_camera():
    """카메라 시작"""
    global camera_active
    print(f"카메라 시작 요청 - 현재 상태: {camera_active}")
    
    if not camera_active:
        try:
            # HLS 서버/파이프라인 준비
            try:
                start_hls_http_server()
            except Exception:
                pass
            camera_thread = threading.Thread(target=camera_stream, daemon=True)
            camera_thread.start()
            camera_active = True
            print("✅ 카메라 스레드가 시작되었습니다.")
            return jsonify({'status': 'success', 'message': '카메라가 시작되었습니다.'})
        except Exception as e:
            print(f"❌ 카메라 시작 실패: {e}")
            camera_active = False
            return jsonify({'status': 'error', 'message': f'카메라 시작 실패: {e}'})
    else:
        print("⚠️  카메라가 이미 실행 중입니다.")
        return jsonify({'status': 'info', 'message': '카메라가 이미 실행 중입니다.'})

@app.route('/stop_camera')
def stop_camera():
    """카메라 중지"""
    global camera_active
    camera_active = False
    try:
        stop_hls_pipeline()
        stop_hls_http_server()
    except Exception:
        pass
    return jsonify({'status': 'success', 'message': '카메라가 중지되었습니다.'})

@app.route('/start_recording')
def start_recording_route():
    """녹화 시작 API"""
    global camera_frame
    
    if not camera_active or camera_frame is None:
        return jsonify({'status': 'error', 'message': '카메라가 활성화되지 않았습니다.'})
    
    success, message = start_recording(camera_frame)
    if success:
        return jsonify({'status': 'success', 'message': message})
    else:
        return jsonify({'status': 'error', 'message': message})

@app.route('/stop_recording')
def stop_recording_route():
    """녹화 중지 API"""
    success, message = stop_recording()
    if success:
        return jsonify({'status': 'success', 'message': message})
    else:
        return jsonify({'status': 'error', 'message': message})

@app.route('/recording_status')
def recording_status_route():
    """녹화 상태 확인 API"""
    status = get_recording_status()
    return jsonify(status)

@app.route('/hls_on')
def hls_on_route():
    try:
        # 적당한 기본 해상도/프레임레이트로 시작 (현재 프레임 크기 알 수 없는 경우 가정)
        start_hls_http_server()
        start_hls_pipeline(1280, 720, 20)
        return jsonify({'status': 'success', 'message': 'HLS가 시작되었습니다.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'HLS 시작 실패: {e}'})

@app.route('/hls_off')
def hls_off_route():
    try:
        stop_hls_pipeline()
        stop_hls_http_server()
        return jsonify({'status': 'success', 'message': 'HLS가 중지되었습니다.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'HLS 중지 실패: {e}'})

@app.route('/list_recordings')
def list_recordings_route():
    """녹화된 파일 목록 API"""
    try:
        # 현재 디렉토리에서 녹화 파일들 찾기
        recording_files = []
        for filename in os.listdir('.'):
            if filename.startswith('recording_') and filename.endswith('.mp4'):
                file_path = os.path.join('.', filename)
                file_stat = os.stat(file_path)
                
                recording_files.append({
                    'filename': filename,
                    'size_mb': round(file_stat.st_size / (1024 * 1024), 2),
                    'created_time': datetime.fromtimestamp(file_stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                    'modified_time': datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                })
        
        # 생성 시간 기준으로 정렬 (최신순)
        recording_files.sort(key=lambda x: x['created_time'], reverse=True)
        
        return jsonify({
            'status': 'success',
            'files': recording_files,
            'count': len(recording_files)
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'파일 목록 조회 실패: {e}'
        })

@app.route('/delete_recording/<filename>')
def delete_recording_route(filename):
    """녹화 파일 삭제 API"""
    try:
        # 보안: 파일명 검증
        if not filename.startswith('recording_') or not filename.endswith('.mp4'):
            return jsonify({
                'status': 'error',
                'message': '잘못된 파일명입니다.'
            })
        
        file_path = os.path.join('.', filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            return jsonify({
                'status': 'success',
                'message': f'파일 {filename}이 삭제되었습니다.'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': '파일을 찾을 수 없습니다.'
            })
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'파일 삭제 실패: {e}'
        })

@app.route('/download_recording/<filename>')
def download_recording_route(filename):
    """녹화 파일 다운로드 API"""
    try:
        # 보안: 파일명 검증
        if not filename.startswith('recording_') or not filename.endswith('.mp4'):
            return jsonify({
                'status': 'error',
                'message': '잘못된 파일명입니다.'
            })
        
        file_path = os.path.join('.', filename)
        if os.path.exists(file_path):
            from flask import send_file
            return send_file(
                file_path,
                as_attachment=True,
                download_name=filename,
                mimetype='video/mp4'
            )
        else:
            return jsonify({
                'status': 'error',
                'message': '파일을 찾을 수 없습니다.'
            })
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'파일 다운로드 실패: {e}'
        })

@app.route('/play_recording/<filename>')
def play_recording_route(filename):
    """녹화 파일 재생 API"""
    try:
        # 보안: 파일명 검증
        if not filename.startswith('recording_') or not filename.endswith('.mp4'):
            return jsonify({
                'status': 'error',
                'message': '잘못된 파일명입니다.'
            })
        
        file_path = os.path.join('.', filename)
        if os.path.exists(file_path):
            from flask import send_file
            return send_file(
                file_path,
                mimetype='video/mp4'
            )
        else:
            return jsonify({
                'status': 'error',
                'message': '파일을 찾을 수 없습니다.'
            })
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'파일 재생 실패: {e}'
        })

def start_recording(frame):
    """녹화 시작"""
    global recording, video_writer, recording_start_time, recording_filename
    
    if recording:
        return False, "이미 녹화 중입니다."
    
    try:
        # 현재 시간으로 파일명 생성
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        recording_filename = f"recording_{timestamp}.mp4"
        
        # 프레임 크기 가져오기
        height, width = frame.shape[:2]
        
        # VideoWriter 초기화 (H.264 코덱 사용)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        video_writer = cv2.VideoWriter(recording_filename, fourcc, 20.0, (width, height))
        
        if not video_writer.isOpened():
            return False, "비디오 writer를 초기화할 수 없습니다."
        
        recording = True
        recording_start_time = time.time()
        
        print(f"✅ 녹화가 시작되었습니다: {recording_filename}")
        return True, f"녹화가 시작되었습니다: {recording_filename}"
        
    except Exception as e:
        print(f"❌ 녹화 시작 실패: {e}")
        return False, f"녹화 시작 실패: {e}"

def stop_recording():
    """녹화 중지"""
    global recording, video_writer, recording_start_time, recording_filename
    
    if not recording:
        return False, "녹화 중이 아닙니다."
    
    try:
        recording = False
        
        if video_writer:
            video_writer.release()
            video_writer = None
        
        if recording_start_time:
            duration = time.time() - recording_start_time
            recording_start_time = None
            
            # 파일 크기 확인
            if os.path.exists(recording_filename):
                file_size = os.path.getsize(recording_filename)
                file_size_mb = file_size / (1024 * 1024)
                
                print(f"✅ 녹화가 완료되었습니다: {recording_filename}")
                print(f"  - 녹화 시간: {duration:.1f}초")
                print(f"  - 파일 크기: {file_size_mb:.2f}MB")
                
                return True, f"녹화 완료: {recording_filename} ({duration:.1f}초, {file_size_mb:.2f}MB)"
            else:
                return False, "녹화 파일을 찾을 수 없습니다."
        
        return True, "녹화가 중지되었습니다."
        
    except Exception as e:
        print(f"❌ 녹화 중지 실패: {e}")
        return False, f"녹화 중지 실패: {e}"

def get_recording_status():
    """녹화 상태 반환"""
    global recording, recording_start_time, recording_filename
    
    if not recording:
        return {
            'recording': False,
            'message': '녹화 중이 아닙니다.'
        }
    
    duration = time.time() - recording_start_time if recording_start_time else 0
    
    return {
        'recording': True,
        'filename': recording_filename,
        'duration': f"{duration:.1f}초",
        'message': f"녹화 중: {recording_filename} ({duration:.1f}초)"
    }

def write_frame_to_recording(frame):
    """프레임을 녹화 파일에 쓰기"""
    global recording, video_writer
    
    if recording and video_writer and video_writer.isOpened():
        try:
            video_writer.write(frame)
        except Exception as e:
            print(f"❌ 프레임 녹화 실패: {e}")
            # 녹화 오류 시 자동으로 녹화 중지
            stop_recording()

def create_templates():
    """HTML 템플릿 생성"""
    os.makedirs('templates', exist_ok=True)
    
    html_content = '''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>웹 카메라 QR 스캐너 - 최적화 버전</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        .header h1 {
            margin: 0;
            font-size: 2.5em;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }
        .header .subtitle {
            font-size: 1.2em;
            opacity: 0.9;
            margin-top: 10px;
        }
        .controls {
            text-align: center;
            margin-bottom: 20px;
        }
        .btn {
            background: #4CAF50;
            color: white;
            border: none;
            padding: 12px 24px;
            margin: 0 10px;
            border-radius: 25px;
            cursor: pointer;
            font-size: 16px;
            transition: all 0.3s ease;
        }
        .btn:hover {
            background: #45a049;
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        }
        .btn.stop {
            background: #f44336;
        }
        .btn.stop:hover {
            background: #da190b;
        }
        .btn.optimize {
            background: #ff9800;
        }
        .btn.optimize:hover {
            background: #f57c00;
        }
        .btn.record {
            background: #e91e63;
        }
        .btn.record:hover {
            background: #c2185b;
        }
        .btn.record.recording {
            background: #f44336;
            animation: pulse 1s infinite;
        }
        .btn.record.recording:hover {
            background: #da190b;
        }
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.7; }
            100% { opacity: 1; }
        }
        .camera-container {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
        }
        .camera-feed {
            flex: 2;
            background: rgba(0,0,0,0.8);
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        }
        .camera-feed img {
            width: 100%;
            height: auto;
            border-radius: 10px;
            border: 3px solid #4CAF50;
        }
        .qr-results {
            flex: 1;
            background: rgba(0,0,0,0.8);
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            max-height: 600px;
            overflow-y: auto;
        }
        .qr-results h3 {
            margin-top: 0;
            color: #4CAF50;
        }
        .qr-item {
            background: rgba(255,255,255,0.1);
            padding: 15px;
            margin: 10px 0;
            border-radius: 10px;
            border-left: 4px solid #4CAF50;
        }
        .qr-item.enhanced {
            border-left-color: #ff9800;
        }
        .qr-data {
            font-family: monospace;
            background: rgba(0,0,0,0.5);
            padding: 8px;
            border-radius: 5px;
            margin: 5px 0;
            word-break: break-all;
        }
        .quality-badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            margin-left: 10px;
        }
        .quality-original {
            background: #4CAF50;
            color: white;
        }
        .quality-enhanced {
            background: #ff9800;
            color: white;
        }
        .status {
            text-align: center;
            padding: 15px;
            background: rgba(0,0,0,0.8);
            border-radius: 15px;
            margin-top: 20px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        }
        .status.connected {
            border-left: 4px solid #4CAF50;
        }
        .status.disconnected {
            border-left: 4px solid #f44336;
        }
        .loading {
            text-align: center;
            padding: 40px;
            font-size: 18px;
        }
        .error {
            color: #f44336;
            text-align: center;
            padding: 20px;
        }
        .optimization-info {
            background: rgba(255,152,0,0.2);
            border: 1px solid #ff9800;
            border-radius: 10px;
            padding: 15px;
            margin: 20px 0;
        }
        .optimization-info h4 {
            margin: 0 0 10px 0;
            color: #ff9800;
        }
        .optimization-info ul {
            margin: 0;
            padding-left: 20px;
        }
        .optimization-info li {
            margin: 5px 0;
        }
        .recording-status {
            background: rgba(233,30,99,0.2);
            border: 1px solid #e91e63;
            border-radius: 10px;
            padding: 15px;
            margin: 20px 0;
            text-align: center;
        }
        .recording-status h4 {
            margin: 0 0 10px 0;
            color: #e91e63;
        }
        .recording-info {
            font-size: 16px;
        }
        .recordings-list {
            background: rgba(0,0,0,0.8);
            border-radius: 15px;
            padding: 20px;
            margin: 20px 0;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        }
        .recordings-list h3 {
            margin-top: 0;
            color: #4CAF50;
            text-align: center;
        }
        .recordings-controls {
            text-align: center;
            margin-bottom: 20px;
        }
        .btn.refresh {
            background: #2196F3;
        }
        .btn.refresh:hover {
            background: #1976D2;
        }
        .btn.download {
            background: #4CAF50;
        }
        .btn.download:hover {
            background: #45a049;
        }
        .recordings-container {
            max-height: 400px;
            overflow-y: auto;
        }
        .recording-item {
            background: rgba(255,255,255,0.1);
            padding: 15px;
            margin: 10px 0;
            border-radius: 10px;
            border-left: 4px solid #4CAF50;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .recording-info-left {
            flex: 1;
        }
        .recording-info-right {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        .recording-filename {
            font-weight: bold;
            color: #4CAF50;
            margin-bottom: 5px;
        }
        .recording-details {
            font-size: 14px;
            opacity: 0.8;
        }
        .recording-actions {
            display: flex;
            gap: 5px;
        }
        .btn.small {
            padding: 8px 12px;
            font-size: 12px;
        }
        .btn.play {
            background: #4CAF50;
        }
        .btn.play:hover {
            background: #45a049;
        }
        .btn.delete {
            background: #f44336;
        }
        .btn.delete:hover {
            background: #da190b;
        }
        .recordings-summary {
            background: rgba(76,175,80,0.2);
            border: 1px solid #4CAF50;
            border-radius: 10px;
            padding: 10px;
            margin: 15px 0;
            text-align: center;
        }
        .summary-info {
            font-size: 16px;
            font-weight: bold;
            color: #4CAF50;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🌐 웹 카메라 QR 스캐너 - 최적화 버전</h1>
            <div class="subtitle">CM5 + IO 보드 + Pi Camera 3 | 자동 초점 + 이미지 향상</div>
        </div>
        
        <div class="optimization-info">
            <h4>🎯 QR 코드 인식 최적화 기능</h4>
            <ul>
                <li><strong>자동 초점:</strong> 연속 자동 초점 모드로 선명한 이미지 제공</li>
                <li><strong>이미지 향상:</strong> 노이즈 제거, 대비 향상, 선명도 개선</li>
                <li><strong>고해상도:</strong> 1920x1080 해상도로 더 정확한 인식</li>
                <li><strong>이중 감지:</strong> 원본 + 향상된 이미지로 인식률 향상</li>
            </ul>
        </div>
        
        <div class="controls">
            <button class="btn" onclick="startCamera()">📹 카메라 시작</button>
            <button class="btn stop" onclick="stopCamera()">⏹️ 카메라 중지</button>
            <button class="btn optimize" onclick="optimizeFocus()">🎯 초점 최적화</button>
            <button class="btn record" id="recordBtn" onclick="toggleRecording()" disabled>🔴 녹화 시작</button>
        </div>
        
        <div class="camera-container">
            <div class="camera-feed">
                <h3>📷 카메라 화면 (고해상도)</h3>
                <div id="cameraDisplay">
                    <div class="loading">카메라를 시작해주세요...</div>
                </div>
            </div>
            
            <div class="qr-results">
                <h3>🎯 QR 코드 결과</h3>
                <div id="qrResults">
                    <div class="loading">QR 코드를 감지하면 여기에 표시됩니다...</div>
                </div>
            </div>
        </div>
        
        <div class="status" id="status">
            <div>상태: <span id="statusText">대기 중</span></div>
        </div>
        
        <div class="recording-status" id="recordingStatus" style="display: none;">
            <div class="recording-info">
                <h4>🎥 녹화 상태</h4>
                <div id="recordingInfo">녹화 중이 아닙니다.</div>
            </div>
        </div>
        
        <div class="recordings-list">
            <h3>📁 녹화된 파일 목록</h3>
            <div class="recordings-controls">
                <button class="btn refresh" onclick="refreshRecordings()">🔄 새로고침</button>
                <button class="btn download" onclick="downloadAllRecordings()">📥 전체 다운로드</button>
            </div>
            <div class="recordings-summary" id="recordingsSummary" style="display: none;">
                <div class="summary-info">
                    <span id="fileCount">0개 파일</span> | 
                    <span id="totalSize">총 0MB</span>
                </div>
            </div>
            <div id="recordingsList" class="recordings-container">
                <div class="loading">녹화된 파일을 불러오는 중...</div>
            </div>
        </div>
    </div>

    <script>
        let cameraActive = false;
        let updateInterval;
        let recordingActive = false;
        
        function startCamera() {
            fetch('/start_camera')
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        cameraActive = true;
                        updateStatus('카메라 실행 중 (최적화 모드)', 'connected');
                        startFrameUpdates();
                        // 녹화 버튼 활성화
                        document.getElementById('recordBtn').disabled = false;
                    }
                    alert(data.message);
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('카메라 시작 중 오류가 발생했습니다.');
                });
        }
        
        function stopCamera() {
            fetch('/stop_camera')
                .then(response => response.json())
                .then(data => {
                    cameraActive = false;
                    updateStatus('카메라 중지됨', 'disconnected');
                    stopFrameUpdates();
                    
                    // 녹화 중인 경우 녹화 중지
                    if (recordingActive) {
                        stopRecording();
                    }
                    
                    // 녹화 버튼 비활성화
                    document.getElementById('recordBtn').disabled = true;
                    updateRecordButton(false);
                    hideRecordingStatus();
                    
                    alert(data.message);
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('카메라 중지 중 오류가 발생했습니다.');
                });
        }
        
        function optimizeFocus() {
            alert('초점 최적화 기능이 활성화되었습니다. QR 코드를 카메라에 보여주세요.');
            updateStatus('초점 최적화 중...', 'connected');
        }
        
        function startFrameUpdates() {
            updateInterval = setInterval(updateFrame, 100); // 100ms마다 업데이트
        }
        
        function toggleRecording() {
            if (!recordingActive) {
                startRecording();
            } else {
                stopRecording();
            }
        }
        
        function startRecording() {
            fetch('/start_recording')
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        recordingActive = true;
                        updateRecordButton(true);
                        showRecordingStatus();
                        updateRecordingInfo(data.message);
                    } else {
                        alert(data.message);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('녹화 시작 중 오류가 발생했습니다.');
                });
        }
        
        function stopRecording() {
            fetch('/stop_recording')
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        recordingActive = false;
                        updateRecordButton(false);
                        updateRecordingInfo(data.message);
                        setTimeout(() => {
                            hideRecordingStatus();
                        }, 3000);
                        
                        // 녹화 완료 후 파일 목록 새로고침
                        setTimeout(() => {
                            refreshRecordings();
                        }, 1000);
                    } else {
                        alert(data.message);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('녹화 중지 중 오류가 발생했습니다.');
                });
        }
        
        function updateRecordButton(isRecording) {
            const recordBtn = document.getElementById('recordBtn');
            if (isRecording) {
                recordBtn.textContent = '⏹️ 녹화 중지';
                recordBtn.classList.add('recording');
            } else {
                recordBtn.textContent = '🔴 녹화 시작';
                recordBtn.classList.remove('recording');
            }
        }
        
        function showRecordingStatus() {
            document.getElementById('recordingStatus').style.display = 'block';
        }
        
        function hideRecordingStatus() {
            document.getElementById('recordingStatus').style.display = 'none';
        }
        
        function updateRecordingInfo(message) {
            document.getElementById('recordingInfo').textContent = message;
        }
        
        function updateRecordingStatus() {
            fetch('/recording_status')
                .then(response => response.json())
                .then(data => {
                    if (data.recording) {
                        recordingActive = true;
                        updateRecordButton(true);
                        showRecordingStatus();
                        updateRecordingInfo(data.message);
                    }
                })
                .catch(error => {
                    console.error('Recording status error:', error);
                });
        }
        
        function stopFrameUpdates() {
            if (updateInterval) {
                clearInterval(updateInterval);
            }
        }
        
        function updateFrame() {
            if (!cameraActive) return;
            
            fetch('/get_frame')
                .then(response => response.json())
                .then(data => {
                    if (data.frame) {
                        document.getElementById('cameraDisplay').innerHTML = 
                            `<img src="data:image/jpeg;base64,${data.frame}" alt="Camera Feed">`;
                    }
                    
                    if (data.qr_results && data.qr_results.length > 0) {
                        updateQRResults(data.qr_results);
                    }
                })
                .catch(error => {
                    console.error('Frame update error:', error);
                });
        }
        
        function updateQRResults(results) {
            const qrContainer = document.getElementById('qrResults');
            if (results.length === 0) {
                qrContainer.innerHTML = '<div class="loading">QR 코드를 감지하면 여기에 표시됩니다...</div>';
                return;
            }
            
            let html = '';
            results.slice(-5).reverse().forEach(result => {
                const timestamp = new Date(result.timestamp * 1000).toLocaleTimeString();
                const qualityClass = result.quality === 'enhanced' ? 'enhanced' : '';
                const qualityBadgeClass = result.quality === 'enhanced' ? 'quality-enhanced' : 'quality-original';
                
                html += `
                    <div class="qr-item ${qualityClass}">
                        <div>
                            <strong>감지 시간:</strong> ${timestamp}
                            <span class="quality-badge ${qualityBadgeClass}">${result.quality}</span>
                        </div>
                        <div><strong>QR 데이터:</strong></div>
                        <div class="qr-data">${result.data}</div>
                        ${result.server_info ? `<div><strong>서버 정보:</strong> ${JSON.stringify(result.server_info)}</div>` : ''}
                    </div>
                `;
            });
            
            qrContainer.innerHTML = html;
        }
        
        function updateStatus(text, className) {
            document.getElementById('statusText').textContent = text;
            document.getElementById('status').className = `status ${className}`;
        }
        
        // 페이지 로드 시 상태 초기화
        document.addEventListener('DOMContentLoaded', function() {
            updateStatus('대기 중', 'disconnected');
            // 녹화 상태 확인
            updateRecordingStatus();
            // 녹화 파일 목록 로드
            refreshRecordings();
        });
        
        // 녹화 파일 목록 관련 함수들
        function refreshRecordings() {
            fetch('/list_recordings')
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        displayRecordings(data.files);
                    } else {
                        console.error('녹화 파일 목록 조회 실패:', data.message);
                        document.getElementById('recordingsList').innerHTML = 
                            '<div class="error">녹화 파일 목록을 불러올 수 없습니다.</div>';
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('recordingsList').innerHTML = 
                        '<div class="error">녹화 파일 목록을 불러올 수 없습니다.</div>';
                });
        }
        
        function displayRecordings(files) {
            const container = document.getElementById('recordingsList');
            
            if (files.length === 0) {
                container.innerHTML = '<div class="loading">녹화된 파일이 없습니다.</div>';
                document.getElementById('recordingsSummary').style.display = 'none';
                return;
            }
            
            // 요약 정보 업데이트
            updateRecordingsSummary(files);
            
            let html = '';
            files.forEach(file => {
                // 파일 크기를 보기 좋게 포맷팅
                let sizeText = '';
                if (file.size_mb >= 1024) {
                    sizeText = `${(file.size_mb / 1024).toFixed(2)}GB`;
                } else {
                    sizeText = `${file.size_mb}MB`;
                }
                
                html += `
                    <div class="recording-item">
                        <div class="recording-info-left">
                            <div class="recording-filename">${file.filename}</div>
                            <div class="recording-details">
                                📏 크기: ${sizeText} | 📅 생성: ${file.created_time}
                            </div>
                        </div>
                        <div class="recording-info-right">
                            <div class="recording-actions">
                                <button class="btn small play" onclick="playRecording('${file.filename}')" title="브라우저에서 재생">▶️ 재생</button>
                                <button class="btn small" onclick="downloadRecording('${file.filename}')" title="파일 다운로드">📥 다운로드</button>
                                <button class="btn small delete" onclick="deleteRecording('${file.filename}')" title="파일 삭제">🗑️ 삭제</button>
                            </div>
                        </div>
                    </div>
                `;
            });
            
            container.innerHTML = html;
        }
        
        function updateRecordingsSummary(files) {
            const summaryDiv = document.getElementById('recordingsSummary');
            const fileCountSpan = document.getElementById('fileCount');
            const totalSizeSpan = document.getElementById('totalSize');
            
            if (files.length === 0) {
                summaryDiv.style.display = 'none';
                return;
            }
            
            // 총 파일 크기 계산
            const totalSizeMB = files.reduce((sum, file) => sum + file.size_mb, 0);
            
            // 파일 개수와 총 크기 표시
            fileCountSpan.textContent = `${files.length}개 파일`;
            
            if (totalSizeMB >= 1024) {
                totalSizeSpan.textContent = `총 ${(totalSizeMB / 1024).toFixed(2)}GB`;
            } else {
                totalSizeSpan.textContent = `총 ${totalSizeMB.toFixed(2)}MB`;
            }
            
            summaryDiv.style.display = 'block';
        }
        
        function playRecording(filename) {
            // 브라우저에서 비디오 재생 (새 탭에서 열기)
            window.open(`/play_recording/${filename}`, '_blank');
        }
        
        function downloadRecording(filename) {
            // Flask API를 통한 파일 다운로드
            window.open(`/download_recording/${filename}`, '_blank');
        }
        
        function deleteRecording(filename) {
            if (confirm(`정말로 "${filename}" 파일을 삭제하시겠습니까?`)) {
                fetch(`/delete_recording/${filename}`)
                    .then(response => response.json())
                    .then(data => {
                        if (data.status === 'success') {
                            alert(data.message);
                            refreshRecordings(); // 목록 새로고침
                        } else {
                            alert(data.message);
                        }
                    })
                    .catch(error => {
                        console.error('Error:', error);
                        alert('파일 삭제 중 오류가 발생했습니다.');
                    });
            }
        }
        
        function downloadAllRecordings() {
            // 녹화된 모든 파일을 ZIP으로 다운로드하는 기능
            alert('전체 다운로드 기능은 개발 중입니다. 개별 파일을 다운로드해주세요.');
        }
    </script>
</body>
</html>'''
    
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print("✅ 최적화된 HTML 템플릿이 생성되었습니다.")

def main():
    """메인 함수"""
    print("=== 웹 기반 카메라 QR 스캐너 시스템 ===")
    print("웹 브라우저에서 카메라 화면을 실시간으로 확인할 수 있습니다.")
    
    # MQTT 구독자 시작 (백그라운드 루프)
    try:
        start_mqtt_subscriber()
    except Exception as e:
        print(f"[MQTT] 시작 실패: {e}")

    # HTML 템플릿 생성
    create_templates()
    
    # 서버 IP 확인
    server_ip = get_client_ip()
    print(f"서버 IP: {server_ip}")
    print(f"웹 브라우저에서 http://{server_ip}:5000 으로 접속하세요")
    print("또는 http://localhost:5000 으로 접속하세요")
    print("github test")
    
    # Flask 서버 시작
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

if __name__ == "__main__":
    main()
