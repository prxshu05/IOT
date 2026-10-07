"""
1_gui_app.py
============
Streamlit GUI for sending straight-line commands in the 1-6 cm range.

Run:
    streamlit run 1_gui_app.py
"""

import json
import os
import ssl
import threading
import time

import paho.mqtt.client as mqtt
import streamlit as st

MQTT_HOST = os.environ.get("MQTT_HOST", "13.51.178.9")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TLS = os.environ.get("MQTT_TLS", "false").lower() in {"1", "true", "yes", "on"}
MQTT_CERT_DIR = os.path.dirname(os.path.abspath(__file__))
MQTT_CA = os.path.join(MQTT_CERT_DIR, "ca.pem")
MQTT_CERT = os.path.join(MQTT_CERT_DIR, "cert.pem")
MQTT_KEY = os.path.join(MQTT_CERT_DIR, "key.pem")
TOPIC_COMMANDS = "forensic_robot/commands"
TOPIC_STATUS = "forensic_robot/status"


def _on_connect(client, userdata, flags, rc):
    if userdata is not None:
        userdata["connect_rc"] = rc
        userdata["connect_event"].set()
    if rc == 0:
        client.subscribe(TOPIC_STATUS, qos=1)


def _on_disconnect(client, userdata, rc):
    st.session_state.mqtt_connected = False


def _on_message(client, userdata, msg):
    try:
        ts = time.strftime("%H:%M:%S")
        st.session_state.status_log.append(f"[{ts}] {msg.payload.decode()[:120]}")
        if len(st.session_state.status_log) > 20:
            st.session_state.status_log.pop(0)
    except Exception:
        pass


def connect_mqtt(host, port, use_tls):
    if use_tls:
        for path in (MQTT_CA, MQTT_CERT, MQTT_KEY):
            if not os.path.exists(path):
                return False, f"Missing certificate file: {path}"

    try:
        connect_state = {"connect_event": threading.Event(), "connect_rc": None}
        client = mqtt.Client(client_id=f"forensic_gui_{int(time.time())}", protocol=mqtt.MQTTv311)
        if use_tls:
            client.tls_set(
                ca_certs=MQTT_CA,
                certfile=MQTT_CERT,
                keyfile=MQTT_KEY,
                tls_version=ssl.PROTOCOL_TLSv1_2,
            )
        client.user_data_set(connect_state)
        client.on_connect = _on_connect
        client.on_disconnect = _on_disconnect
        client.on_message = _on_message
        client.connect(host, port, keepalive=60)
        client.loop_start()

        st.session_state.mqtt_client = client
        if not connect_state["connect_event"].wait(timeout=5):
            st.session_state.mqtt_client = None
            return False, "Timed out waiting for MQTT CONNACK."

        if connect_state["connect_rc"] == 0:
            st.session_state.mqtt_connected = True
            mode = "TLS" if use_tls else "plain TCP"
            return True, f"Connected to MQTT broker ({mode})"

        st.session_state.mqtt_client = None
        st.session_state.mqtt_connected = False
        return False, f"MQTT broker rejected the connection (rc={connect_state['connect_rc']})."
    except Exception as exc:
        st.session_state.mqtt_client = None
        st.session_state.mqtt_connected = False
        return False, f"Error: {exc}"


def get_broker_config():
    return (
        st.session_state.broker_host.strip(),
        int(st.session_state.broker_port),
        bool(st.session_state.broker_tls),
    )


def connect_current_broker(force=False):
    host, port, use_tls = get_broker_config()
    if not host:
        st.session_state.connection_status = "idle"
        st.session_state.connection_message = "Enter a broker host to connect automatically."
        return False, st.session_state.connection_message

    config = (host, port, use_tls)
    if not force and st.session_state.get("mqtt_connected") and st.session_state.get("connected_broker_config") == config:
        st.session_state.connection_status = "success"
        st.session_state.connection_message = "Already connected."
        return True, st.session_state.connection_message

    ok, msg = connect_mqtt(host, port, use_tls)
    st.session_state.connection_status = "success" if ok else "error"
    st.session_state.connection_message = msg
    st.session_state.last_auto_connect_attempt = config
    if ok:
        st.session_state.connected_broker_config = config
    return ok, msg


