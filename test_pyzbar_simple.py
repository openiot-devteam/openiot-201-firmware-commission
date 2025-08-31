#!/usr/bin/env python3
"""
pyzbar 간단 테스트 스크립트
pyzbar가 기본적으로 동작하는지 빠르게 확인합니다.
"""

def test_basic_import():
    """기본 임포트 테스트"""
    print("=== pyzbar 기본 테스트 ===")
    
    try:
        print("1. pyzbar 모듈 임포트 시도...")
        from pyzbar import pyzbar
        print("   ✅ 성공!")
        
        print("2. pyzbar.decode 함수 확인...")
        if hasattr(pyzbar, 'decode'):
            print("   ✅ decode 함수 존재")
        else:
            print("   ❌ decode 함수 없음")
            return False
        
        print("3. 간단한 테스트 이미지 생성...")
        import numpy as np
        test_img = np.zeros((100, 100, 3), dtype=np.uint8)
        print("   ✅ 테스트 이미지 생성 완료")
        
        print("4. pyzbar.decode() 실행...")
        result = pyzbar.decode(test_img)
        print(f"   ✅ decode() 실행 성공 (결과: {len(result)}개)")
        
        print("\n🎉 pyzbar가 정상적으로 동작합니다!")
        return True
        
    except ImportError as e:
        print(f"   ❌ ImportError: {e}")
        print("\n💡 해결 방법:")
        print("   pip3 install pyzbar")
        return False
        
    except Exception as e:
        print(f"   ❌ 오류: {e}")
        print("\n💡 해결 방법:")
        print("   sudo apt-get install libzbar0")
        print("   pip3 install --upgrade pyzbar")
        return False

if __name__ == "__main__":
    test_basic_import()
