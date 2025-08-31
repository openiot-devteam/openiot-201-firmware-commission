#!/usr/bin/env python3
"""
CM5 + IO 보드 + Pi Camera 3 설정 스크립트
카메라 활성화 및 최적화를 위한 설정을 자동으로 적용합니다.
"""

import os
import subprocess
import sys
import time

def run_command(command, description, sudo=False):
    """명령어 실행 및 결과 출력"""
    print(f"\n=== {description} ===")
    print(f"명령어: {command}")
    
    if sudo:
        command = f"sudo {command}"
    
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
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

def check_cm5_environment():
    """CM5 환경 확인"""
    print("=== CM5 + IO 보드 환경 확인 ===")
    
    # 하드웨어 모델 확인
    if os.path.exists('/proc/device-tree/model'):
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().strip()
            print(f"하드웨어: {model}")
            
            if 'CM5' in model.upper():
                print("✅ CM5 환경이 감지되었습니다.")
                return True
            else:
                print("⚠️  CM5 환경이 아닙니다. 일반 라즈베리파이 설정을 적용합니다.")
                return False
    
    return False

def check_camera_modules():
    """카메라 관련 커널 모듈 확인 및 로드"""
    print("\n=== 카메라 커널 모듈 확인 ===")
    
    # 필요한 모듈들
    required_modules = [
        'bcm2835-v4l2',
        'videodev',
        'media'
    ]
    
    for module in required_modules:
        # 모듈이 로드되어 있는지 확인
        result = subprocess.run(f"lsmod | grep {module}", shell=True, capture_output=True, text=True)
        if result.stdout:
            print(f"✅ {module}: 이미 로드됨")
        else:
            print(f"📥 {module}: 로드 중...")
            if run_command(f"modprobe {module}", f"{module} 모듈 로드", sudo=True):
                print(f"✅ {module}: 로드 성공")
            else:
                print(f"❌ {module}: 로드 실패")

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
        print("카메라가 연결되어 있는지 확인하세요.")
    
    # /dev/media* 장치 확인
    media_devices = []
    for i in range(10):
        if os.path.exists(f'/dev/media{i}'):
            media_devices.append(f'/dev/media{i}')
    
    if media_devices:
        print(f"✅ 발견된 미디어 장치: {media_devices}")
    else:
        print("❌ 미디어 장치를 찾을 수 없습니다")

def configure_camera_interface():
    """카메라 인터페이스 설정"""
    print("\n=== 카메라 인터페이스 설정 ===")
    
    # config.txt 확인 및 수정
    config_file = '/boot/config.txt'
    if os.path.exists(config_file):
        print("config.txt 파일을 확인하고 카메라 설정을 추가합니다...")
        
        with open(config_file, 'r') as f:
            content = f.read()
        
        # 필요한 설정들
        required_settings = [
            'camera_auto_detect=1',
            'dtoverlay=imx708',
            'gpu_mem=128',
            'start_x=1'
        ]
        
        new_settings = []
        for setting in required_settings:
            if setting not in content:
                new_settings.append(setting)
        
        if new_settings:
            print(f"추가할 설정: {new_settings}")
            
            # 백업 생성
            backup_file = f"{config_file}.backup.{int(time.time())}"
            run_command(f"cp {config_file} {backup_file}", "config.txt 백업 생성", sudo=True)
            
            # 설정 추가
            with open(config_file, 'a') as f:
                f.write('\n# Pi Camera 3 설정\n')
                for setting in new_settings:
                    f.write(f'{setting}\n')
            
            print("✅ config.txt에 카메라 설정이 추가되었습니다.")
            print("⚠️  변경사항을 적용하려면 시스템을 재부팅해야 합니다.")
        else:
            print("✅ 모든 필요한 카메라 설정이 이미 적용되어 있습니다.")
    else:
        print("❌ config.txt 파일을 찾을 수 없습니다.")

def check_picamera2_installation():
    """Picamera2 설치 상태 확인 및 설치"""
    print("\n=== Picamera2 설치 상태 확인 ===")
    
    try:
        import picamera2
        print("✅ Python picamera2 모듈이 이미 설치되어 있습니다.")
        version = getattr(picamera2, '__version__', '알 수 없음')
        print(f"  버전: {version}")
        return True
    except ImportError:
        print("❌ Python picamera2 모듈이 설치되지 않았습니다.")
        print("설치를 시작합니다...")
        
        # 시스템 업데이트
        if run_command("apt-get update", "시스템 패키지 업데이트", sudo=True):
            print("✅ 시스템 업데이트 완료")
        else:
            print("❌ 시스템 업데이트 실패")
            return False
        
        # Picamera2 설치
        if run_command("apt-get install -y python3-picamera2", "Picamera2 설치", sudo=True):
            print("✅ Picamera2 설치 완료")
            return True
        else:
            print("❌ Picamera2 설치 실패")
            return False

