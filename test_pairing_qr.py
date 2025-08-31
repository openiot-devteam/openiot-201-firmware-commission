#!/usr/bin/env python3
"""
페어링 테스트용 QR 코드 생성 스크립트
"""

import qrcode
import json
from datetime import datetime

def create_pairing_qr():
    """페어링용 QR 코드 데이터 생성"""
    
    # 페어링 데이터 생성
    pairing_data = {
        "endpoint": "http://localhost:3000/devices/pairing",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    
    print("생성된 페어링 데이터:")
    print(json.dumps(pairing_data, indent=2, ensure_ascii=False))
    
    # QR 코드 생성
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    
    qr.add_data(json.dumps(pairing_data))
    qr.make(fit=True)
    
    # QR 코드 이미지 생성
    img = qr.make_image(fill_color="black", back_color="white")
    
    # 이미지 저장
    filename = "pairing_qr_code.png"
    img.save(filename)
    print(f"\n✅ QR 코드가 '{filename}' 파일로 저장되었습니다.")
    print("이 QR 코드를 카메라로 스캔하여 페어링 기능을 테스트할 수 있습니다.")
    
    return pairing_data

if __name__ == "__main__":
    create_pairing_qr()
