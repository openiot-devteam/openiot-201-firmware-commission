#!/usr/bin/env python3
"""
간단한 카메라 테스트 스크립트
카메라가 제대로 작동하는지 확인하고 화면 표시 문제를 진단합니다.
"""

import cv2
import os
import sys

def test_camera():
    """카메라 테스트 함수"""
    print("=== 카메라 테스트 시작 ===")
    
    # GUI 환경 확인
    display = os.environ.get('DISPLAY')
    print(f"DISPLAY 환경변수: {display}")
    
    if not display:
        print("⚠️  GUI 환경이 감지되지 않습니다!")
        print("카메라 화면을 보려면 다음 중 하나를 시도하세요:")
        print("1. VNC나 원격 데스크톱으로 연결")
        print("2. 직접 라즈베리파이에 모니터 연결")
        print("3. X11 포워딩 사용 (ssh -X)")
        print("4. 터미널에서만 실행하려면 Enter를 누르세요...")
        input()
    
    # 카메라 열기
    print("카메라를 열고 있습니다...")
    cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("❌ 카메라를 열 수 없습니다!")
        print("다음을 확인하세요:")
        print("1. 카메라가 연결되어 있는지")
        print("2. 카메라가 활성화되어 있는지 (sudo raspi-config)")
        print("3. 시스템을 재부팅했는지")
        return False
    
    print("✅ 카메라가 열렸습니다!")
    
    # 카메라 정보 출력
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"카메라 해상도: {width}x{height}, FPS: {fps}")
    
    # 창 설정
    window_name = 'Camera Test'
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 800, 600)
    
    print("\n카메라 화면이 표시됩니다. 'q'를 누르면 종료됩니다.")
    print("화면이 보이지 않으면 GUI 환경 문제일 수 있습니다.")
    
    frame_count = 0
    
    try:
        while True:
            ret, frame = cap.read()
            frame_count += 1
            
            if not ret:
                print(f"❌ 프레임 {frame_count}을 읽을 수 없습니다.")
                if frame_count > 10:
                    print("카메라에서 프레임을 읽을 수 없어 종료합니다.")
                    break
                continue
            
            # 프레임 정보 표시
            info_text = f"Frame: {frame_count} | Size: {frame.shape[1]}x{frame.shape[0]}"
            cv2.putText(frame, info_text, (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            
            # 종료 안내
            cv2.putText(frame, "Press 'q' to quit", (10, frame.shape[0] - 20), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            
            # 화면에 표시
            try:
                cv2.imshow(window_name, frame)
                
                # 창이 제대로 표시되었는지 확인
                if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                    print("⚠️  카메라 창이 표시되지 않습니다!")
                    print("GUI 환경을 확인하거나 VNC를 사용하세요.")
                    break
                    
            except Exception as e:
                print(f"⚠️  화면 표시 오류: {e}")
                print("터미널에서만 실행 중입니다.")
                break
            
            # 'q' 키를 누르면 종료
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
            # 100프레임마다 상태 출력
            if frame_count % 100 == 0:
                print(f"프레임 처리 중... ({frame_count})")
                
    except KeyboardInterrupt:
        print("\n프로그램이 중단되었습니다.")
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cv2.destroyAllWindows()
        cap.release()
        print("카메라 테스트가 종료되었습니다.")
    
    return True

def test_picamera2():
    """Picamera2 테스트 함수"""
    print("\n=== Picamera2 테스트 시작 ===")
    
    try:
        from picamera2 import Picamera2
        print("✅ Picamera2 모듈을 가져올 수 있습니다.")
        
        picam2 = Picamera2()
        config = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"}
        )
        
        picam2.configure(config)
        picam2.start()
        print("✅ Picamera2가 성공적으로 시작되었습니다!")
        
        # 한 프레임 캡처
        frame = picam2.capture_array()
        if frame is not None:
            print(f"✅ 프레임 캡처 성공: {frame.shape}")
        else:
            print("❌ 프레임 캡처 실패")
        
        picam2.stop()
        picam2.close()
        print("Picamera2 테스트 완료")
        
    except ImportError:
        print("❌ Picamera2 모듈을 가져올 수 없습니다.")
        print("설치: sudo apt-get install python3-picamera2")
    except Exception as e:
        print(f"❌ Picamera2 테스트 실패: {e}")

if __name__ == "__main__":
    print("카메라 테스트를 시작합니다...")
    
    # OpenCV 테스트
    test_camera()
    
    # Picamera2 테스트
    test_picamera2()
    
    print("\n=== 테스트 완료 ===")
    print("문제가 지속되면 다음을 확인하세요:")
    print("1. GUI 환경 (VNC, 원격 데스크톱)")
    print("2. 카메라 권한 및 활성화")
    print("3. 시스템 재부팅")
