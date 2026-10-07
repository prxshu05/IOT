# Forensic Sketching Robot — Command Pipeline

A four-stage pipeline that sends a straight-line drawing command (1–6 cm) from a web dashboard, through an MQTT broker on AWS, to a Raspberry Pi running ROS 2, and finally over serial to an Arduino that moves the servos of a robotic hand.

```
 ┌──────────────┐   MQTT    ┌──────────────┐   MQTT    ┌──────────────────────┐  ROS 2   ┌──────────────────────┐  Serial  ┌─────────┐
 │ 1_gui_app.py │ ───────▶  │ 2_aws_model_ │ ───────▶  │ 3_rpi_servo_         │ ───────▶ │ 4_rpi_hardware_      │ ───────▶ │ Arduino │ ─▶ servos ─▶ pen
 │ (Streamlit)  │ commands  │ bridge.py    │ servo_    │ subscriber.py        │ /drawing │ bridge.py            │ "N\n"    │ sketch  │
 │ operator PC  │           │ AWS EC2      │ angles    │ Raspberry Pi         │ _length  │ Raspberry Pi         │ 9600 bd  │         │
 └──────────────┘           └──────────────┘           └──────────────────────┘          └──────────────────────┘          └─────────┘
        ▲                          │                              │
        └──────────── forensic_robot/status (MQTT) ◀──────────────┘
```

## Repository layout

| File | Runs on | Role |
|---|---|---|
| `1_gui_app.py` | Operator PC | Streamlit dashboard with six preset buttons (1–6 cm). Publishes commands, shows the live status feed. |
| `2_aws_model_bridge.py` | AWS EC2 | Subscribes to commands, validates and clamps the length, republishes a normalised payload. |
| `3_rpi_servo_subscriber.py` | Raspberry Pi | Subscribes to the normalised payload and publishes the length on ROS 2 topic `/drawing_length`. |
| `4_rpi_hardware_bridge.py` | Raspberry Pi | Subscribes to `/drawing_length` and writes the integer to the Arduino over serial. |

The Arduino sketch is **not** part of this repository. It must read a plain integer 1–6 with `Serial.parseInt()` at 9600 baud and convert it into the servo sweep.

## How a command travels

1. **GUI** — pressing *Draw N cm* publishes `{"primitive": "line", "length_cm": N}` to `forensic_robot/commands` (QoS 1).
2. **EC2 bridge** — parses the JSON, rounds and clamps `length_cm` to 1–6 (non-numeric input becomes 1; any primitive other than `line` or `home` becomes `home`), adds a timestamp and publishes to `forensic_robot/servo_angles` (QoS 1). It also reports progress on `forensic_robot/status`.
3. **Pi relay** — re-clamps the value and publishes a `Float32` on `/drawing_length`. `home` commands are ignored. Status messages go to the ROS topic `/drawing_status` and to `forensic_robot/status`.
4. **Hardware bridge** — re-clamps the value, sends `"<N>\n"` over serial (`/dev/ttyACM0`, 9600 baud, thread-locked), reads back any Arduino reply lines and logs them. State goes to the ROS topic `/hw_status`.

Every stage clamps the length independently, so an out-of-range value can never reach the Arduino.

## MQTT topics and payloads

| Topic | Direction | Payload |
|---|---|---|
| `forensic_robot/commands` | GUI → EC2 | `{"primitive": "line", "length_cm": 4}` |
| `forensic_robot/servo_angles` | EC2 → Pi | `{"primitive": "line", "length_cm": 4, "timestamp": "..."}` or `{"primitive": "home", ...}` |
| `forensic_robot/status` | EC2 and Pi → GUI | `{"state": "...", "detail": "...", "timestamp": "..."}` |

Despite its name, `servo_angles` carries only the normalised length. Angle calculation happens in the Arduino sketch.

## Requirements

- Python 3.9+ on every machine (the hardware bridge uses `list[str]` type hints)
- An MQTT broker reachable from all three machines (the code is written for Mosquitto on the EC2 instance; AWS IoT Core also works with TLS)
- Raspberry Pi: ROS 2 Humble and `pyserial`
- Arduino connected to the Pi by USB

```bash
# Operator PC
pip install "paho-mqtt<2" streamlit

# AWS EC2
pip install "paho-mqtt<2"

# Raspberry Pi
pip install "paho-mqtt<2" pyserial
source /opt/ros/humble/setup.bash      # provides rclpy and std_msgs
```

