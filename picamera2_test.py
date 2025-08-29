#!/usr/bin/env python3
"""
Picamera2 간단 테스트 스크립트 (GUI 없음)
"""

import cv2
import time
from picamera2 import Picamera2

def picamera2_test():
    """Picamera2 간단 테스트 (GUI 없음)"""
    print("=== Picamera2 간단 테스트 (GUI 없음) ===")
    
    try:
        # Picamera2 초기화
        print("Picamera2 초기화 중...")
        picam2 = Picamera2()
        
        # 카메라 설정
        config = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 30}
        )
        
        print("카메라 설정 적용 중...")
        picam2.configure(config)
        
        print("카메라 시작 중...")
        picam2.start()
        
        print("✅ Picamera2가 성공적으로 시작되었습니다!")
        print("카메라 해상도: 640x480")
        print("FPS: 30")
        
        # 5초간 카메라 테스트 (프레임 캡처만)
        print("5초간 카메라 프레임을 테스트합니다...")
        start_time = time.time()
        frame_count = 0
        
        try:
            while time.time() - start_time < 5:
                # 프레임 캡처
                frame = picam2.capture_array()
                frame_count += 1
                
                if frame is None:
                    print("프레임을 읽을 수 없습니다.")
                    break
                
                # 프레임 정보 출력 (1초마다)
                if frame_count % 30 == 0:  # 30fps 기준으로 1초마다
                    elapsed = time.time() - start_time
                    print(f"✅ 프레임 {frame_count} 캡처 성공 (경과: {elapsed:.1f}초)")
                    
        except Exception as e:
            print(f"카메라 테스트 중 오류: {e}")
        
        finally:
            picam2.stop()
            picam2.close()
            
        print(f"✅ Picamera2 테스트가 완료되었습니다! (총 {frame_count}개 프레임 캡처)")
        return True
        
    except Exception as e:
        print(f"❌ Picamera2 초기화 실패: {e}")
        print("다음을 확인하세요:")
        print("1. 라즈베리 카메라3가 연결되어 있는지")
        print("2. 카메라가 활성화되어 있는지 (sudo raspi-config)")
        print("3. 시스템을 재부팅했는지")
        print("4. picamera2가 설치되어 있는지 (sudo apt-get install python3-picamera2)")
        return False

if __name__ == "__main__":
    picamera2_test()
