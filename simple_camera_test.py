#!/usr/bin/env python3
"""
간단한 카메라 테스트 스크립트
"""

import cv2
import time

def simple_camera_test():
    """간단한 카메라 테스트"""
    print("=== 간단한 카메라 테스트 ===")
    
    # 카메라 장치 시도
    for camera_index in range(5):  # video0부터 video4까지 시도
        print(f"\n카메라 {camera_index} 시도 중...")
        
        cap = cv2.VideoCapture(camera_index)
        
        if not cap.isOpened():
            print(f"카메라 {camera_index}를 열 수 없습니다.")
            cap.release()
            continue
        
        print(f"✅ 카메라 {camera_index}가 열렸습니다!")
        
        # 카메라 정보 출력
        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        print(f"해상도: {width}x{height}")
        print(f"FPS: {fps}")
        
        # 5초간 카메라 화면 표시
        print("5초간 카메라 화면을 표시합니다...")
        start_time = time.time()
        
        try:
            while time.time() - start_time < 5:
                ret, frame = cap.read()
                
                if not ret:
                    print("프레임을 읽을 수 없습니다.")
                    break
                
                # 화면에 텍스트 표시
                cv2.putText(frame, f"Camera {camera_index} - Press 'q' to quit", 
                           (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                
                cv2.imshow(f'Camera {camera_index} Test', frame)
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
                    
        except Exception as e:
            print(f"오류 발생: {e}")
        
        finally:
            cap.release()
            cv2.destroyAllWindows()
            
        # 성공한 카메라를 찾았으면 종료
        break
    
    else:
        print("\n❌ 사용 가능한 카메라를 찾을 수 없습니다.")
        print("다음을 확인하세요:")
        print("1. 라즈베리 카메라3가 연결되어 있는지")
        print("2. 카메라가 활성화되어 있는지")
        print("3. 시스템을 재부팅했는지")
        return False
    
    print("\n✅ 카메라 테스트가 완료되었습니다!")
    return True

if __name__ == "__main__":
    simple_camera_test()
