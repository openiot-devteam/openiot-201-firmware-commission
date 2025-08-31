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

app = Flask(__name__)

# 전역 변수
camera_frame = None
qr_detection_results = []
camera_active = False
last_qr_data = None
qr_detection_time = 0
cooldown_period = 3

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

def camera_stream():
    """카메라 스트리밍 함수"""
    global camera_frame, qr_detection_results, last_qr_data, qr_detection_time
    
    print("카메라 스트리밍을 시작합니다...")
    
    # Picamera2 초기화 시도
    try:
        picam2 = Picamera2()
        config = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 15}  # 웹 스트리밍을 위해 FPS 낮춤
        )
        picam2.configure(config)
        picam2.start()
        print("✅ Picamera2가 성공적으로 시작되었습니다!")
        camera_type = "Picamera2"
        
    except Exception as e:
        print(f"Picamera2 초기화 실패: {e}")
        print("OpenCV로 대안 시도 중...")
        
        # OpenCV로 대안 시도
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("❌ OpenCV로도 카메라를 열 수 없습니다.")
            return
        
        print("✅ OpenCV 카메라가 열렸습니다!")
        camera_type = "OpenCV"
    
    camera_active = True
    frame_count = 0
    
    try:
        while camera_active:
            try:
                if camera_type == "Picamera2":
                    frame = picam2.capture_array()
                    if frame is not None:
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                else:
                    ret, frame = cap.read()
                    if not ret:
                        continue
                
                frame_count += 1
                
                # QR 코드 디코딩
                decoded_objects = pyzbar.decode(frame)
                
                current_time = time.time()
                qr_results = []
                
                for obj in decoded_objects:
                    qr_data = obj.data.decode('utf-8')
                    
                    # 새로운 QR 코드이거나 쿨다운이 지난 경우에만 처리
                    if (qr_data != last_qr_data or 
                        current_time - qr_detection_time > cooldown_period):
                        
                        print(f"\n🎯 QR 코드 감지됨: {qr_data}")
                        
                        # 서버 정보 파싱 및 API 호출
                        server_info = parse_server_info(qr_data)
                        if server_info:
                            print(f"📡 서버 정보: {server_info}")
                            
                            # 별도 스레드에서 API 호출
                            api_thread = threading.Thread(
                                target=send_commission_request, 
                                args=(server_info,)
                            )
                            api_thread.start()
                            
                            qr_results.append({
                                "data": qr_data,
                                "server_info": server_info,
                                "timestamp": current_time
                            })
                        else:
                            print("❌ QR 코드 데이터를 파싱할 수 없습니다.")
                        
                        last_qr_data = qr_data
                        qr_detection_time = current_time
                    
                    # QR 코드 영역에 사각형 그리기
                    points = obj.polygon
                    if len(points) > 4:
                        hull = cv2.convexHull(np.array([point for point in points], dtype=np.float32))
                        points = hull
                    
                    n = len(points)
                    for j in range(n):
                        cv2.line(frame, tuple(points[j]), tuple(points[(j+1) % n]), (0, 255, 0), 3)
                    
                    # QR 코드 데이터 텍스트 표시
                    x, y, w, h = obj.rect
                    cv2.putText(frame, obj.data.decode('utf-8'), (x, y-10), 
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                
                # 상태 정보를 화면에 표시
                status_text = f"Frame: {frame_count} | QR Detected: {len(decoded_objects)}"
                cv2.putText(frame, status_text, (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                
                # 카메라 타입 표시
                cv2.putText(frame, f"Camera: {camera_type}", (10, 60), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
                
                # 전역 변수 업데이트
                camera_frame = frame.copy()
                if qr_results:
                    qr_detection_results.extend(qr_results)
                    # 최근 10개 결과만 유지
                    qr_detection_results = qr_detection_results[-10:]
                
                # 웹 스트리밍을 위해 약간의 지연
                time.sleep(0.1)
                
            except Exception as e:
                print(f"프레임 처리 오류: {e}")
                time.sleep(0.1)
                
    except KeyboardInterrupt:
        print("카메라 스트리밍이 중단되었습니다.")
    except Exception as e:
        print(f"카메라 스트리밍 오류: {e}")
    finally:
        camera_active = False
        if camera_type == "Picamera2":
            picam2.stop()
            picam2.close()
        else:
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
    """비디오 스트림 (MJPEG)"""
    def generate():
        while True:
            if camera_frame is not None:
                # JPEG로 인코딩
                _, buffer = cv2.imencode('.jpg', camera_frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                frame_bytes = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            time.sleep(0.1)
    
    return Response(generate(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/get_frame')
def get_frame():
    """현재 프레임을 base64로 반환 (AJAX용)"""
    frame_base64 = get_frame_base64()
    return jsonify({
        'frame': frame_base64,
        'qr_results': qr_detection_results,
        'camera_active': camera_active
    })

@app.route('/start_camera')
def start_camera():
    """카메라 시작"""
    global camera_active
    if not camera_active:
        camera_thread = threading.Thread(target=camera_stream, daemon=True)
        camera_thread.start()
        camera_active = True
        return jsonify({'status': 'success', 'message': '카메라가 시작되었습니다.'})
    else:
        return jsonify({'status': 'info', 'message': '카메라가 이미 실행 중입니다.'})

@app.route('/stop_camera')
def stop_camera():
    """카메라 중지"""
    global camera_active
    camera_active = False
    return jsonify({'status': 'success', 'message': '카메라가 중지되었습니다.'})

def create_templates():
    """HTML 템플릿 생성"""
    os.makedirs('templates', exist_ok=True)
    
    html_content = '''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>웹 카메라 QR 스캐너</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .container {
            max-width: 1200px;
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
        .qr-data {
            font-family: monospace;
            background: rgba(0,0,0,0.5);
            padding: 8px;
            border-radius: 5px;
            margin: 5px 0;
            word-break: break-all;
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
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🌐 웹 카메라 QR 스캐너</h1>
            <p>라즈베리파이 카메라를 웹 브라우저에서 실시간으로 확인하고 QR 코드를 인식합니다</p>
        </div>
        
        <div class="controls">
            <button class="btn" onclick="startCamera()">📹 카메라 시작</button>
            <button class="btn stop" onclick="stopCamera()">⏹️ 카메라 중지</button>
        </div>
        
        <div class="camera-container">
            <div class="camera-feed">
                <h3>📷 카메라 화면</h3>
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
    </div>

    <script>
        let cameraActive = false;
        let updateInterval;
        
        function startCamera() {
            fetch('/start_camera')
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        cameraActive = true;
                        updateStatus('카메라 실행 중', 'connected');
                        startFrameUpdates();
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
                    alert(data.message);
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('카메라 중지 중 오류가 발생했습니다.');
                });
        }
        
        function startFrameUpdates() {
            updateInterval = setInterval(updateFrame, 100); // 100ms마다 업데이트
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
                html += `
                    <div class="qr-item">
                        <div><strong>감지 시간:</strong> ${timestamp}</div>
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
        });
    </script>
</body>
</html>'''
    
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print("✅ HTML 템플릿이 생성되었습니다.")

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
    
    # Flask 서버 시작
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

if __name__ == "__main__":
    main()
