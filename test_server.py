from flask import Flask, request, jsonify
import datetime
import json

app = Flask(__name__)

# 커미션 요청을 저장할 리스트
commission_requests = []

@app.route('/commission', methods=['POST'])
def commission():
    """커미션 요청을 처리하는 엔드포인트"""
    try:
        # 요청 데이터 받기
        data = request.get_json()
        
        if not data or 'client_ip' not in data:
            return jsonify({
                'success': False,
                'error': 'client_ip가 필요합니다.'
            }), 400
        
        client_ip = data['client_ip']
        
        # 요청 정보 저장
        request_info = {
            'client_ip': client_ip,
            'timestamp': datetime.datetime.now().isoformat(),
            'request_headers': dict(request.headers),
            'remote_addr': request.remote_addr
        }
        
        commission_requests.append(request_info)
        
        print(f"커미션 요청 받음: {client_ip} (시간: {request_info['timestamp']})")
        
        # 성공 응답
        return jsonify({
            'success': True,
            'message': '커미션 요청이 성공적으로 처리되었습니다.',
            'client_ip': client_ip,
            'timestamp': request_info['timestamp']
        }), 200
        
    except Exception as e:
        print(f"오류 발생: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/requests', methods=['GET'])
def get_requests():
    """받은 모든 커미션 요청을 확인하는 엔드포인트"""
    return jsonify({
        'total_requests': len(commission_requests),
        'requests': commission_requests
    }), 200

@app.route('/clear', methods=['POST'])
def clear_requests():
    """요청 기록을 초기화하는 엔드포인트"""
    global commission_requests
    commission_requests = []
    return jsonify({
        'success': True,
        'message': '모든 요청 기록이 삭제되었습니다.'
    }), 200

@app.route('/', methods=['GET'])
def home():
    """홈페이지 - 서버 상태 확인"""
    return jsonify({
        'server': 'QR 커미션 테스트 서버',
        'status': 'running',
        'endpoints': {
            'POST /commission': '커미션 요청 처리',
            'GET /requests': '요청 기록 확인',
            'POST /clear': '요청 기록 초기화',
            'GET /': '서버 상태 확인'
        },
        'total_requests': len(commission_requests)
    }), 200

if __name__ == '__main__':
    print("=== QR 커미션 테스트 서버 시작 ===")
    print("서버 주소: http://localhost:8080")
    print("커미션 엔드포인트: http://localhost:8080/commission")
    print("요청 확인: http://localhost:8080/requests")
    print("서버 중단: Ctrl+C")
    print("=" * 40)
    
    app.run(host='0.0.0.0', port=8080, debug=True)


