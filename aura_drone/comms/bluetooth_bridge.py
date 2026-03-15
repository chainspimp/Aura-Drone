"""
comms/bluetooth_bridge.py — Bluetooth RFCOMM Ground Station Bridge

Runs a Bluetooth Serial Port Profile (SPP) server on the Jetson Orin Nano.
A laptop operator connects using bt_client.py (or any RFCOMM terminal) and
gets a live stream of telemetry, alerts, and detections — plus full
two-way command control.

Why Bluetooth over WiFi for survival:
  - No router or infrastructure needed
  - Pairs directly laptop ↔ drone
  - 10–100m range depending on adapter class
  - Class 1 USB adapter (e.g. Plugable USB-BT4LE) reaches ~100m LOS
  - Consumes ~1W vs 3W for the Alfa WiFi adapter

Protocol:
  - JSON lines over RFCOMM channel 1
  - Each line is a complete JSON object terminated by newline
  - Server → Client: telemetry updates, alerts, detections (pushed ~2/sec)
  - Client → Server: command objects {"cmd": "takeoff", "params": {"alt": 30}}

Install on Jetson:
    sudo apt install bluetooth bluez python3-bluetooth
    pip install PyBluez

Pair from laptop:
    bluetoothctl
    > scan on
    > pair <JETSON_BT_MAC>
    > trust <JETSON_BT_MAC>
    Then run bt_client.py on your laptop.
"""

import json
import logging
import threading
import time
from datetime import datetime
from typing import Optional, Callable

logger = logging.getLogger("AURA.bluetooth")

try:
    import bluetooth
    PYBLUEZ_AVAILABLE = True
except ImportError:
    logger.warning("PyBluez not installed — Bluetooth bridge disabled. "
                   "Install: sudo apt install bluetooth bluez python3-bluetooth && pip install PyBluez")
    PYBLUEZ_AVAILABLE = False


# Service UUID for AURA drone — clients use this to find the right RFCOMM channel
AURA_BT_UUID = "94f39d29-7d6d-437d-973b-fba39e49d4ee"
AURA_BT_SERVICE_NAME = "AURA Drone Ground Station"
RFCOMM_CHANNEL = 1


