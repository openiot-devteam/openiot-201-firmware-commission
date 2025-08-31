#!/usr/bin/env python3
"""
페어링 요청을 받는 테스트 서버
"""

from flask import Flask, request, jsonify
import json
from datetime import datetime

app = Flask(__name__)

# 페어링된 디바이스 목록
paired_devices = []

@app.route('/')
def index():
    """메인 페이지"""
    return """
    <h1>페어링 테스트 서버</h1>
    <p>이 서버는 디바이스 페어링 요청을 받습니다.</p>
    <h2>페어링된 디바이스:</h2>
    <ul>
    """ + "".join([f"<li>IP: {device['ip']}, MAC: {device['mac_address']}, 시간: {device['timestamp']}</li>" for device in paired_devices]) + """
    </ul>
    """

@app.route('/devices/pairing', methods=['POST'])
def device_pairing():
    """디바이스 페어링 엔드포인트"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '데이터가 없습니다.'}), 400
        
        # 필수 필드 확인
        if 'ip' not in data or 'mac_address' not in data:
            return jsonify({'error': 'IP와 MAC 주소가 필요합니다.'}), 400
        
        # 페어링 정보 저장
        device_info = {
            'ip': data['ip'],
            'mac_address': data['mac_address'],
            'timestamp': datetime.now().isoformat(),
            'user_agent': request.headers.get('User-Agent', 'Unknown')
        }
        
        # 중복 확인 (같은 IP나 MAC이 이미 있는지)
        existing_device = None
        for device in paired_devices:
            if device['ip'] == data['ip'] or device['mac_address'] == data['mac_address']:
                existing_device = device
                break
        
        if existing_device:
            # 기존 디바이스 정보 업데이트
            existing_device.update(device_info)
            print(f"기존 디바이스 정보 업데이트: {existing_device}")
            return jsonify({
                'message': '디바이스 정보가 업데이트되었습니다.',
                'device': existing_device
            }), 200
        else:
            # 새 디바이스 추가
            paired_devices.append(device_info)
            print(f"새 디바이스 페어링: {device_info}")
            return jsonify({
                'message': '디바이스가 성공적으로 페어링되었습니다.',
                'device': device_info
            }), 200
            
    except Exception as e:
        print(f"페어링 요청 처리 오류: {e}")
        return jsonify({'error': f'서버 오류: {str(e)}'}), 500

@app.route('/devices', methods=['GET'])
def get_devices():
    """페어링된 디바이스 목록 조회"""
    return jsonify({
        'devices': paired_devices,
        'count': len(paired_devices)
    })

@app.route('/devices/<mac_address>', methods=['GET'])
def get_device(mac_address):
    """특정 MAC 주소의 디바이스 정보 조회"""
    for device in paired_devices:
        if device['mac_address'] == mac_address:
            return jsonify(device)
    return jsonify({'error': '디바이스를 찾을 수 없습니다.'}), 404

if __name__ == '__main__':
    print("🚀 페어링 테스트 서버를 시작합니다...")
    print("📡 서버 주소: http://localhost:3000")
    print("🔗 페어링 엔드포인트: http://localhost:3000/devices/pairing")
    print("📱 디바이스 목록: http://localhost:3000/devices")
    print("\nQR 코드를 스캔하여 페어링을 테스트하세요!")
    
    app.run(host='0.0.0.0', port=3000, debug=True)
