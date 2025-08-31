#!/usr/bin/env python3
"""
카메라 문제 진단 스크립트
CM5 + IO 보드에서 카메라가 제대로 작동하지 않는 문제를 해결합니다.
"""

import cv2
import os
import time
import subprocess
import numpy as np

def check_system_info():
    """시스템 정보 확인"""
    print("=== 시스템 정보 확인 ===")
    
    # OS 정보
    try:
        with open('/etc/os-release', 'r') as f:
            os_info = f.read()
            print("OS 정보:")
            for line in os_info.split('\n'):
                if line.startswith('PRETTY_NAME'):
                    print(f"  {line}")
    except:
        print("OS 정보 확인 불가")
    
    # 메모리 정보
    try:
        with open('/proc/meminfo', 'r') as f:
            mem_info = f.read()
            mem_total = [line for line in mem_info.split('\n') if 'MemTotal' in line]
            mem_available = [line for line in mem_info.split('\n') if 'MemAvailable' in line]
            if mem_total:
                print(f"메모리: {mem_total[0]}")
            if mem_available:
                print(f"  {mem_available[0]}")
    except:
        print("메모리 정보 확인 불가")
    
    # CPU 정보
    try:
        with open('/proc/cpuinfo', 'r') as f:
            cpu_info = f.read()
            model_name = [line for line in cpu_info.split('\n') if 'Model' in line]
            if model_name:
                print(f"CPU: {model_name[0]}")
    except:
        print("CPU 정보 확인 불가")
    
    print()

def check_video_devices():
    """비디오 장치 상세 확인"""
    print("=== 비디오 장치 상세 확인 ===")
    
    # v4l2-ctl 설치 확인
    try:
        result = subprocess.run(['v4l2-ctl', '--version'], capture_output=True, text=True)
        print("✅ v4l2-ctl 설치됨")
    except FileNotFoundError:
        print("❌ v4l2-ctl 설치 필요: sudo apt-get install v4l-utils")
        return
    
    # 비디오 장치 목록
    try:
        result = subprocess.run(['v4l2-ctl', '--list-devices'], capture_output=True, text=True)
        print("비디오 장치 목록:")
        print(result.stdout)
    except Exception as e:
        print(f"v4l2-ctl 실행 오류: {e}")
    
    # 각 장치의 상세 정보
    for i in range(10):
        device_path = f'/dev/video{i}'
        if os.path.exists(device_path):
            print(f"\n--- /dev/video{i} 상세 정보 ---")
            
            # 장치 정보
            try:
                result = subprocess.run(['v4l2-ctl', '-d', str(i), '--all'], 
                                     capture_output=True, text=True)
                print(result.stdout[:500] + "..." if len(result.stdout) > 500 else result.stdout)
            except Exception as e:
                print(f"장치 정보 확인 실패: {e}")
            
            # 지원하는 포맷
            try:
                result = subprocess.run(['v4l2-ctl', '-d', str(i), '--list-formats-ext'], 
                                     capture_output=True, text=True)
                print("지원하는 포맷:")
                print(result.stdout)
            except Exception as e:
                print(f"포맷 정보 확인 실패: {e}")
    
    print()

def test_camera_direct():
    """카메라 직접 테스트"""
    print("=== 카메라 직접 테스트 ===")
    
    for device_index in range(5):
        device_path = f'/dev/video{device_index}'
        if not os.path.exists(device_path):
            continue
            
        print(f"\n--- /dev/video{device_index} 직접 테스트 ---")
        
        try:
            # 권한 확인
            stat = os.stat(device_path)
            mode = oct(stat.st_mode)[-3:]
            print(f"권한: {mode}")
            
            # 카메라 열기
            cap = cv2.VideoCapture(device_index)
            if not cap.isOpened():
                print("❌ 카메라 열기 실패")
                continue
            
            print("✅ 카메라 열기 성공")
            
            # 기본 속성 확인
            width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            fps = cap.get(cv2.CAP_PROP_FPS)
            print(f"기본 해상도: {width}x{height}, FPS: {fps}")
            
            # 버퍼 크기 설정
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            print("✅ 버퍼 크기 설정 완료")
            
            # 낮은 해상도로 설정
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            cap.set(cv2.CAP_PROP_FPS, 10)
            print("✅ 낮은 해상도 설정 완료")
            
            # 설정 적용 대기
            time.sleep(1)
            
            # 실제 설정값 확인
            actual_width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            actual_height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            print(f"실제 설정: {actual_width}x{actual_height}, FPS: {actual_fps}")
            
            # 프레임 읽기 테스트
            print("프레임 읽기 테스트 시작...")
            success_count = 0
            total_attempts = 10
            
            for attempt in range(total_attempts):
                ret, frame = cap.read()
                if ret and frame is not None:
                    success_count += 1
                    print(f"  시도 {attempt+1}: ✅ 성공 - {frame.shape}")
                    
                    # 첫 번째 성공한 프레임 저장
                    if attempt == 0:
                        filename = f"test_frame_device_{device_index}.jpg"
                        cv2.imwrite(filename, frame)
                        print(f"  ✅ 프레임 저장: {filename}")
                else:
                    print(f"  시도 {attempt+1}: ❌ 실패")
                
                time.sleep(0.2)
            
            success_rate = (success_count / total_attempts) * 100
            print(f"성공률: {success_count}/{total_attempts} ({success_rate:.1f}%)")
            
            if success_rate > 50:
                print("🎉 이 장치로 카메라를 사용할 수 있습니다!")
                cap.release()
                return device_index
            else:
                print("⚠️  이 장치는 안정적이지 않습니다.")
            
            cap.release()
            
        except Exception as e:
            print(f"❌ 테스트 중 오류: {e}")
            if 'cap' in locals():
                cap.release()
    
    print("\n❌ 모든 장치에서 안정적인 카메라를 찾을 수 없습니다.")
    return None

