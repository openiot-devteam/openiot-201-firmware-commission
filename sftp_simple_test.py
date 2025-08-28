import paramiko
import os

# ===== 서버 접속 정보 =====
SFTP_HOST = "210.117.163.181"   # Public IP
SFTP_PORT = 4414                # ssh port
SFTP_USER = "msdl"              # username
SFTP_PASS = "Msdl2020!@@"       # password

def test_sftp_connection():
    """SFTP 연결 테스트"""
    try:
        # SSH 클라이언트 초기화
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # 서버 연결
        print(f"[INFO] SFTP 서버에 연결 중... {SFTP_HOST}:{SFTP_PORT}")
        ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS)
        print("[SUCCESS] SFTP 서버 연결 성공")

        # sFTP 세션 열기
        sftp = ssh.open_sftp()
        print("[INFO] SFTP 세션 열기 성공")

        # 현재 디렉토리 확인
        current_dir = sftp.getcwd()
        print(f"[INFO] 현재 원격 디렉토리: {current_dir}")

        # 디렉토리 목록 확인
        try:
            files = sftp.listdir('.')
            print(f"[INFO] 현재 디렉토리 파일 목록: {files[:10]}...")  # 처음 10개만 표시
        except Exception as e:
            print(f"[WARNING] 디렉토리 목록 확인 실패: {e}")

        # 간단한 파일 업로드 테스트 (작은 텍스트 파일)
        test_content = "This is a test file for SFTP upload."
        test_file = "test_upload.txt"
        
        # 로컬에 테스트 파일 생성
        with open(test_file, 'w') as f:
            f.write(test_content)
        
        print(f"[INFO] 테스트 파일 생성: {test_file}")
        
        # 파일 업로드 시도
        try:
            sftp.put(test_file, f"test_upload_{os.getpid()}.txt")
            print("[SUCCESS] 테스트 파일 업로드 성공")
        except Exception as e:
            print(f"[ERROR] 테스트 파일 업로드 실패: {e}")

        # 세션 종료
        sftp.close()
        ssh.close()
        print("[INFO] SFTP 세션 종료")

        # 로컬 테스트 파일 삭제
        if os.path.exists(test_file):
            os.remove(test_file)
            print(f"[INFO] 로컬 테스트 파일 삭제: {test_file}")

    except Exception as e:
        print(f"[ERROR] SFTP 연결 테스트 실패: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_sftp_connection()


