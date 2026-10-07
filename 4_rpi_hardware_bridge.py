#!/usr/bin/env python3
"""
4_rpi_hardware_bridge.py  -  Run on Raspberry Pi
=================================================

This bridge listens for the single drawing-length value produced by the Pi
relay and sends that plain integer to the tested Arduino sketch.

The Arduino code uses Serial.parseInt() at 9600 baud, so this bridge writes
only the number 1-6 followed by a newline.
"""

import argparse
import json
import os
import threading
import time

try:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import Float32, String

    _HAS_ROS2 = True
except ImportError:
    _HAS_ROS2 = False

try:
    import serial as _serial

    _HAS_SERIAL = True
except ImportError:
    _HAS_SERIAL = False
    print("[WARN] pip3 install pyserial")

SERIAL_PORT = os.environ.get("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD = 9600
SERIAL_TIMEOUT = 0.5
LINE_MIN_CM = 1
LINE_MAX_CM = 6


def clamp_length(value) -> int:
    try:
        length_cm = int(round(float(value)))
    except (TypeError, ValueError):
        length_cm = LINE_MIN_CM
    return max(LINE_MIN_CM, min(LINE_MAX_CM, length_cm))


class ArduinoSerial:
    def __init__(self, port: str, baud: int = SERIAL_BAUD):
        if not _HAS_SERIAL:
            raise RuntimeError("pip3 install pyserial")
        self._lock = threading.Lock()
        print(f"[SERIAL] Opening {port} @ {baud}...")
        self._ser = _serial.Serial(port, baud, timeout=SERIAL_TIMEOUT, write_timeout=1.0)
        time.sleep(2.0)
        self._ser.reset_input_buffer()

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def send_length(self, length_cm: int) -> list[str]:
        if not self.connected:
            return []

        length_cm = clamp_length(length_cm)
        command = f"{length_cm}\n".encode()

        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(command)
            self._ser.flush()

            deadline = time.time() + 1.5
            lines: list[str] = []
            while time.time() < deadline:
                line = self._ser.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    lines.append(line)
                    deadline = time.time() + 0.2
                elif lines:
                    break
            return lines

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()


class HardwareBridgeNode(Node):
    def __init__(self, port: str = SERIAL_PORT):
        super().__init__("hardware_bridge_node")

        self._arduino = None
        self._sim = False

        if not self._sim:
            try:
                self._arduino = ArduinoSerial(port, SERIAL_BAUD)
                self.get_logger().info(f"Arduino serial ready on {port}")
            except Exception as exc:
                self.get_logger().error(f"Serial error: {exc}")
                self._sim = True

        mode = f"SERIAL ({port})" if not self._sim else "SIM"
        self.get_logger().info(
            f"Hardware bridge ready.\n"
            f"  Mode   : {mode}\n"
            f"  Input  : /drawing_length\n"
            f"  Arduino: plain integer 1-6 @ 9600 baud"
        )

        self._sub = self.create_subscription(
            Float32,
            "/drawing_length",
            self._on_length,
            10,
        )
        self._hw_pub = self.create_publisher(String, "/hw_status", 10)

    def _pub_status(self, state: str, detail: str = ""):
        message = String()
        message.data = json.dumps(
            {
                "state": state,
                "detail": detail,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        self._hw_pub.publish(message)

    def _on_length(self, msg: Float32):
        length_cm = clamp_length(msg.data)
        self._send_length(length_cm)

    def _send_length(self, length_cm: int):
        if self._sim:
            self.get_logger().info(f"[SIM] draw {length_cm} cm")
            self._pub_status("sim", f"{length_cm} cm")
            return

        responses = self._arduino.send_length(length_cm)
        if responses:
            for line in responses:
                self.get_logger().info(f"[ARDUINO] {line}")
        else:
            self.get_logger().info(f"Sent {length_cm} cm to Arduino")
        self._pub_status("sent", f"{length_cm} cm")

    def destroy_node(self):
        if self._arduino:
            self._arduino.close()
        super().destroy_node()


def standalone_test(port: str):
    print("=" * 60)
    print("  Hardware Bridge  -  Standalone Serial Test")
    print(f"  Port: {port} @ {SERIAL_BAUD}")
    print("=" * 60)

    try:
        ard = ArduinoSerial(port, SERIAL_BAUD)
    except Exception as exc:
        print(f"[ERR] {exc}")
        print("Try: python3 4_rpi_hardware_bridge.py --port /dev/ttyACM0")
        return

    print("Sending lengths 1..6 to the Arduino sketch...")
    for length_cm in range(1, 7):
        print(f"  -> {length_cm} cm")
        responses = ard.send_length(length_cm)
        for line in responses:
            print(f"     {line}")
        time.sleep(1.0)

    print("Done. Closing.")
    ard.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument("--port", default=SERIAL_PORT)
    parser.add_argument("--sim", action="store_true")
    args = parser.parse_args()

    if args.sim:
        global _HAS_SERIAL
        _HAS_SERIAL = False

    if args.standalone or not _HAS_ROS2:
        standalone_test(args.port)
    else:
        rclpy.init()
        node = HardwareBridgeNode(port=args.port)
        try:
            rclpy.spin(node)
        except KeyboardInterrupt:
            pass
        finally:
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()