def test_camera_with_different_formats():
    """다양한 포맷으로 카메라 테스트"""
    print("\n=== 다양한 포맷으로 카메라 테스트 ===")
    
    # 테스트할 포맷들
    formats = [
        ('YUYV', cv2.VideoWriter_fourcc('Y', 'U', 'Y', 'V')),
        ('MJPG', cv2.VideoWriter_fourcc('M', 'J', 'P', 'G')),
        ('RGB3', cv2.VideoWriter_fourcc('R', 'G', 'B', '3')),
        ('BGR3', cv2.VideoWriter_fourcc('B', 'G', 'R', '3'))
    ]
    
    for device_index in range(5):
        device_path = f'/dev/video{device_index}'
        if not os.path.exists(device_path):
            continue
            
        print(f"\n--- /dev/video{device_index} 포맷 테스트 ---")
        
        for format_name, fourcc in formats:
            try:
                cap = cv2.VideoCapture(device_index)
                if not cap.isOpened():
                    continue
                
                # 포맷 설정
                cap.set(cv2.CAP_PROP_FOURCC, fourcc)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
                cap.set(cv2.CAP_PROP_FPS, 10)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                
                time.sleep(0.5)
                
                # 프레임 읽기 테스트
                ret, frame = cap.read()
                if ret and frame is not None:
                    print(f"  ✅ {format_name}: 성공 - {frame.shape}")
                    
                    # 프레임 저장
                    filename = f"test_frame_{device_index}_{format_name}.jpg"
                    cv2.imwrite(filename, frame)
                    print(f"    저장: {filename}")
                else:
                    print(f"  ❌ {format_name}: 실패")
                
                cap.release()
                
            except Exception as e:
                print(f"  ❌ {format_name}: 오류 - {e}")
                if 'cap' in locals():
                    cap.release()

def main():
    """메인 함수"""
    print("=== CM5 + IO 보드 카메라 문제 진단 ===")
    print("카메라가 제대로 작동하지 않는 문제를 해결합니다.\n")
    
    # 1. 시스템 정보 확인
    check_system_info()
    
    # 2. 비디오 장치 상세 확인
    check_video_devices()
    
    # 3. 카메라 직접 테스트
    working_device = test_camera_direct()
    
    # 4. 다양한 포맷으로 테스트
    test_camera_with_different_formats()
    
    # 결과 요약
    print("\n" + "="*50)
    print("=== 진단 결과 요약 ===")
    
    if working_device is not None:
        print(f"✅ 작동하는 카메라 장치: /dev/video{working_device}")
        print("\n💡 해결 방법:")
        print(f"1. web_camera_server.py에서 device_index를 {working_device}로 설정")
        print("2. 낮은 해상도(320x240)로 시작")
        print("3. 버퍼 크기를 1로 설정")
    else:
        print("❌ 작동하는 카메라를 찾을 수 없습니다.")
        print("\n💡 문제 해결 방법:")
        print("1. sudo apt-get install v4l-utils")
        print("2. sudo chmod 666 /dev/video*")
        print("3. v4l2-ctl --list-devices 실행")
        print("4. 시스템 재부팅")
        print("5. 카메라 하드웨어 연결 확인")

if __name__ == "__main__":
    main()
