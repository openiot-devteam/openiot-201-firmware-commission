#!/usr/bin/env python3
"""
간단한 카메라 테스트 스크립트
카메라가 제대로 작동하는지 확인합니다.
"""

import cv2
import time
import os

def test_opencv_camera():
    """OpenCV 카메라 테스트"""
    print("=== OpenCV 카메라 테스트 ===")
    
    # 사용 가능한 비디오 장치 확인
    camera_devices = []
    for i in range(5):
        if os.path.exists(f'/dev/video{i}'):
            camera_devices.append(i)
    
    print(f"발견된 비디오 장치: {camera_devices}")
    
    if not camera_devices:
        print("❌ 사용 가능한 비디오 장치가 없습니다.")
        return False
    
    # 각 장치로 카메라 열기 시도
    for device_index in camera_devices:
        print(f"\n비디오 장치 {device_index} 테스트 중...")
        
        cap = cv2.VideoCapture(device_index)
        if not cap.isOpened():
            print(f"  ❌ 장치 {device_index} 열기 실패")
            continue
        
        # 카메라 정보 확인
        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        print(f"  ✅ 장치 {device_index} 열기 성공")
        print(f"    해상도: {width}x{height}")
        print(f"    FPS: {fps}")
        
        # 5초간 프레임 캡처 테스트
        print("  5초간 프레임 캡처 테스트...")
        start_time = time.time()
        frame_count = 0
        
        while time.time() - start_time < 5:
            ret, frame = cap.read()
            if ret and frame is not None:
                frame_count += 1
                print(f"    프레임 {frame_count}: {frame.shape}")
                
                # 첫 번째 프레임을 파일로 저장
                if frame_count == 1:
                    cv2.imwrite(f'test_frame_device_{device_index}.jpg', frame)
                    print(f"    첫 번째 프레임을 test_frame_device_{device_index}.jpg로 저장했습니다.")
                
                time.sleep(0.1)
            else:
                print(f"    프레임 읽기 실패")
                break
        
        print(f"  총 {frame_count}개 프레임 캡처 완료")
        cap.release()
        
        if frame_count > 0:
            print(f"✅ 장치 {device_index} 테스트 성공!")
            return True
    
    print("❌ 모든 장치에서 카메라 테스트 실패")
    return False

def test_picamera2():
    """Picamera2 테스트"""
    print("\n=== Picamera2 테스트 ===")
    
    try:
        from picamera2 import Picamera2
        print("✅ Picamera2 모듈 임포트 성공")
        
        picam2 = Picamera2()
        print("✅ Picamera2 객체 생성 성공")
        
        # 간단한 설정
        config = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 15}
        )
        
        picam2.configure(config)
        print("✅ Picamera2 설정 성공")
        
        picam2.start()
        print("✅ Picamera2 시작 성공")
        
        # 3초간 프레임 캡처 테스트
        print("3초간 프레임 캡처 테스트...")
        start_time = time.time()
        frame_count = 0
        
        while time.time() - start_time < 3:
            frame = picam2.capture_array()
            if frame is not None:
                frame_count += 1
                print(f"  프레임 {frame_count}: {frame.shape}")
                
                # 첫 번째 프레임을 파일로 저장
                if frame_count == 1:
                    cv2.imwrite('test_frame_picamera2.jpg', cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
                    print("  첫 번째 프레임을 test_frame_picamera2.jpg로 저장했습니다.")
                
                time.sleep(0.1)
            else:
                print("  프레임 캡처 실패")
                break
        
        print(f"총 {frame_count}개 프레임 캡처 완료")
        picam2.stop()
        picam2.close()
        
        if frame_count > 0:
            print("✅ Picamera2 테스트 성공!")
            return True
        else:
            print("❌ Picamera2 프레임 캡처 실패")
            return False
            
    except ImportError:
        print("❌ Picamera2 모듈을 찾을 수 없습니다.")
        return False
    except Exception as e:
        print(f"❌ Picamera2 테스트 실패: {e}")
        return False

def main():
    """메인 함수"""
    print("=== 카메라 테스트 시작 ===")
    
    # OpenCV 테스트
    opencv_success = test_opencv_camera()
    
    # Picamera2 테스트
    picamera2_success = test_picamera2()
    
    print("\n=== 테스트 결과 요약 ===")
    print(f"OpenCV: {'✅ 성공' if opencv_success else '❌ 실패'}")
    print(f"Picamera2: {'✅ 성공' if picamera2_success else '❌ 실패'}")
    
    if opencv_success or picamera2_success:
        print("\n🎉 카메라가 정상적으로 작동합니다!")
        print("웹 서버를 실행할 수 있습니다.")
    else:
        print("\n❌ 카메라에 문제가 있습니다.")
        print("하드웨어 연결과 설정을 확인해주세요.")

if __name__ == "__main__":
    main()
