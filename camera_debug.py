#!/usr/bin/env python3
"""
Pi Camera 3 + CM5 + IO 보드 카메라 디버그 스크립트
카메라 연결 상태와 설정을 확인하고 문제를 해결합니다.
"""

import os
import subprocess
import time
import sys

def run_command(command, description):
    """명령어 실행 및 결과 출력"""
    print(f"\n=== {description} ===")
    print(f"명령어: {command}")
    
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=10)
        print(f"출력: {result.stdout}")
        if result.stderr:
            print(f"오류: {result.stderr}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("❌ 명령어 실행 시간 초과")
        return False
    except Exception as e:
        print(f"❌ 명령어 실행 오류: {e}")
        return False

def check_system_info():
    """시스템 정보 확인"""
    print("=== 시스템 정보 확인 ===")
    
    # OS 정보
    if os.path.exists('/etc/os-release'):
        with open('/etc/os-release', 'r') as f:
            for line in f:
                if line.startswith('PRETTY_NAME'):
                    print(f"OS: {line.split('=')[1].strip().strip('\"')}")
                    break
    
    # 하드웨어 정보
    if os.path.exists('/proc/device-tree/model'):
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().strip()
            print(f"하드웨어: {model}")
    
    # 커널 버전
    if os.path.exists('/proc/version'):
        with open('/proc/version', 'r') as f:
            version = f.read().strip()
            print(f"커널: {version}")

def check_camera_modules():
    """카메라 관련 커널 모듈 확인"""
    print("\n=== 카메라 커널 모듈 확인 ===")
    
    modules = [
        'bcm2835-v4l2',
        'v4l2loopback',
        'videodev',
        'media'
    ]
    
    for module in modules:
        result = subprocess.run(f"lsmod | grep {module}", shell=True, capture_output=True, text=True)
        if result.stdout:
            print(f"✅ {module}: 로드됨")
            print(f"  {result.stdout.strip()}")
        else:
            print(f"❌ {module}: 로드되지 않음")

def check_camera_devices():
    """카메라 장치 확인"""
    print("\n=== 카메라 장치 확인 ===")
    
    # /dev/video* 장치 확인
    video_devices = []
    for i in range(10):
        if os.path.exists(f'/dev/video{i}'):
            video_devices.append(f'/dev/video{i}')
    
    if video_devices:
        print(f"✅ 발견된 비디오 장치: {video_devices}")
        
        # 각 장치의 상세 정보 확인
        for device in video_devices:
            print(f"\n--- {device} 상세 정보 ---")
            run_command(f"v4l2-ctl --device={device} --all", f"{device} 정보")
    else:
        print("❌ 비디오 장치를 찾을 수 없습니다")
    
    # /dev/media* 장치 확인
    media_devices = []
    for i in range(10):
        if os.path.exists(f'/dev/media{i}'):
            media_devices.append(f'/dev/media{i}')
    
    if media_devices:
        print(f"✅ 발견된 미디어 장치: {media_devices}")
    else:
        print("❌ 미디어 장치를 찾을 수 없습니다")

def check_camera_config():
    """카메라 설정 확인"""
    print("\n=== 카메라 설정 확인 ===")
    
    # raspi-config 카메라 설정 확인
    if os.path.exists('/boot/config.txt'):
        with open('/boot/config.txt', 'r') as f:
            content = f.read()
            camera_enabled = 'camera_auto_detect=1' in content or 'dtoverlay=imx708' in content
            if camera_enabled:
                print("✅ 카메라가 config.txt에서 활성화됨")
                # 관련 설정 출력
                for line in content.split('\n'):
                    if 'camera' in line.lower() or 'imx708' in line.lower():
                        print(f"  {line.strip()}")
            else:
                print("❌ 카메라가 config.txt에서 활성화되지 않음")
    
    # dtoverlay 확인
    run_command("dtoverlay -l", "현재 로드된 디바이스 트리 오버레이")

def check_picamera2_installation():
    """Picamera2 설치 상태 확인"""
    print("\n=== Picamera2 설치 상태 확인 ===")
    
    try:
        import picamera2
        print("✅ Python picamera2 모듈 설치됨")
        
        # Picamera2 버전 확인
        version = getattr(picamera2, '__version__', '알 수 없음')
        print(f"  버전: {version}")
        
    except ImportError:
        print("❌ Python picamera2 모듈이 설치되지 않음")
        print("설치 명령어: sudo apt-get install python3-picamera2")
        return False
    
    # 시스템 Picamera2 확인
    if os.path.exists('/usr/bin/picamera2-hello'):
        print("✅ 시스템 Picamera2 도구 설치됨")
        run_command("picamera2-hello --help", "Picamera2 도구 도움말")
    else:
        print("❌ 시스템 Picamera2 도구가 설치되지 않음")
    
    return True

def test_camera_access():
    """카메라 접근 테스트"""
    print("\n=== 카메라 접근 테스트 ===")
    
    # Python으로 카메라 접근 시도
    try:
        from picamera2 import Picamera2
        print("Picamera2 초기화 시도 중...")
        
        picam2 = Picamera2()
        config = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"}
        )
        
        print("카메라 설정 적용 중...")
        picam2.configure(config)
        
        print("카메라 시작 중...")
        picam2.start()
        
        print("프레임 캡처 시도 중...")
        frame = picam2.capture_array()
        
        if frame is not None:
            print(f"✅ 카메라 접근 성공! 프레임 크기: {frame.shape}")
            picam2.stop()
            picam2.close()
            return True
        else:
            print("❌ 프레임 캡처 실패")
            return False
            
    except Exception as e:
        print(f"❌ Picamera2 테스트 실패: {e}")
        return False

