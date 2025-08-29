#!/usr/bin/env python3
"""
라즈베리파이 카메라 디버깅 스크립트
"""

import cv2
import subprocess
import os
import time
import sys

def check_system_info():
    """시스템 정보 확인"""
    print("=== 시스템 정보 ===")
    
    try:
        # 라즈베리파이 모델 확인
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().strip()
        print(f"라즈베리파이 모델: {model}")
    except:
        print("라즈베리파이 모델 정보를 읽을 수 없습니다.")
    
    # OS 정보
    try:
        result = subprocess.run(['uname', '-a'], capture_output=True, text=True)
        print(f"OS 정보: {result.stdout.strip()}")
    except:
        print("OS 정보를 확인할 수 없습니다.")

def check_camera_modules():
    """카메라 관련 커널 모듈 확인"""
    print("\n=== 카메라 모듈 확인 ===")
    
    try:
        result = subprocess.run(['lsmod'], capture_output=True, text=True)
        modules = result.stdout
        
        camera_modules = ['bcm2835-v4l2', 'v4l2_common', 'videodev', 'v4l2loopback']
        found_modules = []
        
        for module in camera_modules:
            if module in modules:
                found_modules.append(module)
                print(f"✅ {module} 모듈이 로드되어 있습니다.")
            else:
                print(f"❌ {module} 모듈이 로드되어 있지 않습니다.")
        
        return found_modules
        
    except Exception as e:
        print(f"모듈 확인 중 오류: {e}")
        return []

def check_camera_devices():
    """카메라 장치 확인"""
    print("\n=== 카메라 장치 확인 ===")
    
    video_devices = []
    for i in range(10):
        device_path = f"/dev/video{i}"
        if os.path.exists(device_path):
            # 장치 권한 확인
            stat = os.stat(device_path)
            mode = oct(stat.st_mode)[-3:]
            video_devices.append((device_path, mode))
            print(f"✅ {device_path} (권한: {mode})")
        else:
            print(f"❌ {device_path} 없음")
    
    return video_devices

def check_camera_config():
    """카메라 설정 확인"""
    print("\n=== 카메라 설정 확인 ===")
    
    try:
        # vcgencmd로 카메라 상태 확인
        result = subprocess.run(['vcgencmd', 'get_camera'], capture_output=True, text=True)
        print(f"카메라 상태: {result.stdout.strip()}")
    except:
        print("vcgencmd를 사용할 수 없습니다.")
    
    try:
        # config.txt 확인
        with open('/boot/config.txt', 'r') as f:
            config_content = f.read()
        
        camera_settings = [
            'camera_auto_detect=1',
            'dtoverlay=imx219',
            'dtoverlay=ov5647',
            'gpu_mem=128',
            'start_x=1'
        ]
        
        print("\nconfig.txt 설정:")
        for setting in camera_settings:
            if setting in config_content:
                print(f"✅ {setting}")
            else:
                print(f"❌ {setting} 없음")
                
    except Exception as e:
        print(f"config.txt 확인 중 오류: {e}")

def test_camera_with_different_settings():
    """다양한 설정으로 카메라 테스트"""
    print("\n=== 카메라 테스트 (다양한 설정) ===")
    
    # 테스트할 설정들
    test_configs = [
        {"index": 0, "width": 640, "height": 480, "fps": 30},
        {"index": 0, "width": 1280, "height": 720, "fps": 30},
        {"index": 0, "width": 1920, "height": 1080, "fps": 30},
        {"index": 0, "width": 640, "height": 480, "fps": 10},
    ]
    
    for i, config in enumerate(test_configs):
        print(f"\n--- 테스트 {i+1}: {config} ---")
        
        cap = cv2.VideoCapture(config["index"])
        
        if not cap.isOpened():
            print(f"❌ 카메라 {config['index']}를 열 수 없습니다.")
            continue
        
        # 설정 적용
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config["width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config["height"])
        cap.set(cv2.CAP_PROP_FPS, config["fps"])
        
        # 실제 설정값 확인
        actual_width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        
        print(f"요청: {config['width']}x{config['height']} @ {config['fps']}fps")
        print(f"실제: {actual_width}x{actual_height} @ {actual_fps}fps")
        
        # 프레임 읽기 테스트
        success_count = 0
        total_attempts = 10
        
        for attempt in range(total_attempts):
            ret, frame = cap.read()
            if ret:
                success_count += 1
                print(f"프레임 {attempt+1}: ✅ 성공")
            else:
                print(f"프레임 {attempt+1}: ❌ 실패")
            time.sleep(0.1)
        
        success_rate = (success_count / total_attempts) * 100
        print(f"성공률: {success_rate:.1f}% ({success_count}/{total_attempts})")
        
        if success_rate > 50:
            print("✅ 이 설정으로 카메라가 작동합니다!")
            cap.release()
            return config
        else:
            print("❌ 이 설정으로는 카메라가 제대로 작동하지 않습니다.")
        
        cap.release()
    
    return None

def fix_camera_issues():
    """카메라 문제 해결 시도"""
    print("\n=== 카메라 문제 해결 ===")
    
    # 1. 필요한 모듈 로드
    print("1. 필요한 모듈 로드 중...")
    modules_to_load = ['bcm2835-v4l2']
    
    for module in modules_to_load:
        try:
            result = subprocess.run(['sudo', 'modprobe', module], capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✅ {module} 모듈 로드 성공")
            else:
                print(f"❌ {module} 모듈 로드 실패: {result.stderr}")
        except Exception as e:
            print(f"❌ {module} 모듈 로드 중 오류: {e}")
    
    # 2. 장치 권한 확인 및 수정
    print("\n2. 장치 권한 확인 중...")
    video_devices = check_camera_devices()
    
    for device_path, mode in video_devices:
        if mode != '666':
            print(f"권한 수정 중: {device_path}")
            try:
                subprocess.run(['sudo', 'chmod', '666', device_path])
                print(f"✅ {device_path} 권한 수정 완료")
            except Exception as e:
                print(f"❌ {device_path} 권한 수정 실패: {e}")

def main():
    """메인 함수"""
    print("라즈베리파이 카메라 디버깅 도구")
    print("=" * 50)
    
    # 1. 시스템 정보 확인
    check_system_info()
    
    # 2. 카메라 모듈 확인
    modules = check_camera_modules()
    
    # 3. 카메라 장치 확인
    devices = check_camera_devices()
    
    # 4. 카메라 설정 확인
    check_camera_config()
    
    # 5. 문제 해결 시도
    if not devices:
        print("\n❌ 카메라 장치가 없습니다.")
        fix_camera_issues()
        devices = check_camera_devices()
    
    # 6. 카메라 테스트
    if devices:
        working_config = test_camera_with_different_settings()
        
        if working_config:
            print(f"\n🎉 성공! 작동하는 설정: {working_config}")
            print("이 설정을 main.py에 적용하세요.")
        else:
            print("\n❌ 모든 설정에서 카메라가 작동하지 않습니다.")
            print("다음을 시도해보세요:")
            print("1. sudo reboot")
            print("2. sudo raspi-config -> Interface Options -> Camera -> Enable")
            print("3. 카메라 케이블 재연결")
    else:
        print("\n❌ 카메라 장치를 찾을 수 없습니다.")

if __name__ == "__main__":
    main()
