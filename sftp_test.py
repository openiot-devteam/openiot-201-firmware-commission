import paramiko
import os
import time
from datetime import datetime

# ===== 서버 접속 정보 =====
SFTP_HOST = "210.117.163.181"   # Public IP
SFTP_PORT = 4414                # ssh port
SFTP_USER = "msdl"              # username
SFTP_PASS = "Msdl2020!@@"       # password

# ===== 로컬 및 원격 경로 =====
# 전송할 로컬 영상 파일 경로 (현재 프로젝트의 test.mp4 사용)
local_file = os.path.abspath("test.mp4")

# 원격 서버의 저장 경로 (NOVA_FARM 구조에 맞게 설정)
remote_dir = "NOVA_FARM/GI01/GI01A11"
remote_file = os.path.basename(local_file)

# ===== sFTP 다운로드 함수 =====
def download_file():
    start_time = time.time()
    try:
        # SSH 클라이언트 초기화
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # 서버 연결
        ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS)
        print("[INFO] SFTP 서버 연결 성공")

        # sFTP 세션 열기
        sftp = ssh.open_sftp()

        # 원격 파일 경로
        remote_path = f"{remote_dir}/{remote_file}"
        
        # 다운로드할 로컬 파일명 (타임스탬프 추가)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        download_filename = f"downloaded_test_{timestamp}.mp4"
        local_download_path = os.path.abspath(download_filename)

        # 파일 다운로드
        sftp.get(remote_path, local_download_path)
        
        # 실행 시간 계산
        end_time = time.time()
        execution_time = end_time - start_time
        
        print(f"[SUCCESS] 파일 다운로드 완료 → {local_download_path}")
        print(f"[INFO] 다운로드 소요 시간: {execution_time:.2f}초")

        # 세션 종료
        sftp.close()
        ssh.close()

    except Exception as e:
        end_time = time.time()
        execution_time = end_time - start_time
        print(f"[ERROR] 파일 다운로드 실패: {e}")
        print(f"[INFO] 실패까지 소요 시간: {execution_time:.2f}초")

# ===== sFTP 업로드 함수 =====
def upload_file():
    start_time = time.time()
    try:
        # SSH 클라이언트 초기화
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # 서버 연결
        ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS)
        print("[INFO] SFTP 서버 연결 성공")

        # sFTP 세션 열기
        sftp = ssh.open_sftp()

        # 원격 경로 확인 및 생성
        try:
            sftp.chdir(remote_dir)
        except IOError:
            # 디렉토리가 없으면 생성
            path_parts = remote_dir.split("/")
            current_path = ""
            for part in path_parts:
                # current_path = f"{current_path}/{part}" if current_path else part

                try:
                    # sftp.chdir(current_path)
                    # NOVA 추가
                    sftp.chdir(part)
                except IOError:
                    # sftp.mkdir(current_path)
                    # sftp.chdir(current_path)

                    # NOVA 추가
                    sftp.mkdir(part)
                    sftp.chdir(part)

        # 파일 업로드
        remote_path = f"{remote_dir}/{remote_file}"

        # NOVA 추가
        sftp.chdir("/home/msdl/")

        sftp.put(local_file, remote_path)
        
        # 실행 시간 계산
        end_time = time.time()
        execution_time = end_time - start_time
        
        print(f"[SUCCESS] 파일 업로드 완료 → {remote_path}")
        print(f"[INFO] 업로드 소요 시간: {execution_time:.2f}초")

        # 세션 종료
        sftp.close()
        ssh.close()

    except Exception as e:
        end_time = time.time()
        execution_time = end_time - start_time
        print(f"[ERROR] 파일 업로드 실패: {e}")
        print(f"[INFO] 실패까지 소요 시간: {execution_time:.2f}초")

# 실행
if __name__ == "__main__":
    total_start_time = time.time()
    
    print("=== SFTP 파일 다운로드 ===")
    download_file()
    
    print("\n=== SFTP 파일 업로드 ===")
    upload_file()
    
    # 전체 실행 시간 계산
    total_end_time = time.time()
    total_execution_time = total_end_time - total_start_time
    print(f"\n[INFO] 전체 작업 소요 시간: {total_execution_time:.2f}초")