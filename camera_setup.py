#!/usr/bin/env python3
"""
라즈베리파이 카메라 설정 및 테스트 스크립트
"""

import cv2
import subprocess
import os
import sys

def check_camera_availability():
    """사용 가능한 카메라 장치 확인"""
    print("=== 카메라 장치 확인 ===")
    
    # /dev/video* 장치 확인
    video_devices = []
    for i in range(10):  # video0부터 video9까지 확인
        device_path = f"/dev/video{i}"
        if os.path.exists(device_path):
            video_devices.append(device_path)
    
    if video_devices:
        print(f"발견된 비디오 장치: {video_devices}")
        return video_devices
    else:
        print("비디오 장치를 찾을 수 없습니다.")
        return []

def test_camera(camera_index=0):
    """카메라 테스트"""
    print(f"\n=== 카메라 {camera_index} 테스트 ===")
    
    cap = cv2.VideoCapture(camera_index)
    
    if not cap.isOpened():
        print(f"카메라 {camera_index}를 열 수 없습니다.")
        return False
    
    # 카메라 정보 출력
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    print(f"카메라 해상도: {width}x{height}")
    print(f"FPS: {fps}")
    
    # 테스트 프레임 캡처
    ret, frame = cap.read()
    if ret:
        print("카메라가 정상적으로 작동합니다.")
        cv2.imshow('Camera Test', frame)
        cv2.waitKey(2000)  # 2초간 표시
        cv2.destroyAllWindows()
        cap.release()
        return True
    else:
        print("프레임을 읽을 수 없습니다.")
        cap.release()
        return False

def enable_camera():
    """라즈베리파이에서 카메라 활성화"""
    print("\n=== 라즈베리파이 카메라 활성화 ===")
    
    try:
        # raspi-config 명령어로 카메라 활성화
        result = subprocess.run(['sudo', 'raspi-config', 'nonint', 'do_camera', '0'], 
                              capture_output=True, text=True)
        
        if result.returncode == 0:
            print("카메라가 활성화되었습니다. 시스템을 재부팅하세요.")
        else:
            print("카메라 활성화에 실패했습니다.")
            
    except FileNotFoundError:
        print("raspi-config를 찾을 수 없습니다. 수동으로 설정하세요.")
        print("sudo raspi-config -> Interface Options -> Camera -> Enable")

def check_camera_modules():
    """카메라 관련 커널 모듈 확인"""
    print("\n=== 카메라 모듈 확인 ===")
    
    try:
        result = subprocess.run(['lsmod'], capture_output=True, text=True)
        modules = result.stdout
        
        camera_modules = ['bcm2835-v4l2', 'v4l2_common', 'videodev']
        found_modules = []
        
        for module in camera_modules:
            if module in modules:
                found_modules.append(module)
                print(f"✓ {module} 모듈이 로드되어 있습니다.")
            else:
                print(f"✗ {module} 모듈이 로드되어 있지 않습니다.")
        
        return found_modules
        
    except Exception as e:
        print(f"모듈 확인 중 오류: {e}")
        return []

def main():
    """메인 함수"""
    print("라즈베리파이 카메라 설정 도구")
    print("=" * 40)
    
    # 1. 카메라 장치 확인
    devices = check_camera_availability()
    
    # 2. 카메라 모듈 확인
    modules = check_camera_modules()
    
    # 3. 카메라 테스트
    if devices:
        for i, device in enumerate(devices):
            device_num = int(device.split('/dev/video')[-1])
            test_camera(device_num)
    else:
        print("\n카메라 장치가 없습니다. 다음을 확인하세요:")
        print("1. 라즈베리 카메라3가 올바르게 연결되어 있는지")
        print("2. 카메라가 활성화되어 있는지")
        print("3. 시스템을 재부팅했는지")
        
        response = input("\n카메라를 활성화하시겠습니까? (y/n): ")
        if response.lower() == 'y':
            enable_camera()

if __name__ == "__main__":
    main()
