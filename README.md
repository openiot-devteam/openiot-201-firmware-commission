# 라즈베리 카메라3 QR코드 커미션 시스템

라즈베리 카메라3를 사용하여 QR코드를 실시간으로 인식하고, QR코드에 담긴 서버 정보를 통해 API를 호출하는 시스템입니다.

## 기능

- 실시간 QR코드 인식 (Picamera2 사용)
- JSON 형태의 서버 정보 파싱 (예: `{"ip":"192.168.0.164","port":8080}`)
- **새로운 기능: 디바이스 페어링 지원**
  - QR코드에서 endpoint 정보 인식
  - 자동 페어링 요청 전송 (IP + MAC 주소)
  - 페어링 상태 추적
- 자동 API 호출 (커미션 요청)
- 중복 인식 방지 (3초 쿨다운)
- 시각적 피드백 (QR코드 영역 표시)
- Picamera2 실패 시 OpenCV 대안 제공

## 요구사항

### 하드웨어
- 라즈베리파이 (3B+ 이상 권장)
- 라즈베리 카메라3

### 소프트웨어
```bash
pip install -r requirements.txt
```

## 설치 및 설정

### 1. 라즈베리파이 카메라 활성화

```bash
# 카메라 활성화
sudo raspi-config
# Interface Options -> Camera -> Enable

# 또는 명령어로 활성화
sudo raspi-config nonint do_camera 0

# 시스템 재부팅
sudo reboot
```

### 2. Picamera2 설치

```bash
# 시스템 패키지 업데이트
sudo apt-get update

# Picamera2 설치
sudo apt-get install -y python3-picamera2

# 추가 의존성 설치
sudo apt-get install -y python3-opencv python3-pip libzbar0
```

### 3. 카메라 테스트

```bash
# Picamera2 테스트
python3 picamera2_test.py

# 또는 기존 카메라 설정 확인
python3 camera_setup.py

# 또는 간단한 테스트
vcgencmd get_camera
```

### 4. Python 패키지 설치

```bash
# Python 패키지 설치
pip3 install -r requirements.txt
```

## 사용법

### 기본 실행

```bash
python3 main.py
```

### QR코드 형식

#### 기존 커미션 형식
QR코드에는 다음과 같은 JSON 형태의 서버 정보가 포함되어야 합니다:

```json
{
  "ip": "192.168.0.164",
  "port": 8080
}
```

#### 새로운 페어링 형식
디바이스 페어링을 위한 QR코드 형식:

```json
{
  "endpoint": "http://localhost:3000/devices/pairing",
  "timestamp": "2025-08-29T01:34:18.736Z"
}
```

### 동작 과정

1. **카메라 초기화**: Picamera2를 사용하여 라즈베리 카메라3를 초기화합니다.
2. **실시간 스캔**: 카메라 화면에서 QR코드를 실시간으로 감지합니다.
3. **QR코드 인식**: QR코드가 감지되면 JSON 데이터를 파싱합니다.
4. **자동 처리**:
   - **페어링 모드**: endpoint가 있으면 페어링 요청 전송
   - **커미션 모드**: ip/port가 있으면 커미션 API 호출
5. **결과 표시**: 처리 결과를 콘솔에 출력합니다.

## 페어링 기능 테스트

### 1. 테스트 서버 실행

```bash
python3 test_pairing_server.py
```

테스트 서버는 `http://localhost:3000`에서 실행됩니다.

### 2. 페어링 QR코드 생성

```bash
python3 test_pairing_qr.py
```

이 스크립트는 `pairing_qr_code.png` 파일을 생성합니다.

### 3. 페어링 테스트

1. 메인 애플리케이션 실행: `python3 main.py`
2. 생성된 QR코드를 카메라로 스캔
3. 자동으로 페어링 요청이 전송됨
4. 테스트 서버에서 페어링 결과 확인

## 페어링 요청 형식

QR코드 인식 시 자동으로 다음 형식의 POST 요청이 전송됩니다:

```json
{
  "ip": "192.168.1.100",
  "mac_address": "aa:bb:cc:dd:ee:ff"
}
```

- `ip`: 현재 기기의 IP 주소
- `mac_address`: 현재 기기의 MAC 주소

## 시스템 정보

### 지원 플랫폼
- Linux (Raspberry Pi OS, Ubuntu 등)
- Windows (MAC 주소 가져오기 지원)

### MAC 주소 획득 방법
1. **Linux**: `ip link show` 명령어 사용
2. **Windows**: `getmac` 명령어 사용
3. **대안**: UUID 기반 MAC 주소 생성

## 제어

- **'q' 키**: 프로그램 종료
- **Ctrl+C**: 프로그램 강제 종료

## 문제 해결

### 카메라가 인식되지 않는 경우

1. 카메라 연결 확인
2. 카메라 활성화 상태 확인
3. 시스템 재부팅
4. `picamera2_test.py` 실행하여 진단

### QR코드 인식이 안 되는 경우

1. QR코드가 명확하게 보이는지 확인
2. 조명 상태 확인
3. 카메라와 QR코드 사이 거리 조정
4. QR코드 크기 확인

### API 호출 실패

1. 네트워크 연결 확인
2. 서버 IP/포트 확인
3. 서버가 실행 중인지 확인
4. 방화벽 설정 확인

### Picamera2 오류 시

Picamera2가 작동하지 않으면 자동으로 OpenCV로 전환됩니다. 만약 여전히 문제가 있다면:

```bash
# Picamera2 재설치
sudo apt-get remove python3-picamera2
sudo apt-get install python3-picamera2

# 또는 OpenCV만 사용하는 버전
python3 simple_camera_test.py
```

## 파일 구조

```
├── main.py              # 메인 QR코드 스캔 프로그램 (Picamera2 우선)
├── picamera2_test.py    # Picamera2 테스트 도구
├── simple_camera_test.py # OpenCV 카메라 테스트 도구
├── camera_setup.py      # 카메라 설정 및 테스트 도구
├── camera_debug.py      # 상세한 카메라 디버깅 도구
├── main_image_mode.py   # 이미지 파일 모드 (카메라 대안)
├── requirements.txt     # Python 의존성
└── README.md           # 이 파일
```

## 개발자 정보

이 시스템은 라즈베리파이 환경에서 QR코드를 통한 자동 커미션을 위해 개발되었습니다. Picamera2를 우선적으로 사용하여 더 안정적인 카메라 접근을 제공합니다.

## 라이선스

MIT License