> **Pin `paho-mqtt<2`.** The scripts use the v1 callback signatures. With paho-mqtt 2.x, `mqtt.Client(...)` raises an error unless a callback API version is passed.

## Configuration

All settings are environment variables.

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `MQTT_HOST` | all | GUI and Pi: `13.51.178.9`; EC2: `localhost` | Broker host or IP |
| `MQTT_PORT` | all | `1883` | Broker port (use `8883` for TLS) |
| `MQTT_TLS` | all | `false` | `true`/`1`/`yes`/`on` enables TLS 1.2 with client certificates |
| `MQTT_CERT_DIR` | EC2, Pi | `~/aws_iot_certs` | Folder containing `ca.pem`, `cert.pem`, `key.pem` |
| `SERIAL_PORT` | hardware bridge | `/dev/ttyACM0` | Arduino serial device (`--port` overrides it) |

The GUI looks for `ca.pem`, `cert.pem` and `key.pem` in the same folder as `1_gui_app.py`, and its broker fields can also be edited in the sidebar.

Set `MQTT_HOST` explicitly on every machine. The hard-coded default in the GUI and Pi scripts is a specific public IP and should not be relied on.

## Running

Start the stages from the broker side outwards.

**1. Broker and EC2 bridge (AWS EC2)**
```bash
export MQTT_HOST=localhost        # or your IoT Core endpoint
python3 2_aws_model_bridge.py
```

**2. Pi relay (Raspberry Pi, terminal 1)**
```bash
source /opt/ros/humble/setup.bash
export MQTT_HOST=<broker-ip-or-dns>
python3 3_rpi_servo_subscriber.py
```

**3. Hardware bridge (Raspberry Pi, terminal 2)**
```bash
source /opt/ros/humble/setup.bash
python3 4_rpi_hardware_bridge.py --port /dev/ttyACM0
```

**4. Dashboard (operator PC)**
```bash
export MQTT_HOST=<broker-ip-or-dns>
streamlit run 1_gui_app.py
```

Open the URL Streamlit prints, confirm the sidebar chip says **Live**, and press a preset button.

### TLS

```bash
export MQTT_TLS=true
export MQTT_PORT=8883
export MQTT_CERT_DIR=~/aws_iot_certs     # EC2 and Pi
```

Place `ca.pem`, `cert.pem` and `key.pem` in that folder (next to `1_gui_app.py` for the GUI). Each script checks that the files exist and stops with a clear message if one is missing. **Never commit certificates or private keys.**

### Testing without the full stack

| Command | What it does |
|---|---|
| `python3 3_rpi_servo_subscriber.py --standalone` | Runs the relay without ROS 2 and prints each received length. Used automatically if `rclpy` is missing. |
| `python3 4_rpi_hardware_bridge.py --standalone --port /dev/ttyACM0` | Sends lengths 1 to 6 to the Arduino, one per second, and prints its replies. No ROS 2 needed. |
| `python3 4_rpi_hardware_bridge.py --sim` | Intended as a dry run without serial. Note that `--sim` currently only disables the serial import flag and does not by itself force simulation inside the ROS node. |

## Troubleshooting

| Symptom | Check |
|---|---|
| GUI shows **Off** | Broker host and port, firewall or security group (1883 or 8883), TLS toggle matches the broker. |
| `TypeError` / "callback API version" when starting a script | You have paho-mqtt 2.x installed. Run `pip install "paho-mqtt<2"`. |
| `Missing certificate file` | `MQTT_TLS=true` but `ca.pem`, `cert.pem` or `key.pem` is not in the expected folder. |
| Commands reach EC2 but the arm does not move | Run the Pi relay and hardware bridge in separate terminals with ROS 2 sourced, then check `ros2 topic echo /drawing_length`. |
| `Serial error` / bridge falls back to SIM | Wrong `SERIAL_PORT`, Arduino not plugged in, or the user is not in the `dialout` group. |
| Arm does nothing for integers outside 1–6 | Expected: every layer clamps to 1–6. |

## Known limitations

- Straight lines of 1–6 cm only. `home` is accepted by the EC2 bridge but ignored by the Pi relay.
- The EC2 bridge validates and forwards commands. No model inference runs in these scripts.
- The status feed in the GUI is written from the MQTT background thread into Streamlit session state, so it may not refresh until the next interaction.
- The hardware bridge publishes its status only on the ROS topic `/hw_status`, not on MQTT.
- Plain TCP on port 1883 is the default. Enable TLS for anything beyond a lab network.