class BluetoothBridge:
    """
    Bluetooth SPP (Serial Port Profile) server.

    Accepts one client connection at a time (laptop ground station).
    Pushes telemetry + alerts to client at 2Hz.
    Receives commands from client and dispatches via on_command callback.

    Multiple connection attempts are handled — if client disconnects
    (walked out of range, laptop closed) the server waits for reconnect
    without requiring a drone restart.
    """

    def __init__(
        self,
        drone=None,
        alerts=None,
        yolo=None,
        thermal=None,
        on_command: Optional[Callable] = None,
    ) -> None:
        self.drone = drone
        self.alerts = alerts
        self.yolo = yolo
        self.thermal = thermal
        self.on_command = on_command

        self._server_sock = None
        self._client_sock = None
        self._client_addr = None
        self._running = False
        self._connected = False
        self._send_lock = threading.Lock()

        # Push thread — sends telemetry/alerts to client continuously
        self._push_thread: Optional[threading.Thread] = None
        # Receive thread — listens for commands from client
        self._recv_thread: Optional[threading.Thread] = None
        # Accept thread — waits for new client connections
        self._accept_thread: Optional[threading.Thread] = None

        # Track last alert count to only push new ones
        self._last_alert_idx = 0

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    def start(self) -> bool:
        """
        Open RFCOMM server socket and begin accepting connections.
        Non-blocking — returns immediately, runs in background threads.
        """
        if not PYBLUEZ_AVAILABLE:
            logger.error("PyBluez not available — cannot start Bluetooth bridge")
            return False

        try:
            self._server_sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
            self._server_sock.bind(("", RFCOMM_CHANNEL))
            self._server_sock.listen(1)  # Queue of 1 — only one operator at a time

            # Advertise service via SDP so clients can discover by UUID
            bluetooth.advertise_service(
                self._server_sock,
                AURA_BT_SERVICE_NAME,
                service_id=AURA_BT_UUID,
                service_classes=[AURA_BT_UUID, bluetooth.SERIAL_PORT_CLASS],
                profiles=[bluetooth.SERIAL_PORT_PROFILE],
            )

            self._running = True
            self._accept_thread = threading.Thread(
                target=self._accept_loop,
                name="BT_Accept",
                daemon=True,
            )
            self._accept_thread.start()

            logger.info(
                f"Bluetooth bridge listening on RFCOMM channel {RFCOMM_CHANNEL} "
                f"— service: '{AURA_BT_SERVICE_NAME}'"
            )
            return True

        except Exception as e:
            logger.error(f"Bluetooth bridge start failed: {e}")
            logger.error("Make sure Bluetooth is enabled: sudo systemctl start bluetooth")
            return False

    def stop(self) -> None:
        """Shut down server and disconnect any active client."""
        self._running = False
        self._disconnect_client()
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        logger.info("Bluetooth bridge stopped")

    # ──────────────────────────────────────────
    # Connection Management
    # ──────────────────────────────────────────

    def _accept_loop(self) -> None:
        """
        Wait for incoming client connections.
        After a client disconnects, immediately waits for the next one —
        so the operator can reconnect without restarting the drone.
        """
        while self._running:
            try:
                logger.info("Bluetooth: waiting for operator to connect...")
                client_sock, client_addr = self._server_sock.accept()

                self._client_sock = client_sock
                self._client_addr = client_addr
                self._connected = True

                logger.info(f"Bluetooth: operator connected from {client_addr}")

                # Announce connection via alerts
                if self.alerts:
                    self.alerts.add_alert(
                        "BT CONNECTED",
                        f"Laptop ground station connected via Bluetooth ({client_addr[0]})",
                        "info",
                    )

                # Send welcome packet immediately
                self._send({
                    "type": "welcome",
                    "message": "AURA Drone connected",
                    "timestamp": time.time(),
                    "version": "1.0",
                })

                # Start push and receive threads for this client
                self._start_client_threads()

            except Exception as e:
                if self._running:
                    logger.error(f"Bluetooth accept error: {e}")
                    time.sleep(2)

    def _start_client_threads(self) -> None:
        """Start per-client push and receive threads."""
        self._push_thread = threading.Thread(
            target=self._push_loop, name="BT_Push", daemon=True
        )
        self._recv_thread = threading.Thread(
            target=self._receive_loop, name="BT_Recv", daemon=True
        )
        self._push_thread.start()
        self._recv_thread.start()

    def _disconnect_client(self) -> None:
        """Close the active client connection cleanly."""
        self._connected = False
        if self._client_sock:
            try:
                self._client_sock.close()
            except Exception:
                pass
            self._client_sock = None
            self._client_addr = None
            logger.info("Bluetooth: client disconnected")

    # ──────────────────────────────────────────
    # Data Push (Jetson → Laptop)
    # ──────────────────────────────────────────

    def _push_loop(self) -> None:
        """
        Push telemetry + new alerts + detections to client at ~2Hz.
        Stops when client disconnects.
        """
        while self._connected and self._running:
            try:
                # Telemetry packet
                if self.drone:
                    tel = self.drone.get_telemetry()
                    self._send({
                        "type": "telemetry",
                        "data": tel,
                        "timestamp": time.time(),
                    })

                # New alerts since last push
                if self.alerts:
                    all_alerts = self.alerts.get_all()
                    new_alerts = all_alerts[self._last_alert_idx:]
                    if new_alerts:
                        self._last_alert_idx = len(all_alerts)
                        for alert in new_alerts:
                            self._send({
                                "type": "alert",
                                "data": alert.to_dict(),
                                "timestamp": time.time(),
                            })

                # Recent detections (last 2 seconds)
                if self.yolo:
                    detections = self.yolo.get_recent_detections(window_s=2.0)
                    if detections:
                        self._send({
                            "type": "detections",
                            "data": detections,
                            "timestamp": time.time(),
                        })

                # Thermal alerts
                if self.thermal:
                    thermal = self.thermal.get_recent_alerts(window_s=2.0)
                    if thermal:
                        self._send({
                            "type": "thermal_alerts",
                            "data": thermal,
                            "timestamp": time.time(),
                        })

            except Exception as e:
                logger.error(f"Bluetooth push error: {e}")
                self._disconnect_client()
                return

            time.sleep(0.5)  # 2Hz push rate

    # ──────────────────────────────────────────
    # Command Receive (Laptop → Jetson)
    # ──────────────────────────────────────────

    def _receive_loop(self) -> None:
        """
        Continuously read command JSON lines from client.
        Each line must be a complete JSON object.

        Expected format:
            {"cmd": "takeoff", "params": {"altitude": 30}}
            {"cmd": "land"}
            {"cmd": "return_home"}
            {"cmd": "raw", "text": "scout north"}
        """
        buffer = ""

        while self._connected and self._running:
            try:
                chunk = self._client_sock.recv(1024).decode("utf-8", errors="replace")
                if not chunk:
                    # Empty read = client disconnected
                    logger.info("Bluetooth: client closed connection")
                    self._disconnect_client()
                    return

                buffer += chunk

                # Process complete lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self._handle_command(line)

            except OSError:
                # Socket closed
                self._disconnect_client()
                return
            except Exception as e:
                logger.error(f"Bluetooth receive error: {e}")

    def _handle_command(self, raw_line: str) -> None:
        """Parse and dispatch an incoming command JSON line."""
        try:
            packet = json.loads(raw_line)
        except json.JSONDecodeError:
            logger.warning(f"BT: invalid JSON from client: {raw_line[:80]}")
            self._send({
                "type": "error",
                "message": "Invalid JSON — send: {\"cmd\": \"land\"}",
            })
            return

        cmd = packet.get("cmd", "")
        params = packet.get("params", {})
        raw_text = packet.get("text", "")

        logger.info(f"BT command received: cmd='{cmd}' params={params}")

        # Acknowledge receipt
        self._send({
            "type": "ack",
            "cmd": cmd,
            "timestamp": time.time(),
        })

        # Route to main command handler
        if self.on_command:
            if raw_text:
                # Raw text command — let the AI parser handle it
                self.on_command(raw_text, source="bluetooth")
            elif cmd:
                # Pre-structured command — convert to text for the parser
                cmd_text = self._cmd_to_text(cmd, params)
                self.on_command(cmd_text, source="bluetooth")

    @staticmethod
    def _cmd_to_text(cmd: str, params: dict) -> str:
        """Convert a structured command back to natural language for the parser."""
        mapping = {
            "takeoff": f"takeoff {params.get('altitude', '')}".strip(),
            "land": "land",
            "return_home": "return home",
            "hover": "hover",
            "patrol": "patrol",
            "scout": "scout",
            "drop_payload": "drop payload",
            "situation_report": "situation report",
            "what_do_you_see": "what do you see",
            "set_relay": "relay mode",
            "emergency_land": "land",
        }
        return mapping.get(cmd, cmd)

    # ──────────────────────────────────────────
    # Send Helper
    # ──────────────────────────────────────────

    def _send(self, data: dict) -> bool:
        """
        Send a JSON packet to the connected client.
        Thread-safe — multiple threads may call this concurrently.

        Returns:
            True if sent successfully
        """
        if not self._connected or not self._client_sock:
            return False

        try:
            line = json.dumps(data, default=str) + "\n"
            with self._send_lock:
                self._client_sock.send(line.encode("utf-8"))
            return True
        except Exception as e:
            logger.debug(f"BT send failed: {e}")
            self._disconnect_client()
            return False

    # ──────────────────────────────────────────
    # Status
    # ──────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """True if an operator laptop is currently connected."""
        return self._connected

    def get_client_address(self) -> Optional[str]:
        """Return the MAC address of the connected client, or None."""
        return self._client_addr[0] if self._client_addr else None
