import os
import json
import threading
import time

import paho.mqtt.client as mqtt
import ssl

# 기존 구현 재사용
from mqtt_camera import (
	RemoteMQTTClient,
	camera_on,
	camera_stop_event,
	start_recording_manual,
	stop_recording_manual,
	current_frame,
	current_fps,
	current_bitrate,
	ensure_rtsp_server,
)


# 환경 변수 기반 기본 설정 (main.py와 호환)
MQTT_BROKER_HOST = os.getenv('MQTT_BROKER_HOST', '192.168.0.76')
MQTT_BROKER_PORT = int(os.getenv('MQTT_BROKER_PORT', '8883'))
MQTT_USERNAME = os.getenv('MQTT_USERNAME', '')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '')
MQTT_TLS_ENABLED = os.getenv('MQTT_TLS_ENABLED', 'true').lower() in ('1', 'true', 'yes')
MQTT_CA_CERT = os.getenv('MQTT_CA_CERT', '')
MQTT_CLIENT_CERT = os.getenv('MQTT_CLIENT_CERT', '')
MQTT_CLIENT_KEY = os.getenv('MQTT_CLIENT_KEY', '')
MQTT_TLS_INSECURE = os.getenv('MQTT_TLS_INSECURE', 'false').lower() in ('1', 'true', 'yes')

THING_NAME = os.getenv('THING_NAME', 'thing_name')


camera_thread = None


def mqtt_on_connect(client: mqtt.Client, userdata, flags, rc):
	print(f"[MQTT] 연결 결과: rc={rc}")
	if rc == 0:
		print("[MQTT] 연결 성공")
		# 샘플 코드 정책에 맞춰 3개 토픽 구독 (QoS 차등)
		queue_topic = f"{THING_NAME}/queue"
		status_req_topic = f"things/{THING_NAME}/status/req"
		command_req_topic = f"things/{THING_NAME}/command/req"
		client.subscribe(queue_topic, qos=1)
		client.subscribe(status_req_topic, qos=0)
		client.subscribe(command_req_topic, qos=1)
		print(f"[MQTT] 구독: {queue_topic} (QoS=1)")
		print(f"[MQTT] 구독: {status_req_topic} (QoS=0)")
		print(f"[MQTT] 구독: {command_req_topic} (QoS=1)")
	else:
		print("[MQTT] 연결 실패")


