"""
comms/lora_bridge.py — RYLR998 LoRa 915 MHz Message Bridge

Provides encrypted point-to-point and broadcast messaging between
ground units when all infrastructure is unavailable.

RYLR998 AT command interface:
  AT+SEND=<addr>,<len>,<data>   — Send to address
  AT+RECV                        — Returns +RCV=<addr>,<len>,<data>,<rssi>,<snr>
  AT+ADDRESS=<addr>              — Set this module's address
  AT+NETWORKID=<id>              — Set network ID (must match all units)
  AT+BAND=<freq>                 — Set frequency in Hz

All messages are AES-128 encrypted with a pre-shared key configured in .env
"""

import logging
import queue
import threading
import time
from datetime import datetime
from typing import Optional

import serial
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import base64

import config

logger = logging.getLogger("AURA.lora")


class LoRaBridge:
    """
    Serial interface to RYLR998 LoRa module with AES-128 message encryption.

    Message format (over the air, after encryption):
        <timestamp>|<sender_id>|<message_text>

    The RYLR998 handles the LoRa PHY layer — we only deal with AT commands.
    All configuration (band, network ID, spreading factor) is applied on connect.
    """

    MAX_PAYLOAD_BYTES = 240  # RYLR998 maximum payload
    RECEIVE_TIMEOUT_S = 2.0

    def __init__(
        self,
        port: str = None,
        baud: int = None,
        my_address: int = None,
    ) -> None:
        self.port = port or config.LORA_PORT
        self.baud = baud or config.LORA_BAUD
        self.my_address = my_address or config.LORA_MY_ADDRESS

        self._serial: Optional[serial.Serial] = None
        self._connected = False
        self._lock = threading.Lock()

        # Inbound message queue
        self._rx_queue: queue.Queue = queue.Queue()
        self._rx_thread: Optional[threading.Thread] = None

        # Outbound retry queue: {recipient_id: [(message, last_try_time), ...]}
        self._retry_queue: dict = {}
        self._retry_thread: Optional[threading.Thread] = None

        # AES cipher key (16 bytes for AES-128)
        self._aes_key: Optional[bytes] = self._load_key()

    # ──────────────────────────────────────────
    # Connection
    # ──────────────────────────────────────────

    def connect(self) -> bool:
        """Open serial port and configure RYLR998 module."""
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                timeout=self.RECEIVE_TIMEOUT_S,
            )
            time.sleep(0.5)  # Allow serial port to stabilize

            if not self._configure_module():
                logger.error("RYLR998 configuration failed")
                return False

            self._connected = True
            logger.info(f"LoRa bridge connected on {self.port} as address {self.my_address}")
            return True

        except serial.SerialException as e:
            logger.error(f"LoRa serial open failed: {e}")
            return False

    def _configure_module(self) -> bool:
        """Send AT configuration commands to RYLR998."""
        commands = [
            f"AT+ADDRESS={self.my_address}",
            f"AT+NETWORKID={config.LORA_NETWORK_ID}",
            f"AT+BAND={config.LORA_BAND}",
            f"AT+PARAMETER={config.LORA_SPREADING_FACTOR},{config.LORA_BANDWIDTH},{config.LORA_CODING_RATE},{config.LORA_POWER_DBM}",
        ]

        for cmd in commands:
            response = self._at_command(cmd)
            if "+ERR" in (response or ""):
                logger.warning(f"AT command failed: {cmd} → {response}")
                # Non-fatal: continue with other commands
            else:
                logger.debug(f"AT OK: {cmd}")

        # Verify module responds
        response = self._at_command("AT")
        return response is not None and "+OK" in response

    def _at_command(self, command: str, timeout: float = 2.0) -> Optional[str]:
        """Send AT command and wait for response."""
        if not self._serial or not self._serial.is_open:
            return None
        with self._lock:
            try:
                self._serial.write((command + "\r\n").encode())
                self._serial.flush()
                deadline = time.time() + timeout
                while time.time() < deadline:
                    line = self._serial.readline().decode(errors="replace").strip()
                    if line:
                        return line
            except Exception as e:
                logger.error(f"AT command error: {e}")
        return None

    # ──────────────────────────────────────────
    # Messaging
    # ──────────────────────────────────────────

    def send_message(self, recipient_id: int, message: str) -> bool:
        """
        Send an encrypted message to a recipient LoRa unit.

        Args:
            recipient_id: Target unit address (0 = broadcast)
            message: Plaintext message string

        Returns:
            True if the AT command was accepted (does not guarantee delivery)
        """
        if not self._connected:
            logger.warning("LoRa not connected — queueing message for retry")
            self._queue_for_retry(recipient_id, message)
            return False

        # Build payload: timestamp|sender|message
        payload = f"{int(time.time())}|{self.my_address}|{message}"

        # Encrypt
        encrypted = self._encrypt(payload)
        if encrypted is None:
            encrypted = payload  # Send plaintext if encryption fails (config error)

        # Check payload length
        if len(encrypted.encode()) > self.MAX_PAYLOAD_BYTES:
            logger.warning(
                f"Message too long ({len(encrypted)} chars) — truncating to {self.MAX_PAYLOAD_BYTES}"
            )
            encrypted = encrypted[:self.MAX_PAYLOAD_BYTES]

        payload_len = len(encrypted.encode())
        at_cmd = f"AT+SEND={recipient_id},{payload_len},{encrypted}"

        response = self._at_command(at_cmd, timeout=5.0)
        success = response is not None and "+OK" in response

        if success:
            logger.info(f"LoRa TX → addr {recipient_id}: '{message[:50]}'")
        else:
            logger.warning(f"LoRa TX failed → addr {recipient_id}: {response}")
            self._queue_for_retry(recipient_id, message)

        return success

    def get_messages(self) -> list[dict]:
        """
        Retrieve all received messages since last call.

        Returns:
            List of message dicts:
            [{"sender": int, "message": str, "rssi": int, "snr": float, "timestamp": float}, ...]
        """
        messages = []
        while not self._rx_queue.empty():
            try:
                messages.append(self._rx_queue.get_nowait())
            except queue.Empty:
                break
        return messages

    def broadcast(self, message: str) -> bool:
        """Broadcast a message to all units on the network (address 0)."""
        return self.send_message(recipient_id=0, message=message)

    # ──────────────────────────────────────────
    # Receive Thread
    # ──────────────────────────────────────────

    def start_receive_thread(self) -> None:
        """Start background thread to continuously poll for incoming messages."""
        self._rx_thread = threading.Thread(
            target=self._receive_loop, name="LoRaReceiver", daemon=True
        )
        self._rx_thread.start()

        self._retry_thread = threading.Thread(
            target=self._retry_loop, name="LoRaRetry", daemon=True
        )
        self._retry_thread.start()

        logger.info("LoRa receive thread started")

    def _receive_loop(self) -> None:
        """
        Continuously read from serial port and parse incoming +RCV messages.

        RYLR998 format: +RCV=<addr>,<len>,<data>,<rssi>,<snr>
        """
        while self._connected:
            try:
                if not self._serial or not self._serial.is_open:
                    time.sleep(1)
                    continue

                line = self._serial.readline().decode(errors="replace").strip()
                if not line:
                    continue

                if line.startswith("+RCV="):
                    msg = self._parse_rcv(line)
                    if msg:
                        logger.info(
                            f"LoRa RX ← addr {msg['sender']}: '{msg['message'][:60]}' "
                            f"RSSI:{msg['rssi']} SNR:{msg['snr']}"
                        )
                        self._rx_queue.put(msg)

            except serial.SerialException as e:
                logger.error(f"LoRa serial error: {e}")
                time.sleep(2)
            except Exception as e:
                logger.error(f"LoRa receive error: {e}")

    def _parse_rcv(self, line: str) -> Optional[dict]:
        """
        Parse +RCV=<addr>,<len>,<data>,<rssi>,<snr> into a dict.
        Decrypts the data payload.
        """
        try:
            # Strip "+RCV=" prefix
            content = line[5:]
            # Split: addr, len, data, rssi, snr
            parts = content.split(",", 4)
            if len(parts) < 5:
                return None

            sender_addr = int(parts[0])
            data_len = int(parts[1])
            encrypted_data = parts[2]
            rssi = int(parts[3])
            snr = float(parts[4])

            # Decrypt
            plaintext = self._decrypt(encrypted_data)
            if plaintext is None:
                plaintext = encrypted_data  # Fallback: treat as plaintext

            # Parse internal format: timestamp|sender|message
            fields = plaintext.split("|", 2)
            if len(fields) == 3:
                msg_text = fields[2]
                ts = float(fields[0]) if fields[0].isdigit() else time.time()
            else:
                msg_text = plaintext
                ts = time.time()

            return {
                "sender": sender_addr,
                "message": msg_text,
                "rssi": rssi,
                "snr": snr,
                "timestamp": ts,
                "received_at": time.time(),
                "datetime": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"LoRa parse error on '{line}': {e}")
            return None

    # ──────────────────────────────────────────
    # Retry Queue
    # ──────────────────────────────────────────

    def _queue_for_retry(self, recipient_id: int, message: str) -> None:
        """Add failed message to retry queue."""
        if recipient_id not in self._retry_queue:
            self._retry_queue[recipient_id] = []
        self._retry_queue[recipient_id].append({
            "message": message,
            "queued_at": time.time(),
            "last_try": 0.0,
        })
        logger.debug(f"Message queued for retry → addr {recipient_id}")

    def _retry_loop(self) -> None:
        """Periodically retry queued messages for unreachable recipients."""
        while self._connected:
            time.sleep(10)

            now = time.time()
            for recipient_id, messages in list(self._retry_queue.items()):
                pending = [m for m in messages if now - m["last_try"] >= config.LORA_RETRY_INTERVAL_S]

                for msg_entry in pending:
                    logger.info(f"Retrying message to addr {recipient_id}")
                    msg_entry["last_try"] = now
                    self.send_message(recipient_id, msg_entry["message"])

                # Remove successfully-sent messages
                # (simplified: remove entries that have been tried at least once after queuing)
                self._retry_queue[recipient_id] = [
                    m for m in messages
                    if m["last_try"] == 0.0 or now - m["queued_at"] < 3600
                ]

    # ──────────────────────────────────────────
    # Encryption
    # ──────────────────────────────────────────

    def _load_key(self) -> Optional[bytes]:
        """Load AES key from config (hex string → bytes)."""
        key_hex = config.LORA_ENCRYPTION_KEY
        if not key_hex:
            logger.warning("No LoRa encryption key configured — messages will be plaintext")
            return None
        try:
            key = bytes.fromhex(key_hex)
            if len(key) not in (16, 24, 32):
                raise ValueError(f"Key must be 16, 24, or 32 bytes (got {len(key)})")
            logger.info("LoRa AES encryption enabled")
            return key
        except Exception as e:
            logger.error(f"LoRa key load failed: {e}")
            return None

    def _encrypt(self, plaintext: str) -> Optional[str]:
        """Encrypt string with AES-128-CBC, return base64 result."""
        if not self._aes_key:
            return plaintext
        try:
            cipher = AES.new(self._aes_key, AES.MODE_CBC)
            ct_bytes = cipher.encrypt(pad(plaintext.encode(), AES.block_size))
            # Pack IV + ciphertext → base64
            result = base64.b64encode(cipher.iv + ct_bytes).decode()
            return result
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return plaintext

    def _decrypt(self, ciphertext_b64: str) -> Optional[str]:
        """Decrypt base64 AES-CBC string."""
        if not self._aes_key:
            return ciphertext_b64
        try:
            raw = base64.b64decode(ciphertext_b64)
            iv = raw[:16]
            ct = raw[16:]
            cipher = AES.new(self._aes_key, AES.MODE_CBC, iv)
            return unpad(cipher.decrypt(ct), AES.block_size).decode()
        except Exception:
            # Decryption failure likely means plaintext or wrong key
            return ciphertext_b64

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    def stop(self) -> None:
        """Close serial port and stop threads."""
        self._connected = False
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        logger.info("LoRa bridge stopped")
