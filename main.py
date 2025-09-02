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
import base64
from io import BytesIO
from PIL import Image
import os
import uuid
import subprocess
from datetime import datetime

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
        });
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
