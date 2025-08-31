#!/usr/bin/env python3
"""
pyzbar 모듈 테스트 스크립트
pyzbar가 정상적으로 동작하는지 확인합니다.
"""

import sys
import os

def test_pyzbar_import():
    """pyzbar 모듈 임포트 테스트"""
    print("=== pyzbar 모듈 임포트 테스트 ===")
    
    try:
        from pyzbar import pyzbar
        print("✅ pyzbar 모듈 임포트 성공!")
        print(f"  모듈 위치: {pyzbar.__file__}")
        return True
    except ImportError as e:
        print(f"❌ pyzbar 모듈 임포트 실패: {e}")
        return False
    except Exception as e:
        print(f"❌ 예상치 못한 오류: {e}")
        return False

def test_pyzbar_version():
    """pyzbar 버전 확인"""
    print("\n=== pyzbar 버전 확인 ===")
    
    try:
        from pyzbar import pyzbar
        version = getattr(pyzbar, '__version__', '알 수 없음')
        print(f"✅ pyzbar 버전: {version}")
        return True
    except Exception as e:
        print(f"❌ 버전 확인 실패: {e}")
        return False

def test_pyzbar_decode():
    """pyzbar 디코딩 기능 테스트"""
    print("\n=== pyzbar 디코딩 기능 테스트 ===")
    
    try:
        from pyzbar import pyzbar
        import numpy as np
        
        # 테스트용 더미 이미지 생성 (QR 코드가 아닌 단순한 이미지)
        print("테스트용 더미 이미지 생성 중...")
        test_image = np.zeros((100, 100, 3), dtype=np.uint8)
        test_image[:, :] = [255, 255, 255]  # 흰색 배경
        
        # 디코딩 시도 (QR 코드가 없으므로 빈 결과가 나와야 함)
        print("pyzbar.decode() 함수 테스트...")
        result = pyzbar.decode(test_image)
        
        print(f"✅ pyzbar.decode() 함수 실행 성공!")
        print(f"  결과 타입: {type(result)}")
        print(f"  결과 길이: {len(result)}")
        print(f"  예상 결과: 빈 리스트 (QR 코드가 없는 이미지)")
        
        if isinstance(result, list):
            print("✅ 결과가 올바른 타입(리스트)입니다.")
            return True
        else:
            print("❌ 결과가 예상과 다른 타입입니다.")
            return False
            
    except Exception as e:
        print(f"❌ 디코딩 테스트 실패: {e}")
        return False

def test_pyzbar_with_real_image():
    """실제 이미지로 pyzbar 테스트"""
    print("\n=== 실제 이미지로 pyzbar 테스트 ===")
    
    try:
        from pyzbar import pyzbar
        import cv2
        
        # 테스트 이미지 파일들 확인
        test_images = [
            'test_frame_device_0.jpg',
            'test_frame_device_1.jpg',
            'test_frame_picamera2.jpg'
        ]
        
        found_images = []
        for img_file in test_images:
            if os.path.exists(img_file):
                found_images.append(img_file)
        
        if not found_images:
            print("⚠️  테스트할 이미지 파일이 없습니다.")
            print("먼저 test_camera_simple.py를 실행하여 이미지를 생성하세요.")
            return False
        
        print(f"발견된 테스트 이미지: {found_images}")
        
        for img_file in found_images:
            print(f"\n--- {img_file} 테스트 ---")
            
            # 이미지 로드
            image = cv2.imread(img_file)
            if image is None:
                print(f"  ❌ 이미지 로드 실패: {img_file}")
                continue
            
            print(f"  이미지 크기: {image.shape}")
            
            # pyzbar로 디코딩 시도
            try:
                result = pyzbar.decode(image)
                print(f"  디코딩 결과: {len(result)}개 QR 코드 발견")
                
                for i, qr in enumerate(result):
                    data = qr.data.decode('utf-8')
                    rect = qr.rect
                    print(f"    QR {i+1}: {data[:50]}... (위치: {rect})")
                
            except Exception as e:
                print(f"  ❌ 디코딩 오류: {e}")
        
        return True
        
    except Exception as e:
        print(f"❌ 실제 이미지 테스트 실패: {e}")
        return False

