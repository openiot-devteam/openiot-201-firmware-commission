#!/usr/bin/env python3
"""
QR 코드 인식 최적화 도구
자동 초점 조정 및 QR 코드 인식 성능 향상을 위한 도구입니다.
"""

import cv2
import numpy as np
from pyzbar import pyzbar
import time
import threading
from picamera2 import Picamera2
import os

class QROptimizer:
    def __init__(self):
        self.camera = None
        self.camera_type = None
        self.auto_focus_enabled = False
        self.qr_detection_history = []
        self.best_focus_position = None
        
    def initialize_camera(self):
        """카메라 초기화"""
        print("카메라 초기화 중...")
        
        try:
            # Picamera2 시도
            self.camera = Picamera2()
            config = self.camera.create_preview_configuration(
                main={"size": (1920, 1080), "format": "RGB888"},  # 고해상도로 변경
                controls={
                    "FrameRate": 30,
                    "AfMode": "Continuous",  # 자동 초점 모드
                    "AfRange": "Normal",
                    "AfSpeed": "Normal"
                }
            )
            
            self.camera.configure(config)
            self.camera.start()
            self.camera_type = "Picamera2"
            print("✅ Picamera2 카메라 초기화 성공")
            return True
            
        except Exception as e:
            print(f"Picamera2 초기화 실패: {e}")
            
            # OpenCV로 대안 시도
            try:
                self.camera = cv2.VideoCapture(0)
                if self.camera.isOpened():
                    # 고해상도 설정
                    self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                    self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                    self.camera.set(cv2.CAP_PROP_FPS, 30)
                    
                    # 자동 초점 설정 시도
                    self.camera.set(cv2.CAP_PROP_AUTOFOCUS, 1)
                    
                    self.camera_type = "OpenCV"
                    print("✅ OpenCV 카메라 초기화 성공")
                    return True
                else:
                    print("❌ OpenCV 카메라 초기화 실패")
                    return False
            except Exception as e2:
                print(f"OpenCV 초기화 실패: {e2}")
                return False
    
    def capture_frame(self):
        """프레임 캡처"""
        if self.camera_type == "Picamera2":
            frame = self.camera.capture_array()
            if frame is not None:
                return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        else:
            ret, frame = self.camera.read()
            if ret:
                return frame
        return None
    
    def enhance_image_for_qr(self, frame):
        """QR 코드 인식을 위한 이미지 향상"""
        if frame is None:
            return None
        
        # 그레이스케일 변환
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # 노이즈 제거
        denoised = cv2.fastNlMeansDenoising(gray)
        
        # 대비 향상
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(denoised)
        
        # 선명도 향상
        kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
        sharpened = cv2.filter2D(enhanced, -1, kernel)
        
        return sharpened
    
    def detect_qr_codes(self, frame):
        """QR 코드 감지 (향상된 버전)"""
        if frame is None:
            return []
        
        # 원본 이미지로 QR 코드 감지
        decoded_original = pyzbar.decode(frame)
        
        # 향상된 이미지로 QR 코드 감지
        enhanced = self.enhance_image_for_qr(frame)
        decoded_enhanced = pyzbar.decode(enhanced)
        
        # 결과 합치기
        all_results = []
        
        # 원본 결과 추가
        for obj in decoded_original:
            all_results.append({
                'data': obj.data.decode('utf-8'),
                'rect': obj.rect,
                'polygon': obj.polygon,
                'quality': 'original'
            })
        
        # 향상된 결과 추가 (중복 제거)
        for obj in decoded_enhanced:
            data = obj.data.decode('utf-8')
            # 중복 확인
            is_duplicate = any(result['data'] == data for result in all_results)
            if not is_duplicate:
                all_results.append({
                    'data': data,
                    'rect': obj.rect,
                    'polygon': obj.polygon,
                    'quality': 'enhanced'
                })
        
        return all_results
    
    def auto_focus_adjustment(self):
        """자동 초점 조정"""
        print("자동 초점 조정을 시작합니다...")
        
        if self.camera_type == "Picamera2":
            # Picamera2 자동 초점 설정
            try:
                # 자동 초점 모드 설정
                self.camera.set_controls({"AfMode": "Continuous"})
                print("✅ Picamera2 자동 초점 활성화")
                self.auto_focus_enabled = True
                return True
            except Exception as e:
                print(f"Picamera2 자동 초점 설정 실패: {e}")
                return False
        else:
            # OpenCV 자동 초점 설정
            try:
                self.camera.set(cv2.CAP_PROP_AUTOFOCUS, 1)
                print("✅ OpenCV 자동 초점 활성화")
                self.auto_focus_enabled = True
                return True
            except Exception as e:
                print(f"OpenCV 자동 초점 설정 실패: {e}")
                return False
    
    def manual_focus_test(self):
        """수동 초점 테스트"""
        print("수동 초점 테스트를 시작합니다...")
        print("QR 코드를 카메라에 보여주고 Enter를 누르세요.")
        
        focus_positions = [0, 25, 50, 75, 100]  # 초점 위치들
        best_position = None
        best_detection_count = 0
        
        for position in focus_positions:
            print(f"\n초점 위치 {position} 테스트 중...")
            
            if self.camera_type == "Picamera2":
                try:
                    self.camera.set_controls({"LensPosition": position})
                except:
                    print(f"초점 위치 {position} 설정 실패")
                    continue
            else:
                try:
                    self.camera.set(cv2.CAP_PROP_FOCUS, position)
                except:
                    print(f"초점 위치 {position} 설정 실패")
                    continue
            
            # 3초간 QR 코드 감지 테스트
            detection_count = 0
            start_time = time.time()
            
            while time.time() - start_time < 3:
                frame = self.capture_frame()
                if frame is not None:
                    qr_results = self.detect_qr_codes(frame)
                    if qr_results:
                        detection_count += len(qr_results)
                        print(f"  QR 코드 감지됨: {len(qr_results)}개")
                
                time.sleep(0.1)
            
            print(f"  초점 위치 {position}: {detection_count}개 감지")
            
            if detection_count > best_detection_count:
                best_detection_count = detection_count
                best_position = position
        
        if best_position is not None:
            print(f"\n🎯 최적 초점 위치: {best_position}")
            self.best_focus_position = best_position
            
            # 최적 위치로 설정
            if self.camera_type == "Picamera2":
                try:
                    self.camera.set_controls({"LensPosition": best_position})
                except:
                    pass
            else:
                try:
                    self.camera.set(cv2.CAP_PROP_FOCUS, best_position)
                except:
                    pass
            
            return True
        else:
            print("❌ 최적 초점 위치를 찾을 수 없습니다.")
            return False
    
    def qr_detection_monitor(self, duration=30):
        """QR 코드 감지 모니터링"""
        print(f"QR 코드 감지 모니터링을 {duration}초간 시작합니다...")
        print("QR 코드를 카메라에 보여주세요.")
        
        start_time = time.time()
        detection_count = 0
        successful_detections = []
        
        while time.time() - start_time < duration:
            frame = self.capture_frame()
            if frame is not None:
                qr_results = self.detect_qr_codes(frame)
                
                if qr_results:
                    detection_count += len(qr_results)
                    for result in qr_results:
                        if result['data'] not in [d['data'] for d in successful_detections]:
                            successful_detections.append(result)
                            print(f"🎯 QR 코드 감지: {result['data']} (품질: {result['quality']})")
                
                # 진행 상황 표시
                elapsed = time.time() - start_time
                remaining = duration - elapsed
                print(f"\r진행: {elapsed:.1f}s / {duration}s | 감지: {detection_count}개 | 남은 시간: {remaining:.1f}s", end="")
            
            time.sleep(0.1)
        
        print(f"\n\n📊 모니터링 결과:")
        print(f"총 감지 횟수: {detection_count}")
        print(f"고유 QR 코드: {len(successful_detections)}개")
        
        if successful_detections:
            print("감지된 QR 코드들:")
            for i, detection in enumerate(successful_detections, 1):
                print(f"  {i}. {detection['data']} (품질: {detection['quality']})")
        
        return successful_detections
    
    def optimize_camera_settings(self):
        """카메라 설정 최적화"""
        print("카메라 설정 최적화 중...")
        
        if self.camera_type == "Picamera2":
            try:
                # 고해상도 설정
                self.camera.set_controls({
                    "FrameDurationLimits": (33333, 33333),  # 30fps
                    "NoiseReductionMode": "HighQuality",
                    "Sharpness": 2.0,
                    "Contrast": 1.2
                })
                print("✅ Picamera2 설정 최적화 완료")
            except Exception as e:
                print(f"Picamera2 설정 최적화 실패: {e}")
        else:
            try:
                # OpenCV 설정 최적화
                self.camera.set(cv2.CAP_PROP_BRIGHTNESS, 0.5)
                self.camera.set(cv2.CAP_PROP_CONTRAST, 0.5)
                self.camera.set(cv2.CAP_PROP_SATURATION, 0.5)
                self.camera.set(cv2.CAP_PROP_HUE, 0.5)
                print("✅ OpenCV 설정 최적화 완료")
            except Exception as e:
                print(f"OpenCV 설정 최적화 실패: {e}")
    
    def interactive_qr_test(self):
        """대화형 QR 코드 테스트"""
        print("\n=== 대화형 QR 코드 테스트 ===")
        print("1. 자동 초점 조정")
        print("2. 수동 초점 테스트")
        print("3. QR 코드 감지 모니터링")
        print("4. 카메라 설정 최적화")
        print("5. 종료")
        
        while True:
            try:
                choice = input("\n선택하세요 (1-5): ").strip()
                
                if choice == '1':
                    self.auto_focus_adjustment()
                elif choice == '2':
                    self.manual_focus_test()
                elif choice == '3':
                    duration = input("모니터링 시간(초, 기본값: 30): ").strip()
                    try:
                        duration = int(duration) if duration else 30
                    except:
                        duration = 30
                    self.qr_detection_monitor(duration)
                elif choice == '4':
                    self.optimize_camera_settings()
                elif choice == '5':
                    break
                else:
                    print("잘못된 선택입니다. 1-5 중에서 선택하세요.")
                    
            except KeyboardInterrupt:
                print("\n테스트가 중단되었습니다.")
                break
            except Exception as e:
                print(f"오류 발생: {e}")
    
    def cleanup(self):
        """정리"""
        if self.camera:
            if self.camera_type == "Picamera2":
                self.camera.stop()
                self.camera.close()
            else:
                self.camera.release()
        cv2.destroyAllWindows()

def main():
    """메인 함수"""
    print("=== QR 코드 인식 최적화 도구 ===")
    print("QR 코드 인식 성능을 향상시키고 초점을 자동으로 조정합니다.\n")
    
    optimizer = QROptimizer()
    
    try:
        # 카메라 초기화
        if not optimizer.initialize_camera():
            print("❌ 카메라 초기화에 실패했습니다.")
            return
        
        # 카메라 설정 최적화
        optimizer.optimize_camera_settings()
        
        # 자동 초점 조정
        optimizer.auto_focus_adjustment()
        
        # 대화형 테스트 시작
        optimizer.interactive_qr_test()
        
    except KeyboardInterrupt:
        print("\n프로그램이 중단되었습니다.")
    except Exception as e:
        print(f"오류 발생: {e}")
    finally:
        optimizer.cleanup()
        print("프로그램이 종료되었습니다.")

if __name__ == "__main__":
    main()
