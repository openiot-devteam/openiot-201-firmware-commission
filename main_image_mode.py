#!/usr/bin/env python3
"""
이미지 파일을 사용한 QR코드 스캔 (카메라 대안)
"""

import cv2
import requests
import json
import socket
import re
import numpy as np
from pyzbar import pyzbar
import time
import os
import glob

def get_client_ip():
    """클라이언트 IP 주소를 가져오는 함수"""
    try:
        # 외부 서버에 연결하여 공인 IP 확인
        response = requests.get('https://api.ipify.org', timeout=5)
        return response.text
    except:
        try:
            # 로컬 IP 확인
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

def parse_server_info(qr_data):
    """QR 코드 데이터에서 서버 정보를 파싱하는 함수"""
    try:
        # JSON 형태로 파싱 시도
        server_info = json.loads(qr_data)
        return server_info
    except json.JSONDecodeError:
        # JSON이 아닌 경우 다른 형식으로 파싱 시도
        # 예: "ip:port:key" 형식
        parts = qr_data.split(':')
        if len(parts) >= 3:
            return {
                "ip": parts[0],
                "port": parts[1],
                "key": parts[2]
            }
        else:
            print(f"지원하지 않는 QR 코드 형식입니다: {qr_data}")
            return None

def send_commission_request(server_info):
    """커미션 요청을 서버에 보내는 함수"""
    try:
        # 클라이언트 IP 가져오기
        client_ip = get_client_ip()
        print(f"클라이언트 IP: {client_ip}")
        
        # 서버 URL 구성
        server_url = f"http://{server_info['ip']}:{server_info['port']}/commission"
        
        # 요청 데이터 준비
        request_data = {
            "client_ip": client_ip
        }
        
        # API 요청 보내기
        print(f"서버에 요청 보내는 중: {server_url}")
        response = requests.post(
            server_url,
            json=request_data,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        
        print(f"응답 상태 코드: {response.status_code}")
        print(f"응답 내용: {response.text}")
        
        if response.status_code == 200:
            print("커미션 요청이 성공적으로 전송되었습니다.")
            return True
        else:
            print(f"커미션 요청 실패: {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"네트워크 오류: {e}")
        return False
    except Exception as e:
        print(f"오류 발생: {e}")
        return False

def scan_qr_from_images():
    """이미지 파일에서 QR 코드 스캔"""
    print("=== 이미지 파일 QR 코드 스캔 모드 ===")
    
    # 지원하는 이미지 확장자
    image_extensions = ['*.png', '*.jpg', '*.jpeg', '*.bmp', '*.tiff']
    image_files = []
    
    # 현재 디렉토리에서 이미지 파일 찾기
    for ext in image_extensions:
        image_files.extend(glob.glob(ext))
        image_files.extend(glob.glob(ext.upper()))
    
    if not image_files:
        print("현재 디렉토리에 이미지 파일이 없습니다.")
        print("지원하는 형식: .png, .jpg, .jpeg, .bmp, .tiff")
        return None
    
    print(f"발견된 이미지 파일들 ({len(image_files)}개):")
    for i, file in enumerate(image_files, 1):
        print(f"{i}. {file}")
    
    # 사용자가 이미지 선택
    while True:
        try:
            choice = input(f"\n스캔할 이미지 번호를 선택하세요 (1-{len(image_files)}): ")
            choice_idx = int(choice) - 1
            
            if 0 <= choice_idx < len(image_files):
                selected_file = image_files[choice_idx]
                break
            else:
                print("올바른 번호를 입력하세요.")
        except ValueError:
            print("숫자를 입력하세요.")
    
    print(f"\n선택된 이미지: {selected_file}")
    
    # 이미지 로드
    image = cv2.imread(selected_file)
    if image is None:
        print(f"이미지를 로드할 수 없습니다: {selected_file}")
        return None
    
    # QR 코드 디코딩
    decoded_objects = pyzbar.decode(image)
    
    if not decoded_objects:
        print("이미지에서 QR 코드를 찾을 수 없습니다.")
        return None
    
    # 첫 번째 QR 코드 반환
    qr_data = decoded_objects[0].data.decode('utf-8')
    print(f"QR 코드 감지됨: {qr_data}")
    
    return qr_data

def main():
    """메인 함수"""
    print("=== QR 코드 커미션 시스템 (이미지 모드) ===")
    print("QR 코드 형식 예시: {\"ip\":\"192.168.0.164\",\"port\":8080}")
    
    # 1. QR 코드 스캔
    print("\n1. QR 코드를 스캔합니다...")
    qr_data = scan_qr_from_images()
    
    if not qr_data:
        print("QR 코드를 스캔할 수 없습니다.")
        return
    
    # 2. 서버 정보 파싱
    print("\n2. 서버 정보를 파싱합니다...")
    server_info = parse_server_info(qr_data)
    
    if not server_info:
        print("서버 정보를 파싱할 수 없습니다.")
        return
    
    print(f"서버 정보: {server_info}")
    
    # 3. API 요청 보내기
    print("\n3. 커미션 요청을 보냅니다...")
    success = send_commission_request(server_info)
    
    if success:
        print("\n=== 작업 완료 ===")
    else:
        print("\n=== 작업 실패 ===")

if __name__ == "__main__":
    main()
