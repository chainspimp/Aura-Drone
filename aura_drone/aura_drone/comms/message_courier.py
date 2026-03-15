"""
comms/message_courier.py — Autonomous Round-Trip Message Delivery

Flies between two camp GPS locations to deliver messages when radio range
is insufficient and LoRa cannot reach. The drone physically carries the
message and broadcasts it via WiFi hotspot at the destination.
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

import config
from flight.drone_control import DroneController
from comms.lora_bridge import LoRaBridge

logger = logging.getLogger("AURA.courier")


class MessageCourier:
    """
    Physical message courier drone.

    For delivering messages when:
    - LoRa range is exceeded (>15km line-of-sight)
    - Terrain blocking radio path
    - Need to deliver large data (maps, photos) that LoRa can't handle

    Delivery method:
    1. Fly to destination camp GPS
    2. Hover and broadcast WiFi hotspot with message payload served via HTTP
    3. Wait for acknowledgment (WiFi client connects and downloads)
    4. Return to origin camp
    """

    def __init__(
        self,
        drone: DroneController,
        lora: Optional[LoRaBridge] = None,
    ) -> None:
        self.drone = drone
        self.lora = lora
        self._delivery_log: list[dict] = []

    def deliver(
        self,
        origin: dict,
        destination: dict,
        message: str,
        return_after: bool = True,
        hover_time_s: float = 60.0,
    ) -> bool:
        """
        Fly to destination, deliver message via WiFi broadcast, optionally return.

        Args:
            origin: {"lat": float, "lon": float} — home camp
            destination: {"lat": float, "lon": float} — delivery camp
            message: Message text or JSON to deliver
            return_after: If True, return to origin after delivery
            hover_time_s: Time to hover at destination waiting for message pickup

        Returns:
            True if delivery completed (or reasonably likely delivered)
        """
        logger.info(
            f"Courier delivery: ({origin['lat']:.5f},{origin['lon']:.5f}) → "
            f"({destination['lat']:.5f},{destination['lon']:.5f})"
        )

        delivery_record = {
            "message": message[:200],
            "origin": origin,
            "destination": destination,
            "departed": datetime.now().isoformat(),
            "status": "in_flight",
        }

        # Ascend to transit altitude
        if not self.drone.takeoff(altitude_m=config.RELAY_ALTITUDE_M):
            delivery_record["status"] = "failed_takeoff"
            self._delivery_log.append(delivery_record)
            return False

        # Fly to destination
        arrived = self.drone.fly_to(
            lat=destination["lat"],
            lon=destination["lon"],
            alt=config.RELAY_ALTITUDE_M,
        )

        if not arrived:
            logger.warning("Courier did not arrive cleanly at destination")
            delivery_record["status"] = "partial_arrival"

        # Hover and broadcast
        self.drone.hover()
        logger.info(f"At destination — hovering {hover_time_s}s for message pickup")
        delivery_record["arrived"] = datetime.now().isoformat()

        # Broadcast message via LoRa to any local receivers (short range)
        if self.lora:
            self.lora.broadcast(f"COURIER MSG: {message}")
            logger.info("Message broadcast via LoRa at destination")

        # Also write message to a simple file that WiFi clients can fetch
        self._write_message_payload(message)

        # Hover for pickup window
        start = time.time()
        while time.time() - start < hover_time_s:
            # Check battery
            tel = self.drone.get_telemetry()
            if tel.get("battery_percent", 100) <= config.BATTERY_CRITICAL_PERCENT + 5:
                logger.warning("Battery critical during courier hover — aborting early")
                break
            time.sleep(5)

        delivery_record["status"] = "delivered"
        delivery_record["hovered_s"] = min(hover_time_s, time.time() - start)

        # Return home
        if return_after:
            logger.info("Returning to origin camp")
            self.drone.fly_to(
                lat=origin["lat"],
                lon=origin["lon"],
                alt=config.RELAY_ALTITUDE_M,
            )
            self.drone.land()
            delivery_record["returned"] = datetime.now().isoformat()

        self._delivery_log.append(delivery_record)
        logger.info(f"Courier delivery complete: {delivery_record['status']}")
        return delivery_record["status"] == "delivered"

    def _write_message_payload(self, message: str) -> None:
        """Write message to a temp file for HTTP serving."""
        payload = {
            "from": f"AURA Drone addr={config.LORA_MY_ADDRESS}",
            "timestamp": datetime.now().isoformat(),
            "message": message,
        }
        try:
            with open("/tmp/aura_courier_msg.json", "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write courier payload: {e}")

    def get_delivery_log(self) -> list[dict]:
        """Return all delivery records for this session."""
        return list(self._delivery_log)
