import cv2
import requests
import json
import socket
import re
import numpy as np
from pyzbar import pyzbar
import time
import threading

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
        # 예: "ip:port:key" 형식
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

def scan_qr_with_camera():
    """라즈베리 카메라3를 사용하여 실시간 QR 코드 스캔"""
    print("라즈베리 카메라3를 초기화 중...")
    
    # 라즈베리 카메라3 설정 (CSI 카메라)
    # 라즈베리파이에서는 보통 /dev/video0을 사용
    print("카메라 장치를 열고 있습니다...")
    cap = cv2.VideoCapture(0)
    
    # 카메라 해상도 설정 (선택사항)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    if not cap.isOpened():
        print("❌ 카메라를 열 수 없습니다.")
        print("다음을 확인해주세요:")
        print("1. 라즈베리 카메라3가 올바르게 연결되어 있는지")
        print("2. 카메라가 활성화되어 있는지 (sudo raspi-config)")
        print("3. 시스템을 재부팅했는지")
        print("4. 다른 프로그램이 카메라를 사용하고 있지 않은지")
        
        # 대안 카메라 장치 시도
        print("\n다른 카메라 장치를 시도합니다...")
        for i in range(1, 5):  # video1부터 video4까지 시도
            print(f"카메라 장치 {i} 시도 중...")
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                print(f"✅ 카메라 장치 {i}에서 성공!")
                break
            cap.release()
        
        if not cap.isOpened():
            print("❌ 모든 카메라 장치에서 실패했습니다.")
            print("카메라 설정을 확인하려면 'python3 camera_setup.py'를 실행하세요.")
            input("계속하려면 Enter를 누르세요...")
            return None
    
    print("✅ 카메라가 성공적으로 열렸습니다!")
    
    # 카메라 정보 출력
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"카메라 해상도: {width}x{height}, FPS: {fps}")
    
    print("\nQR 코드를 카메라에 보여주세요. 'q'를 누르면 종료됩니다.")
    
    last_qr_data = None
    qr_detection_time = 0
    cooldown_period = 3  # 3초 쿨다운
    frame_count = 0
    
    try:
        while True:
            ret, frame = cap.read()
            frame_count += 1
            
            if not ret:
                print(f"❌ 프레임 {frame_count}을 읽을 수 없습니다.")
                if frame_count > 10:  # 10프레임 연속 실패 시 종료
                    print("카메라에서 프레임을 읽을 수 없어 종료합니다.")
                    break
                continue
            
            # QR 코드 디코딩
            decoded_objects = pyzbar.decode(frame)
            
            current_time = time.time()
            
            for obj in decoded_objects:
                qr_data = obj.data.decode('utf-8')
                
                # 새로운 QR 코드이거나 쿨다운이 지난 경우에만 처리
                if (qr_data != last_qr_data or 
                    current_time - qr_detection_time > cooldown_period):
                    
                    print(f"\n🎯 QR 코드 감지됨: {qr_data}")
                    
                    # QR 코드 영역에 박스 그리기
                    points = obj.polygon
                    if len(points) > 4:
                        hull = cv2.convexHull(np.array([point for point in points], dtype=np.float32))
                        points = hull
                    
                    n = len(points)
                    for j in range(n):
                        cv2.line(frame, tuple(points[j]), tuple(points[(j+1) % n]), (0, 255, 0), 3)
                    
                    # QR 코드 데이터 표시
                    cv2.putText(frame, qr_data, (obj.rect.left, obj.rect.top - 10),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                    
                    # 서버 정보 파싱 및 API 호출
                    server_info = parse_server_info(qr_data)
                    if server_info:
                        print(f"📡 서버 정보: {server_info}")
                        
                        # 별도 스레드에서 API 호출 (UI 블로킹 방지)
                        api_thread = threading.Thread(
                            target=send_commission_request, 
                            args=(server_info,)
                        )
                        api_thread.start()
                    else:
                        print("❌ QR 코드 데이터를 파싱할 수 없습니다.")
                    
                    last_qr_data = qr_data
                    qr_detection_time = current_time
            
            # 화면에 안내 텍스트 표시
            cv2.putText(frame, "QR Code Scanner - Press 'q' to quit", 
                       (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(frame, f"Frame: {frame_count}", 
                       (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
            # 프레임 표시
            cv2.imshow('QR Code Scanner', frame)
            
            # 'q' 키를 누르면 종료
            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("\n사용자가 'q' 키를 눌러 종료합니다.")
                break
                
    except KeyboardInterrupt:
        print("\n프로그램이 중단되었습니다.")
    except Exception as e:
        print(f"\n❌ 예상치 못한 오류가 발생했습니다: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("카메라가 종료되었습니다.")

def main():
    """메인 함수"""
    print("=== 라즈베리 카메라3 QR 코드 커미션 시스템 ===")
    print("QR 코드 형식 예시: {\"ip\":\"192.168.0.164\",\"port\":8080}")
    
    # 실시간 QR 코드 스캔 시작
    scan_qr_with_camera()

if __name__ == "__main__":
    main()