def mqtt_on_message(client: mqtt.Client, userdata, msg):
	global camera_thread
	payload_raw = msg.payload.decode('utf-8', 'ignore')
	print(f"[MQTT] 수신 {msg.topic}")
	# JSON 본문만 추출: 첫 '{' 이후만 사용
	if '{' in payload_raw:
		payload = payload_raw[payload_raw.find('{'):]
	else:
		payload = payload_raw
	print(f"메시지 내용: {payload}")

	# 우선 command/req는 JSON 명령으로 처리, 그 외는 문자열 명령 fallback
	try:
		data = json.loads(payload)
	except Exception:
		data = None

	if msg.topic == f"things/{THING_NAME}/command/req" and isinstance(data, dict):
		cmd = str(data.get('command', '')).lower()
		if cmd in ('camera_on', 'start_camera'):
			if camera_thread is None or not camera_thread.is_alive():
				camera_stop_event.clear()
				camera_thread = threading.Thread(target=lambda: camera_on(), daemon=True)
				camera_thread.start()
				print('[CMD] camera_on 시작')
			else:
				print('[CMD] 이미 카메라 스레드 동작 중')
		elif cmd in ('camera_off', 'stop_camera'):
			camera_stop_event.set()
			print('[CMD] camera_off 요청')
		elif cmd in ('start_recording', 'record_on'):
			try:
				from mqtt_camera import camera_frame
				if camera_frame is not None:
					ok, msg_text = start_recording_manual(camera_frame)
					print(f"[CMD] 녹화 시작: {ok} {msg_text}")
				else:
					print('[CMD] 녹화 시작 실패: camera_frame 없음')
			except Exception as e:
				print(f"[CMD] 녹화 시작 예외: {e}")
		elif cmd in ('stop_recording', 'record_off'):
			ok, msg_text = stop_recording_manual()
			print(f"[CMD] 녹화 중지: {ok} {msg_text}")
		elif cmd == 'ensure_rtsp':
			try:
				w, h = [int(v) for v in str(current_frame).split('x')]
			except Exception:
				w, h = 1280, 720
			ensure_rtsp_server(w, h, int(current_fps), int(current_bitrate // 1000))
			print('[CMD] RTSP 서버 확인/시작')
		else:
			print('[CMD] 알 수 없는 명령(JSON)')
		return

	# 문자열 fallback (queue/status 등 단순 테스트용 명령)
	cmd = payload.strip().lower() if isinstance(payload, str) else ''
	if cmd in ('camera_on', 'start_camera'):
		if camera_thread is None or not camera_thread.is_alive():
			camera_stop_event.clear()
			camera_thread = threading.Thread(target=lambda: camera_on(), daemon=True)
			camera_thread.start()
			print('[CMD] camera_on 시작')
		else:
			print('[CMD] 이미 카메라 스레드 동작 중')
	elif cmd in ('camera_off', 'stop_camera'):
		camera_stop_event.set()
		print('[CMD] camera_off 요청')
	elif cmd in ('record_on', 'start_recording'):
		# 최신 프레임은 mqtt_camera.camera_on 루프에서 관리. 즉시 시도
		try:
			from mqtt_camera import camera_frame
			if camera_frame is not None:
				ok, msg_text = start_recording_manual(camera_frame)
				print(f"[CMD] 녹화 시작: {ok} {msg_text}")
			else:
				print('[CMD] 녹화 시작 실패: camera_frame 없음')
		except Exception as e:
			print(f"[CMD] 녹화 시작 예외: {e}")
	elif cmd in ('record_off', 'stop_recording'):
		ok, msg_text = stop_recording_manual()
		print(f"[CMD] 녹화 중지: {ok} {msg_text}")
	elif cmd == 'ensure_rtsp':
		try:
			w, h = [int(v) for v in str(current_frame).split('x')]
		except Exception:
			w, h = 1280, 720
		ensure_rtsp_server(w, h, int(current_fps), int(current_bitrate // 1000))
		print('[CMD] RTSP 서버 확인/시작')
	else:
		print('[CMD] 알 수 없는 명령')


def build_mqtt_client() -> mqtt.Client:
	client_id = f"simple_controller_{os.getenv('COMPUTERNAME', 'host')}"
	client = mqtt.Client(client_id=client_id)
	client.on_connect = mqtt_on_connect
	client.on_message = mqtt_on_message

	if MQTT_USERNAME:
		client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD or None)

	# 샘플 코드: 8883 포트 시 인증서 검증 비활성화 TLS 컨텍스트 사용
	if MQTT_BROKER_PORT == 8883 or MQTT_TLS_ENABLED:
		try:
			ssl_context = ssl.create_default_context()
			ssl_context.check_hostname = False
			ssl_context.verify_mode = ssl.CERT_NONE
			client.tls_set_context(ssl_context)
			client.tls_insecure_set(True)
			print("TLS 설정 완료 (인증서 검증 비활성화)")
		except Exception as e:
			print(f"TLS 설정 실패: {e}")
			print("TLS 없이 연결을 시도합니다.")

	return client


def main():
	print('=== MQTT 카메라 컨트롤러 ===')
	print('명령 토픽으로 camera_on/camera_off, start_recording/stop_recording 전송하세요')

	client = build_mqtt_client()
	client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
	client.loop_start()

	# 초기 RTSP 서버 보장(선택)
	try:
		w, h = [int(v) for v in str(current_frame).split('x')]
	except Exception:
		w, h = 1280, 720
	try:
		ensure_rtsp_server(w, h, int(current_fps), int(current_bitrate // 1000))
	except Exception as e:
		print(f"RTSP ensure 실패: {e}")

	# 유지
	try:
		while True:
			time.sleep(1)
	except KeyboardInterrupt:
		print('종료 요청됨')
	finally:
		client.loop_stop()
		client.disconnect()


if __name__ == '__main__':
	main()


