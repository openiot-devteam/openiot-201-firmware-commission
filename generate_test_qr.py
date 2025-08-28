import qrcode
import json

def create_test_qr_codes():
    """테스트용 QR 코드 이미지들을 생성합니다."""
    
    # 테스트 데이터들
    test_data = [
        {
            "name": "test_server1",
            "data": {
                "ip": "localhost",
                "port": "8080",
                "key": "test_key_123"
            }
        },
        {
            "name": "test_server2", 
            "data": "localhost:8080:another_key"
        },
        {
            "name": "test_server3",
            "data": {
                "ip": "127.0.0.1",
                "port": "8080",
                "key": "production_key_456"
            }
        }
    ]
    
    for test in test_data:
        # QR 코드 생성
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        
        # 데이터 추가
        if isinstance(test["data"], dict):
            qr_data = json.dumps(test["data"])
        else:
            qr_data = test["data"]
        
        qr.add_data(qr_data)
        qr.make(fit=True)
        
        # 이미지 생성
        img = qr.make_image(fill_color="black", back_color="white")
        
        # 파일로 저장
        filename = f"{test['name']}.png"
        img.save(filename)
        print(f"생성됨: {filename} - 데이터: {qr_data}")

if __name__ == "__main__":
    print("테스트용 QR 코드 이미지들을 생성합니다...")
    create_test_qr_codes()
    print("\n완료! 이제 main.py를 실행하여 QR 코드를 테스트할 수 있습니다.")
