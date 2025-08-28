# QR 코드 커미션 시스템

QR 코드를 스캔하여 서버 정보를 추출하고 커미션 요청을 보내는 프로그램입니다.

## 기능

1. **QR 코드 스캔**: 이미지 파일에서 QR 코드를 스캔
2. **서버 정보 파싱**: QR 코드에서 서버 IP, 포트, 키 정보 추출
3. **API 요청**: 추출된 정보를 바탕으로 서버에 커미션 요청 전송

## 설치 방법

1. 필요한 패키지 설치:
```bash
pip install -r requirements.txt
```

2. Windows에서 pyzbar 사용을 위해 추가 설치:
```bash
# zbar 라이브러리 설치 (Windows)
# https://github.com/NaturalHistoryMuseum/pyzbar 에서 zbar 다운로드
```

## 사용 방법

### 테스트 서버 실행 (선택사항)
테스트를 위해 로컬 서버를 실행할 수 있습니다:
```bash
python test_server.py
```

### 메인 프로그램 실행
1. QR 코드 이미지 파일을 프로그램과 같은 디렉토리에 넣으세요
2. 프로그램 실행:
```bash
python main.py
```

3. 목록에서 스캔할 이미지 파일을 선택하세요
4. QR 코드가 인식되면 자동으로 서버에 요청이 전송됩니다

### 테스트용 QR 코드 생성
```bash
python generate_test_qr.py
```

## QR 코드 형식

다음 두 가지 형식을 지원합니다:

### JSON 형식
```json
{
  "ip": "192.168.1.100",
  "port": "8080",
  "key": "your_secret_key"
}
```

### 간단한 형식
```
192.168.1.100:8080:your_secret_key
```

## API 요청 형식

프로그램은 다음 형식으로 서버에 POST 요청을 보냅니다:

```
POST http://{server_ip}:{server_port}/commission
Content-Type: application/json

{
  "client_ip": "클라이언트_IP_주소"
}
```

## 테스트 서버 기능

테스트 서버는 다음 엔드포인트를 제공합니다:
- `GET /`: 서버 상태 확인
- `POST /commission`: 커미션 요청 처리
- `GET /requests`: 받은 요청 기록 확인
- `POST /clear`: 요청 기록 초기화

## 주의사항

- 지원하는 이미지 형식: .png, .jpg, .jpeg, .bmp, .tiff
- 인터넷 연결이 필요합니다 (클라이언트 IP 확인용)
- QR 코드에 서버 정보가 올바르게 인코딩되어 있어야 합니다
