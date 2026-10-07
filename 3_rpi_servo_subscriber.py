#!/usr/bin/env python3
"""
3_rpi_servo_subscriber.py  -  Run on Raspberry Pi
==================================================

This relay receives the normalized MQTT command from the EC2 bridge and
publishes a single drawing-length value on ROS 2 topic /drawing_length.

The downstream hardware bridge then sends that plain integer to the Arduino
sketch, which already knows how to map 1-6 into servo motion.
"""

import argparse
import json
import os
import ssl
import threading
import time

import paho.mqtt.client as mqtt

try:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import Float32, String

    _HAS_ROS2 = True
except ImportError:
    _HAS_ROS2 = False

MQTT_HOST = os.environ.get("MQTT_HOST", "13.51.178.9")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TLS = os.environ.get("MQTT_TLS", "false").lower() in {"1", "true", "yes", "on"}
MQTT_CERT_DIR = os.path.expanduser(os.environ.get("MQTT_CERT_DIR", "~/aws_iot_certs"))
MQTT_CA = os.path.join(MQTT_CERT_DIR, "ca.pem")
MQTT_CERT = os.path.join(MQTT_CERT_DIR, "cert.pem")
MQTT_KEY = os.path.join(MQTT_CERT_DIR, "key.pem")

TOPIC_LENGTH_COMMANDS = "forensic_robot/servo_angles"
TOPIC_STATUS = "forensic_robot/status"
CLIENT_ID = "forensic_robot_rpi_sub"

LINE_MIN_CM = 1
LINE_MAX_CM = 6


def clamp_length(value) -> int:
    try:
        length_cm = int(round(float(value)))
    except (TypeError, ValueError):
        length_cm = LINE_MIN_CM
    return max(LINE_MIN_CM, min(LINE_MAX_CM, length_cm))


class DrawingLengthNode(Node):
    def __init__(self):
        super().__init__("drawing_length_subscriber")

        self._length_pub = self.create_publisher(Float32, "/drawing_length", 10)
        self._status_pub = self.create_publisher(String, "/drawing_status", 10)
        self._mqtt = None
        self._mqtt_ok = False

        self._connect_broker()
        self._pub_status("ready", "waiting for line length")

    def _connect_broker(self):
        if MQTT_TLS:
            for path in (MQTT_CA, MQTT_CERT, MQTT_KEY):
                if not os.path.exists(path):
                    self.get_logger().error(f"Missing: {path}")
                    return

        try:
            client = mqtt.Client(client_id=CLIENT_ID, protocol=mqtt.MQTTv311)
            if MQTT_TLS:
                client.tls_set(
                    ca_certs=MQTT_CA,
                    certfile=MQTT_CERT,
                    keyfile=MQTT_KEY,
                    tls_version=ssl.PROTOCOL_TLSv1_2,
                )
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            client.loop_start()
            self._mqtt = client
        except Exception as exc:
            self.get_logger().error(f"MQTT connect error: {exc}")
            threading.Timer(10.0, self._connect_broker).start()

    def _on_connect(self, client, userdata, flags, rc):
        self._mqtt_ok = (rc == 0)
        if rc == 0:
            client.subscribe(TOPIC_LENGTH_COMMANDS, qos=1)
            self.get_logger().info(f"MQTT connected. Sub: {TOPIC_LENGTH_COMMANDS}")
        else:
            self.get_logger().error(f"MQTT connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        self._mqtt_ok = False
        threading.Timer(5.0, self._connect_broker).start()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            primitive = str(payload.get("primitive", "line")).lower()
            if primitive == "home":
                self._pub_status("home", "ignored")
                return

            length_cm = clamp_length(payload.get("length_cm", LINE_MIN_CM))
            self._publish_length(length_cm)
            self._pub_status("forwarded", f"{length_cm} cm")
        except Exception as exc:
            self.get_logger().error(f"Parse error: {exc}")
            self._pub_status("error", str(exc))

    def _publish_length(self, length_cm: int):
        message = Float32()
        message.data = float(length_cm)
        self._length_pub.publish(message)
        self.get_logger().info(f"Published /drawing_length = {length_cm} cm")

    def _pub_status(self, state: str, detail: str = ""):
        message = String()
        message.data = json.dumps(
            {
                "state": state,
                "detail": detail,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        self._status_pub.publish(message)
        if self._mqtt and self._mqtt_ok:
            self._mqtt.publish(TOPIC_STATUS, message.data, qos=0)

    def destroy_node(self):
        if self._mqtt:
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
        super().destroy_node()


class StandaloneSubscriber:
    def start(self):
        print("=" * 60)
        print("  Pi Length Relay  (STANDALONE - no ROS2)")
        print(f"  Broker: {MQTT_HOST}:{MQTT_PORT} ({'TLS' if MQTT_TLS else 'plain TCP'})")
        print(f"  Sub: {TOPIC_LENGTH_COMMANDS}")
        print("=" * 60)

        if MQTT_TLS:
            for path in (MQTT_CA, MQTT_CERT, MQTT_KEY):
                if not os.path.exists(path):
                    raise FileNotFoundError(path)

        client = mqtt.Client(client_id=CLIENT_ID + "_solo", protocol=mqtt.MQTTv311)
        if MQTT_TLS:
            client.tls_set(
                ca_certs=MQTT_CA,
                certfile=MQTT_CERT,
                keyfile=MQTT_KEY,
                tls_version=ssl.PROTOCOL_TLSv1_2,
            )
        client.on_connect = lambda cl, ud, flags, rc: (
            cl.subscribe(TOPIC_LENGTH_COMMANDS, qos=1),
            print(f"Connected. Sub: {TOPIC_LENGTH_COMMANDS}") if rc == 0 else print(f"Connect failed rc={rc}")
        )
        client.on_message = self._on_msg
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        print("Ready for 1-6 cm line commands.")
        try:
            client.loop_forever()
        except KeyboardInterrupt:
            print("Stopped.")

    def _on_msg(self, client, userdata, msg):
        payload = json.loads(msg.payload.decode())
        primitive = str(payload.get("primitive", "line")).lower()
        if primitive == "home":
            print("[MSG] home ignored")
            return
        length_cm = clamp_length(payload.get("length_cm", LINE_MIN_CM))
        print(f"[MSG] length -> {length_cm} cm")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--standalone", action="store_true")
    args = parser.parse_args()

    if args.standalone or not _HAS_ROS2:
        StandaloneSubscriber().start()
    else:
        rclpy.init()
        node = DrawingLengthNode()
        try:
            rclpy.spin(node)
        except KeyboardInterrupt:
            pass
        finally:
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()