def maybe_auto_connect():
    if not st.session_state.get("auto_connect", True):
        return

    host, port, use_tls = get_broker_config()
    if not host:
        return

    config = (host, port, use_tls)
    if st.session_state.get("mqtt_connected") and st.session_state.get("connected_broker_config") == config:
        return
    if st.session_state.get("last_auto_connect_attempt") == config:
        return

    connect_current_broker(force=True)


def build_line_command(length_cm):
    length_cm = int(max(1, min(6, int(length_cm))))
    return {"primitive": "line", "length_cm": length_cm}


def publish(payload):
    client = st.session_state.get("mqtt_client")
    if client is None or not st.session_state.get("mqtt_connected"):
        ok, msg = connect_current_broker(force=False)
        if not ok:
            return False, msg
        client = st.session_state.get("mqtt_client")

    result = client.publish(TOPIC_COMMANDS, json.dumps(payload), qos=1)
    if result.rc == mqtt.MQTT_ERR_SUCCESS:
        st.session_state.last_cmd = payload
        return True, "Published to MQTT broker"
    return False, f"Failed rc={result.rc}"


def publish_length(length_cm):
    return publish(build_line_command(length_cm))


def inject_clean_theme():
    st.markdown(
        """
        <style>
            .stApp {
                background:
                    radial-gradient(circle at top left, rgba(79, 129, 189, 0.15), transparent 26%),
                    radial-gradient(circle at top right, rgba(24, 180, 150, 0.13), transparent 24%),
                    linear-gradient(180deg, #f8fafc 0%, #eef3f8 100%);
                color: #132238;
            }
            #MainMenu, footer, header { visibility: hidden; }
            .block-container {
                padding-top: 1.1rem;
                padding-bottom: 1.4rem;
                max-width: 1280px;
            }
            section[data-testid="stSidebar"] {
                background: linear-gradient(180deg, #0f172a 0%, #111827 100%);
            }
            section[data-testid="stSidebar"] p,
            section[data-testid="stSidebar"] h1,
            section[data-testid="stSidebar"] h2,
            section[data-testid="stSidebar"] h3,
            section[data-testid="stSidebar"] h4,
            section[data-testid="stSidebar"] label,
            section[data-testid="stSidebar"] li,
            section[data-testid="stSidebar"] span {
                color: #e5eefb;
            }
            .hero-card, .glass-card {
                background: rgba(255, 255, 255, 0.8);
                border: 1px solid rgba(148, 163, 184, 0.2);
                box-shadow: 0 18px 45px rgba(15, 23, 42, 0.08);
                backdrop-filter: blur(14px);
                border-radius: 24px;
            }
            .hero-card {
                padding: 1.5rem 1.7rem 1.3rem 1.7rem;
                margin-bottom: 1rem;
            }
            .hero-kicker {
                text-transform: uppercase;
                letter-spacing: 0.18em;
                font-size: 0.72rem;
                font-weight: 700;
                color: #5b6475;
                margin-bottom: 0.35rem;
            }
            .hero-title {
                font-size: 2.15rem;
                line-height: 1.05;
                font-weight: 800;
                color: #0f172a;
                margin-bottom: 0.45rem;
            }
            .hero-copy {
                font-size: 0.98rem;
                line-height: 1.45;
                color: #475569;
                max-width: 72ch;
            }
            .section-label {
                margin: 0 0 0.6rem 0;
                font-size: 0.82rem;
                font-weight: 700;
                letter-spacing: 0.12em;
                text-transform: uppercase;
                color: #64748b;
            }
            .card-copy {
                color: #475569;
                font-size: 0.95rem;
                line-height: 1.45;
            }
            .chip {
                display: inline-flex;
                align-items: center;
                gap: 0.4rem;
                border-radius: 999px;
                padding: 0.35rem 0.75rem;
                font-size: 0.8rem;
                font-weight: 700;
                border: 1px solid rgba(148, 163, 184, 0.24);
                background: rgba(255, 255, 255, 0.7);
                color: #334155;
            }
            .chip.success {
                background: rgba(16, 185, 129, 0.12);
                color: #047857;
                border-color: rgba(16, 185, 129, 0.18);
            }
            .chip.neutral {
                background: rgba(59, 130, 246, 0.1);
                color: #1d4ed8;
                border-color: rgba(59, 130, 246, 0.16);
            }
            .preset-box {
                border: 1px solid rgba(148, 163, 184, 0.2);
                border-radius: 20px;
                padding: 1rem 0.9rem 0.85rem 0.9rem;
                background: rgba(255, 255, 255, 0.76);
                box-shadow: 0 10px 25px rgba(15, 23, 42, 0.05);
                text-align: center;
                margin-bottom: 0.7rem;
            }
            .preset-value {
                font-size: 2rem;
                line-height: 1;
                font-weight: 800;
                color: #0f172a;
            }
            .preset-unit {
                margin-top: 0.3rem;
                font-size: 0.84rem;
                letter-spacing: 0.14em;
                text-transform: uppercase;
                color: #64748b;
                font-weight: 700;
            }
            .stButton > button {
                border-radius: 14px;
                font-weight: 700;
                padding-top: 0.7rem;
                padding-bottom: 0.7rem;
                border: 1px solid rgba(148, 163, 184, 0.24);
                box-shadow: 0 8px 18px rgba(15, 23, 42, 0.06);
            }
            section[data-testid="stSidebar"] .stButton > button,
            section[data-testid="stSidebar"] .stButton > button * {
                color: #0f172a !important;
            }
            section[data-testid="stSidebar"] .stButton > button {
                background: rgba(255, 255, 255, 0.96) !important;
            }
            .stButton > button[kind="primary"] {
                background: linear-gradient(135deg, #2563eb 0%, #0ea5e9 100%);
                border: none;
            }
            div[data-testid="stMetric"] {
                background: rgba(255, 255, 255, 0.72);
                border: 1px solid rgba(148, 163, 184, 0.18);
                border-radius: 18px;
                padding: 0.8rem 0.9rem;
                box-shadow: 0 10px 25px rgba(15, 23, 42, 0.05);
            }
            .stExpander {
                border-radius: 18px;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


st.set_page_config(
    page_title="Forensic Sketch Robot",
    page_icon="✏️",
    layout="wide",
    initial_sidebar_state="expanded",
)

for key, value in [
    ("mqtt_client", None),
    ("mqtt_connected", False),
    ("last_cmd", None),
    ("status_log", []),
]:
    if key not in st.session_state:
        st.session_state[key] = value

if "connection_status" not in st.session_state:
    st.session_state.connection_status = "idle"
if "connection_message" not in st.session_state:
    st.session_state.connection_message = ""
if "connected_broker_config" not in st.session_state:
    st.session_state.connected_broker_config = None
if "last_auto_connect_attempt" not in st.session_state:
    st.session_state.last_auto_connect_attempt = None
if "auto_connect" not in st.session_state:
    st.session_state.auto_connect = True
if "broker_host" not in st.session_state or (st.session_state.get("broker_host") == "localhost" and not MQTT_HOST):
    st.session_state.broker_host = MQTT_HOST
if "broker_port" not in st.session_state:
    st.session_state.broker_port = MQTT_PORT
if "broker_tls" not in st.session_state:
    st.session_state.broker_tls = MQTT_TLS

maybe_auto_connect()
inject_clean_theme()

st.markdown(
    """
    <div class="hero-card">
      <div class="hero-kicker">Robotic sketch control center</div>
      <div class="hero-title">Forensic Sketching Robot</div>
      <div class="hero-copy">Pick a number from 1 to 6 and the GUI will publish a straight-line command of the same length. This matches the tested Arduino behavior: 1 cm means 1, 2 cm means 2, and so on.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

top_a, top_b, top_c = st.columns(3, gap="medium")
with top_a:
    st.metric("MQTT", "Live" if st.session_state.mqtt_connected else "Off")
with top_b:
    st.metric("Supported input", "1 to 6 cm")
with top_c:
    st.metric("Command type", "Straight line")

with st.sidebar:
    st.markdown("### MQTT Broker")
    st.caption("Point this at your EC2 Mosquitto host or any MQTT broker.")
    st.text_input("Host", key="broker_host", placeholder="ec2-public-ip-or-dns")
    st.number_input("Port", min_value=1, max_value=65535, key="broker_port", step=1)
    st.checkbox("Use TLS", key="broker_tls")
    st.checkbox("Auto-connect", key="auto_connect")
    st.caption("Auto-connect stays on. Enter the broker host once and it will connect on load and after changes.")
    connect_col, status_col = st.columns([1.3, 1])
    if connect_col.button("Connect", use_container_width=True):
        with st.spinner("Connecting…"):
            ok, msg = connect_current_broker(force=True)
        if ok:
            st.success(msg)
        else:
            st.error(msg)
    status_col.markdown(
        f'<span class="chip {"success" if st.session_state.mqtt_connected else "neutral"}">{"Live" if st.session_state.mqtt_connected else "Off"}</span>',
        unsafe_allow_html=True,
    )
    st.markdown("<div class='section-label'>Topics</div>", unsafe_allow_html=True)
    st.code(f"PUB  {TOPIC_COMMANDS}\nSUB  {TOPIC_STATUS}")
    broker_label = st.session_state.broker_host.strip() or "not set"
    st.caption(f"Connected broker: {broker_label}:{int(st.session_state.broker_port)}")
    if st.session_state.connection_message:
        if st.session_state.connection_status == "success":
            st.success(st.session_state.connection_message)
        elif st.session_state.connection_status == "error":
            st.error(st.session_state.connection_message)
        else:
            st.info(st.session_state.connection_message)

st.markdown("<div class='section-label'>Quick presets</div>", unsafe_allow_html=True)
st.markdown("<div class='card-copy'>Each preset sends a discrete command. No slider, no voice parsing, and no angle math exposed to the user.</div>", unsafe_allow_html=True)

for row in ((1, 2, 3), (4, 5, 6)):
    columns = st.columns(3, gap="medium")
    for column, length_cm in zip(columns, row):
        with column:
            st.markdown(
                f"""
                <div class="preset-box">
                  <div class="preset-value">{length_cm}</div>
                  <div class="preset-unit">cm</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button(f"Draw {length_cm} cm", key=f"draw_{length_cm}", use_container_width=True):
                ok, msg = publish_length(length_cm)
                if ok:
                    st.success(f"{msg}: line length {length_cm} cm")
                else:
                    st.error(msg)

left, right = st.columns([1.05, 1], gap="large")
with left:
    st.markdown("<div class='section-label'>Last command</div>", unsafe_allow_html=True)
    if st.session_state.last_cmd:
        st.json(st.session_state.last_cmd)
    else:
        st.info("No command published yet.")

    st.markdown("<div class='section-label'>Status feed</div>", unsafe_allow_html=True)
    if st.session_state.status_log:
        for message in reversed(st.session_state.status_log[-6:]):
            st.caption(message)
    else:
        st.caption("Waiting for status messages from the MQTT chain.")

with right:
    st.markdown("<div class='section-label'>What gets sent</div>", unsafe_allow_html=True)
    st.code(
        '{\n  "primitive": "line",\n  "length_cm": 1\n}',
        language="json",
    )
    st.markdown(
        "<div class='card-copy'>The downstream Python scripts should forward the same discrete length value until it reaches the Arduino, where the tested sketch converts it into the final servo move.</div>",
        unsafe_allow_html=True,
    )

st.markdown("<div style='height: 0.35rem'></div>", unsafe_allow_html=True)
st.caption(
    f"Broker: {st.session_state.broker_host}:{int(st.session_state.broker_port)}  ·  Topic: {TOPIC_COMMANDS}  ·  Input: 1-6 cm"
)