def check_camera_permissions():
    """카메라 권한 확인"""
    print("\n=== 카메라 권한 확인 ===")
    
    # 현재 사용자 확인
    current_user = os.getenv('USER', 'unknown')
    print(f"현재 사용자: {current_user}")
    
    # video 그룹 확인
    try:
        result = subprocess.run("groups", shell=True, capture_output=True, text=True)
        if result.stdout:
            groups = result.stdout.strip().split()
            if 'video' in groups:
                print("✅ 사용자가 video 그룹에 속함")
            else:
                print("❌ 사용자가 video 그룹에 속하지 않음")
                print("해결 방법: sudo usermod -a -G video $USER")
        else:
            print("❌ 그룹 정보를 가져올 수 없음")
    except Exception as e:
        print(f"❌ 그룹 확인 오류: {e}")

def suggest_solutions():
    """문제 해결 방법 제안"""
    print("\n=== 문제 해결 방법 ===")
    
    print("1. 카메라 활성화:")
    print("   sudo raspi-config")
    print("   Interface Options > Camera > Enable")
    
    print("\n2. 시스템 재부팅:")
    print("   sudo reboot")
    
    print("\n3. 사용자를 video 그룹에 추가:")
    print("   sudo usermod -a -G video $USER")
    print("   (재로그인 필요)")
    
    print("\n4. Picamera2 재설치:")
    print("   sudo apt-get update")
    print("   sudo apt-get install python3-picamera2")
    
    print("\n5. 카메라 연결 확인:")
    print("   - Pi Camera 3가 올바르게 연결되어 있는지 확인")
    print("   - 케이블이 단단히 연결되어 있는지 확인")
    print("   - IO 보드의 카메라 인터페이스가 활성화되어 있는지 확인")
    
    print("\n6. CM5 + IO 보드 특별 설정:")
    print("   - IO 보드 펌웨어가 최신인지 확인")
    print("   - 카메라 인터페이스가 IO 보드에서 활성화되어 있는지 확인")

def main():
    """메인 함수"""
    print("=== Pi Camera 3 + CM5 + IO 보드 카메라 디버그 ===")
    print("카메라 문제를 진단하고 해결 방법을 제안합니다.\n")
    
    # 시스템 정보 확인
    check_system_info()
    
    # 카메라 관련 확인
    check_camera_modules()
    check_camera_devices()
    check_camera_config()
    check_camera_permissions()
    
    # Picamera2 확인
    picamera2_ok = check_picamera2_installation()
    
    if picamera2_ok:
        # 카메라 접근 테스트
        camera_ok = test_camera_access()
        if camera_ok:
            print("\n🎉 카메라가 정상적으로 작동합니다!")
        else:
            print("\n⚠️  카메라 접근에 문제가 있습니다.")
    else:
        print("\n⚠️  Picamera2 설치에 문제가 있습니다.")
    
    # 해결 방법 제안
    suggest_solutions()
    
    print("\n=== 디버그 완료 ===")
    print("위의 정보를 바탕으로 문제를 해결하세요.")

if __name__ == "__main__":
    main()
