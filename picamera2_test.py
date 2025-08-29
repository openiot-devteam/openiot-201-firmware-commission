#!/usr/bin/env python3
"""
Picamera2 간단 테스트 스크립트
"""

import cv2
import time
from picamera2 import Picamera2

def picamera2_test():
    """Picamera2 간단 테스트"""
    print("=== Picamera2 간단 테스트 ===")
    
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
        
        # 5초간 카메라 화면 표시
        print("5초간 카메라 화면을 표시합니다...")
        start_time = time.time()
        
        try:
            while time.time() - start_time < 5:
                # 프레임 캡처
                frame = picam2.capture_array()
                
                if frame is None:
                    print("프레임을 읽을 수 없습니다.")
                    break
                
                # 화면에 텍스트 표시
                cv2.putText(frame, "Picamera2 Test - Press 'q' to quit", 
                           (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                
                cv2.imshow('Picamera2 Test', frame)
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
                    
        except Exception as e:
            print(f"카메라 테스트 중 오류: {e}")
        
        finally:
            picam2.stop()
            picam2.close()
            cv2.destroyAllWindows()
            
        print("✅ Picamera2 테스트가 완료되었습니다!")
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