def test_pyzbar_dependencies():
    """pyzbar 의존성 확인"""
    print("\n=== pyzbar 의존성 확인 ===")
    
    dependencies = [
        ('numpy', 'np'),
        ('cv2', 'cv2'),
        ('PIL', 'PIL'),
        ('Pillow', 'PIL')
    ]
    
    all_ok = True
    
    for package_name, import_name in dependencies:
        try:
            if import_name == 'np':
                import numpy as np
                version = np.__version__
            elif import_name == 'cv2':
                import cv2
                version = cv2.__version__
            elif import_name == 'PIL':
                try:
                    import PIL
                    version = PIL.__version__
                except:
                    import PIL.Image
                    version = PIL.Image.__version__
            
            print(f"✅ {package_name}: {version}")
            
        except ImportError:
            print(f"❌ {package_name}: 설치되지 않음")
            all_ok = False
        except Exception as e:
            print(f"⚠️  {package_name}: 확인 불가 ({e})")
    
    return all_ok

def test_system_libraries():
    """시스템 라이브러리 확인"""
    print("\n=== 시스템 라이브러리 확인 ===")
    
    try:
        import subprocess
        
        # zbar 라이브러리 확인
        result = subprocess.run(['ldconfig', '-p'], capture_output=True, text=True)
        if 'libzbar' in result.stdout:
            print("✅ libzbar 시스템 라이브러리 발견")
        else:
            print("❌ libzbar 시스템 라이브러리 없음")
            print("  sudo apt-get install libzbar0 실행 필요")
        
        # Python 경로 확인
        print(f"\nPython 경로:")
        for path in sys.path:
            if 'site-packages' in path:
                print(f"  {path}")
        
        return True
        
    except Exception as e:
        print(f"❌ 시스템 라이브러리 확인 실패: {e}")
        return False

def main():
    """메인 함수"""
    print("=== pyzbar 모듈 종합 테스트 ===")
    print("pyzbar가 정상적으로 동작하는지 확인합니다.\n")
    
    test_results = []
    
    # 1. 모듈 임포트 테스트
    test_results.append(("모듈 임포트", test_pyzbar_import()))
    
    # 2. 버전 확인
    if test_results[0][1]:  # 임포트가 성공한 경우에만
        test_results.append(("버전 확인", test_pyzbar_version()))
        
        # 3. 디코딩 기능 테스트
        test_results.append(("디코딩 기능", test_pyzbar_decode()))
        
        # 4. 실제 이미지 테스트
        test_results.append(("실제 이미지", test_pyzbar_with_real_image()))
    
    # 5. 의존성 확인
    test_results.append(("의존성 확인", test_pyzbar_dependencies()))
    
    # 6. 시스템 라이브러리 확인
    test_results.append(("시스템 라이브러리", test_system_libraries()))
    
    # 결과 요약
    print("\n" + "="*50)
    print("=== 테스트 결과 요약 ===")
    
    passed = 0
    total = len(test_results)
    
    for test_name, result in test_results:
        status = "✅ 통과" if result else "❌ 실패"
        print(f"{test_name}: {status}")
        if result:
            passed += 1
    
    print(f"\n총 {total}개 테스트 중 {passed}개 통과")
    
    if passed == total:
        print("\n🎉 pyzbar가 정상적으로 동작합니다!")
        print("웹 카메라 서버를 실행할 수 있습니다.")
    else:
        print(f"\n⚠️  {total - passed}개 테스트가 실패했습니다.")
        print("문제를 해결한 후 다시 테스트해주세요.")
        
        # 문제 해결 방법 제안
        if not test_results[0][1]:  # 모듈 임포트 실패
            print("\n💡 해결 방법:")
            print("1. pip3 install pyzbar")
            print("2. sudo apt-get install libzbar0")
            print("3. pip3 install --upgrade pyzbar")

if __name__ == "__main__":
    main()
