"""
2_aws_model_bridge.py  -  Run on AWS EC2 Instance
=================================================

This bridge receives the GUI command, validates the requested line length,
and republishes a compact payload that the Pi relay can forward to the Arduino.

The tested Arduino sketch expects a plain integer from 1 to 6, so this file
keeps the command shape simple and bounded.
"""

import json
import os
import ssl
import time

import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TLS = os.environ.get("MQTT_TLS", "false").lower() in {"1", "true", "yes", "on"}
MQTT_CERT_DIR = os.path.expanduser(os.environ.get("MQTT_CERT_DIR", "~/aws_iot_certs"))

TOPIC_COMMANDS = "forensic_robot/commands"
TOPIC_SERVO_ANGLES = "forensic_robot/servo_angles"
TOPIC_STATUS = "forensic_robot/status"

CLIENT_ID = "forensic_robot_ec2_model"
LINE_MIN_CM = 1
LINE_MAX_CM = 6

MQTT_CA = os.path.join(MQTT_CERT_DIR, "ca.pem")
MQTT_CERT = os.path.join(MQTT_CERT_DIR, "cert.pem")
MQTT_KEY = os.path.join(MQTT_CERT_DIR, "key.pem")

_mqtt_client = None
_connected = False


def clamp_length(value) -> int:
    try:
        length_cm = int(round(float(value)))
    except (TypeError, ValueError):
        length_cm = LINE_MIN_CM
    return max(LINE_MIN_CM, min(LINE_MAX_CM, length_cm))


def normalize_command(command: dict) -> dict:
    primitive = str(command.get("primitive", "line")).lower()
    if primitive == "home":
        return {"primitive": "home"}

    if primitive != "line":
        return {"primitive": "home"}

    return {
        "primitive": "line",
        "length_cm": clamp_length(command.get("length_cm", LINE_MIN_CM)),
    }


def pub_status(state: str, detail: str = ""):
    if _mqtt_client and _connected:
        payload = json.dumps(
            {
                "state": state,
                "detail": detail,
                "source": "ec2_model",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        _mqtt_client.publish(TOPIC_STATUS, payload, qos=0)


def on_connect(client, userdata, flags, rc):
    global _connected
    if rc == 0:
        _connected = True
        client.subscribe(TOPIC_COMMANDS, qos=1)
        print(f"[MQTT] Connected. Subscribed to: {TOPIC_COMMANDS}")
        pub_status("connected", "ready")
    else:
        print(f"[MQTT] Connect failed rc={rc}")


def on_disconnect(client, userdata, rc):
    global _connected
    _connected = False
    print(f"[MQTT] Disconnected rc={rc}. Reconnecting in 5 s…")
    time.sleep(5)
    try:
        client.reconnect()
    except Exception as exc:
        print(f"[MQTT] Reconnect error: {exc}")


def on_message(client, userdata, msg):
    raw = msg.payload.decode()
    print(f"\n[MSG] Received on {msg.topic}:\n      {raw[:200]}")

    try:
        command = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[ERR] JSON parse: {exc}")
        pub_status("error", f"bad JSON: {exc}")
        return

    normalized = normalize_command(command)
    print(f"[MODEL] Normalized command: {normalized}")

    payload = {
        **normalized,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    result = client.publish(TOPIC_SERVO_ANGLES, json.dumps(payload), qos=1)
    if result.rc == 0:
        if normalized.get("primitive") == "line":
            print(f"[MQTT] Published length {payload['length_cm']} cm → {TOPIC_SERVO_ANGLES}")
            pub_status("published", f"line {payload['length_cm']} cm")
        else:
            print(f"[MQTT] Published home → {TOPIC_SERVO_ANGLES}")
            pub_status("published", "home")
    else:
        print(f"[MQTT] Publish failed rc={result.rc}")
        pub_status("error", f"publish failed rc={result.rc}")


def build_client() -> mqtt.Client:
    client = mqtt.Client(client_id=CLIENT_ID, protocol=mqtt.MQTTv311)
    if MQTT_TLS:
        client.tls_set(
            ca_certs=MQTT_CA,
            certfile=MQTT_CERT,
            keyfile=MQTT_KEY,
            tls_version=ssl.PROTOCOL_TLSv1_2,
        )
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def main():
    global _mqtt_client

    print("=" * 64)
    print("  Forensic Robot  -  AWS EC2 Model Bridge")
    print(f"  Broker   : {MQTT_HOST}:{MQTT_PORT} ({'TLS' if MQTT_TLS else 'plain TCP'})")
    print(f"  Sub      : {TOPIC_COMMANDS}")
    print(f"  Pub      : {TOPIC_SERVO_ANGLES}")
    print(f"  Input    : {LINE_MIN_CM}-{LINE_MAX_CM} cm")
    print("=" * 64)

    if MQTT_TLS:
        for path in (MQTT_CA, MQTT_CERT, MQTT_KEY):
            if not os.path.exists(path):
                print(f"\nERROR: Certificate missing: {path}")
                print(f"       Copy your certs to: {MQTT_CERT_DIR}/")
                print("       Files needed: ca.pem  cert.pem  key.pem")
                return

    _mqtt_client = build_client()

    try:
        print(f"\n[MQTT] Connecting to {MQTT_HOST}:{MQTT_PORT} …")
        _mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        print("[MQTT] loop_forever() started — waiting for commands…\n")
        _mqtt_client.loop_forever()
    except KeyboardInterrupt:
        print("\n[EXIT] Stopped by user.")
    finally:
        if _mqtt_client is not None:
            _mqtt_client.disconnect()


if __name__ == "__main__":
    main()