def test_camera_access():
    """카메라 접근 테스트"""
    print("\n=== 카메라 접근 테스트 ===")
    
    try:
        from picamera2 import Picamera2
        print("Picamera2로 카메라 테스트 중...")
        
        picam2 = Picamera2()
        config = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 15}
        )
        
        picam2.configure(config)
        picam2.start()
        
        # 프레임 캡처 테스트
        frame = picam2.capture_array()
        if frame is not None:
            print(f"✅ 카메라 테스트 성공! 프레임 크기: {frame.shape}")
            picam2.stop()
            picam2.close()
            return True
        else:
            print("❌ 프레임 캡처 실패")
            return False
            
    except Exception as e:
        print(f"❌ Picamera2 테스트 실패: {e}")
        return False

def setup_camera_permissions():
    """카메라 권한 설정"""
    print("\n=== 카메라 권한 설정 ===")
    
    current_user = os.getenv('USER', 'unknown')
    print(f"현재 사용자: {current_user}")
    
    # video 그룹에 사용자 추가
    if run_command(f"usermod -a -G video {current_user}", "사용자를 video 그룹에 추가", sudo=True):
        print("✅ 사용자가 video 그룹에 추가되었습니다.")
        print("⚠️  변경사항을 적용하려면 재로그인이 필요합니다.")
    else:
        print("❌ video 그룹 추가 실패")
    
    # 카메라 장치 권한 확인
    video_devices = []
    for i in range(10):
        if os.path.exists(f'/dev/video{i}'):
            video_devices.append(f'/dev/video{i}')
    
    for device in video_devices:
        # 권한을 666으로 설정
        if run_command(f"chmod 666 {device}", f"{device} 권한 설정", sudo=True):
            print(f"✅ {device} 권한 설정 완료")
        else:
            print(f"❌ {device} 권한 설정 실패")

def suggest_next_steps():
    """다음 단계 제안"""
    print("\n=== 다음 단계 ===")
    print("1. 시스템 재부팅:")
    print("   sudo reboot")
    
    print("\n2. 재부팅 후 카메라 테스트:")
    print("   python3 cm5_camera_setup.py --test")
    
    print("\n3. 웹 카메라 서버 실행:")
    print("   python3 web_camera_server.py")
    
    print("\n4. 문제가 지속되면:")
    print("   - Pi Camera 3 연결 상태 확인")
    print("   - IO 보드 펌웨어 업데이트")
    print("   - IO 보드에서 카메라 인터페이스 활성화")

def main():
    """메인 함수"""
    print("=== CM5 + IO 보드 + Pi Camera 3 설정 도구 ===")
    print("카메라 활성화 및 최적화를 위한 설정을 자동으로 적용합니다.\n")
    
    # CM5 환경 확인
    is_cm5 = check_cm5_environment()
    
    # 카메라 모듈 확인 및 로드
    check_camera_modules()
    
    # 카메라 장치 확인
    check_camera_devices()
    
    # 카메라 인터페이스 설정
    configure_camera_interface()
    
    # Picamera2 설치 확인
    picamera2_ok = check_picamera2_installation()
    
    if picamera2_ok:
        # 카메라 권한 설정
        setup_camera_permissions()
        
        # 카메라 접근 테스트
        camera_ok = test_camera_access()
        if camera_ok:
            print("\n🎉 카메라 설정이 완료되었습니다!")
        else:
            print("\n⚠️  카메라 접근에 문제가 있습니다.")
    else:
        print("\n⚠️  Picamera2 설치에 문제가 있습니다.")
    
    # 다음 단계 제안
    suggest_next_steps()
    
    print("\n=== 설정 완료 ===")

if __name__ == "__main__":
    # 명령행 인수 확인
    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        # 테스트 모드
        print("=== 카메라 테스트 모드 ===")
        if test_camera_access():
            print("🎉 카메라가 정상적으로 작동합니다!")
        else:
            print("❌ 카메라 테스트에 실패했습니다.")
    else:
        # 전체 설정 모드
        main()
