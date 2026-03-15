"""
flight/drone_control.py — DroneKit MAVLink Bridge

Provides all low-level flight operations: connect, arm, takeoff, navigate,
orbit, gimbal control, payload release, and telemetry reading.

All public methods are safe to call from any thread — internal locking
prevents concurrent MAVLink command collisions.
"""

import logging
import math
import threading
import time
from typing import Any, Optional

import config

logger = logging.getLogger("AURA.drone_control")

# DroneKit import with helpful error on missing package
try:
    from dronekit import connect, VehicleMode, LocationGlobalRelative, LocationGlobal
    from pymavlink import mavutil
    DRONEKIT_AVAILABLE = True
except ImportError:
    logger.warning("dronekit not installed — flight operations will be simulated")
    DRONEKIT_AVAILABLE = False


class DroneController:
    """
    High-level flight controller interface over DroneKit/MAVLink.

    Design principles:
    - Thread-safe via internal lock (multiple subsystems may request telemetry simultaneously)
    - All navigation uses GUIDED mode — no AUTO mode dependency
    - Telemetry polling is decoupled from command execution
    - Graceful degradation: methods log errors rather than raising, so callers don't crash
    """

    def __init__(self) -> None:
        self._vehicle = None
        self._lock = threading.Lock()
        self._connected = False
        self._home_location: Optional[LocationGlobal] = None
        # Cached telemetry to avoid spamming MAVLink reads
        self._telemetry_cache: dict = {}
        self._telemetry_lock = threading.Lock()
        self._telemetry_interval_s = 0.5
        self._telemetry_thread: Optional[threading.Thread] = None

    # ──────────────────────────────────────────
    # Connection
    # ──────────────────────────────────────────

    def connect(self, port: str, baud: int) -> bool:
        """
        Connect to Pixhawk via serial (or TCP for SITL).

        Args:
            port: Serial device path (/dev/ttyUSB0) or TCP string (tcp:127.0.0.1:5760)
            baud: Baud rate (921600 for Pixhawk 6C Mini TELEM2)

        Returns:
            True if connected and heartbeat received within timeout.
        """
        if not DRONEKIT_AVAILABLE:
            logger.warning("DroneKit unavailable — running in simulation mode")
            self._connected = True
            self._start_fake_telemetry()
            return True

        try:
            logger.info(f"Connecting to {port} @ {baud}...")
            # wait_ready=True blocks until vehicle is ready (heartbeat + all streams)
            self._vehicle = connect(
                port,
                baud=baud,
                wait_ready=True,
                timeout=config.MAVLINK_TIMEOUT_S,
            )
            self._connected = True
            self._home_location = self._vehicle.home_location

            # Start continuous telemetry polling thread
            self._start_telemetry_thread()

            logger.info(
                f"Connected | Mode: {self._vehicle.mode.name} | "
                f"Armed: {self._vehicle.armed} | "
                f"System status: {self._vehicle.system_status.state}"
            )
            return True

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self._connected = False
            return False

    def close(self) -> None:
        """Disconnect from vehicle, stop all threads."""
        if self._vehicle:
            try:
                self._vehicle.close()
            except Exception:
                pass
        self._connected = False
        logger.info("MAVLink connection closed")

    # ──────────────────────────────────────────
    # Flight Operations
    # ──────────────────────────────────────────

    def takeoff(self, altitude_m: float) -> bool:
        """
        Arm the drone and ascend to the specified altitude AGL.

        Pre-checks:
        - GPS fix required (GPS_FIX_TYPE >= 3)
        - Battery above critical level
        - FCU must allow arming (all arming checks pass)

        Args:
            altitude_m: Target altitude in meters above ground level

        Returns:
            True if takeoff sequence initiated successfully
        """
        if not self._connected:
            logger.error("Not connected — cannot takeoff")
            return False

        altitude_m = min(altitude_m, config.MAX_ALTITUDE_M)

        with self._lock:
            try:
                if not DRONEKIT_AVAILABLE:
                    logger.info(f"[SIM] Takeoff to {altitude_m}m")
                    self._update_sim_telemetry(altitude=altitude_m, armed=True)
                    return True

                v = self._vehicle

                # Confirm GPS health
                if v.gps_0.fix_type < 3:
                    logger.error(f"Insufficient GPS fix: {v.gps_0.fix_type} — aborting takeoff")
                    return False

                # Switch to GUIDED mode
                logger.info("Switching to GUIDED mode")
                v.mode = VehicleMode("GUIDED")
                self._wait_for_mode("GUIDED", timeout=10)

                # Arm motors
                logger.info("Arming motors")
                v.armed = True
                timeout = time.time() + 15
                while not v.armed:
                    if time.time() > timeout:
                        logger.error("Arming timeout — check pre-arm checks on FCU")
                        return False
                    time.sleep(0.5)

                logger.info(f"Taking off to {altitude_m}m AGL")
                v.simple_takeoff(altitude_m)

                # Wait until we reach target altitude (within 0.95x)
                target = altitude_m * 0.95
                timeout = time.time() + 60
                while True:
                    current_alt = v.location.global_relative_frame.alt
                    if current_alt >= target:
                        logger.info(f"Reached {current_alt:.1f}m — takeoff complete")
                        break
                    if time.time() > timeout:
                        logger.warning(f"Takeoff timeout at {current_alt:.1f}m")
                        break
                    time.sleep(0.5)

                return True

            except Exception as e:
                logger.error(f"Takeoff error: {e}")
                return False

    def land(self) -> bool:
        """Switch to LAND mode. Vehicle descends and auto-disarms on touchdown."""
        if not self._connected:
            return False
        with self._lock:
            try:
                if not DRONEKIT_AVAILABLE:
                    logger.info("[SIM] Landing")
                    self._update_sim_telemetry(altitude=0, armed=False)
                    return True
                self._vehicle.mode = VehicleMode("LAND")
                logger.info("LAND mode activated")
                return True
            except Exception as e:
                logger.error(f"Land error: {e}")
                return False

    def return_home(self) -> bool:
        """Switch to RTL (Return To Launch) mode."""
        if not self._connected:
            return False
        with self._lock:
            try:
                if not DRONEKIT_AVAILABLE:
                    logger.info("[SIM] RTL")
                    return True
                self._vehicle.mode = VehicleMode("RTL")
                logger.info("RTL mode activated")
                return True
            except Exception as e:
                logger.error(f"RTL error: {e}")
                return False

    def hover(self) -> bool:
        """
        Hold current position by switching to LOITER mode.
        Falls back to issuing a fly_to current position in GUIDED mode.
        """
        if not self._connected:
            return False
        with self._lock:
            try:
                if not DRONEKIT_AVAILABLE:
                    logger.info("[SIM] Hover")
                    return True
                self._vehicle.mode = VehicleMode("LOITER")
                logger.info("LOITER mode — hovering in place")
                return True
            except Exception as e:
                logger.error(f"Hover error: {e}")
                return False

    def fly_to(self, lat: float, lon: float, alt: float) -> bool:
        """
        Navigate to a GPS coordinate in GUIDED mode.
        Blocks until arrival (within GUIDED_MODE_ARRIVAL_RADIUS_M) or timeout.

        Args:
            lat, lon: Target coordinates (WGS84 decimal degrees)
            alt: Target altitude in meters AGL

        Returns:
            True if target reached
        """
        if not self._connected:
            return False

        alt = min(alt, config.MAX_ALTITUDE_M)

        with self._lock:
            try:
                if not DRONEKIT_AVAILABLE:
                    logger.info(f"[SIM] Fly to ({lat:.6f}, {lon:.6f}) @ {alt}m")
                    self._update_sim_telemetry(lat=lat, lon=lon, altitude=alt)
                    return True

                v = self._vehicle
                target = LocationGlobalRelative(lat, lon, alt)

                logger.info(f"Flying to ({lat:.6f}, {lon:.6f}) @ {alt}m")
                v.simple_goto(target, airspeed=config.DEFAULT_AIRSPEED_MS)

                # Wait for arrival
                timeout = time.time() + 300  # 5-minute timeout
                while True:
                    current = v.location.global_relative_frame
                    dist = self._haversine(
                        current.lat, current.lon, lat, lon
                    )
                    if dist <= config.GUIDED_MODE_ARRIVAL_RADIUS_M:
                        logger.info(f"Arrived at target (dist={dist:.1f}m)")
                        return True
                    if time.time() > timeout:
                        logger.warning(f"fly_to timeout at {dist:.1f}m from target")
                        return False
                    time.sleep(0.5)

            except Exception as e:
                logger.error(f"fly_to error: {e}")
                return False

    def orbit(
        self,
        lat: float,
        lon: float,
        radius_m: float,
        speed_ms: float,
        duration_s: float,
    ) -> bool:
        """
        Orbit a GPS point at constant radius, yawing toward the center (camera pointed in).

        Uses MAVLink DO_ORBIT command if ArduPilot firmware supports it (Copter 4.1+).
        Falls back to computed waypoints if not supported.

        Args:
            lat, lon: Center point coordinates
            radius_m: Orbit radius in meters
            speed_ms: Tangential speed in m/s
            duration_s: Total orbit time in seconds

        Returns:
            True if orbit completed
        """
        if not self._connected:
            return False

        with self._lock:
            try:
                if not DRONEKIT_AVAILABLE:
                    logger.info(f"[SIM] Orbit ({lat:.6f}, {lon:.6f}) r={radius_m}m for {duration_s}s")
                    time.sleep(min(duration_s, 5))  # Simulate in mock mode
                    return True

                v = self._vehicle
                current_alt = v.location.global_relative_frame.alt

                # Attempt DO_ORBIT (supported on ArduCopter 4.1+)
                try:
                    msg = v.message_factory.command_long_encode(
                        0, 0,
                        mavutil.mavlink.MAV_CMD_DO_ORBIT,
                        0,
                        radius_m,   # param1: radius
                        speed_ms,   # param2: velocity
                        0,          # param3: yaw behavior (0 = toward center)
                        0,          # param4: reserved
                        lat,        # param5: center latitude
                        lon,        # param6: center longitude
                        current_alt  # param7: altitude
                    )
                    v.send_mavlink(msg)
                    logger.info(f"Orbiting ({lat:.6f}, {lon:.6f}) r={radius_m}m @ {speed_ms}m/s")
                    time.sleep(duration_s)
                    # Return to hover after orbit
                    self._vehicle.mode = VehicleMode("LOITER")
                    return True

                except Exception:
                    # Fallback: computed waypoint circle
                    logger.info("DO_ORBIT not available — using computed waypoints")
                    return self._orbit_waypoints(lat, lon, radius_m, speed_ms, duration_s)

            except Exception as e:
                logger.error(f"Orbit error: {e}")
                return False

    def _orbit_waypoints(
        self, lat: float, lon: float, radius_m: float, speed_ms: float, duration_s: float
    ) -> bool:
        """Fallback orbit via discrete GUIDED mode waypoints around a circle."""
        v = self._vehicle
        current_alt = v.location.global_relative_frame.alt
        circumference = 2 * math.pi * radius_m
        # Choose step count so waypoints are ~2m apart
        n_steps = max(12, int(circumference / 2))
        step_deg = 360.0 / n_steps

        start_time = time.time()
        bearing = 0.0

        while time.time() - start_time < duration_s:
            wp_lat, wp_lon = self._offset_gps(lat, lon, bearing, radius_m)
            target = LocationGlobalRelative(wp_lat, wp_lon, current_alt)
            v.simple_goto(target, airspeed=speed_ms)
            # Yaw toward center
            self._yaw_toward(lat, lon)
            time.sleep(circumference / n_steps / speed_ms)
            bearing = (bearing + step_deg) % 360.0

        return True

    def set_gimbal_angle(self, pitch_deg: int) -> bool:
        """
        Command Tarot T2-2D gimbal tilt via MAVLink DO_MOUNT_CONTROL.

        Args:
            pitch_deg: Tilt angle. 0 = forward, -90 = straight down, positive = up.

        Returns:
            True if command sent successfully
        """
        if not self._connected:
            return False
        with self._lock:
            try:
                if not DRONEKIT_AVAILABLE:
                    logger.info(f"[SIM] Gimbal pitch → {pitch_deg}°")
                    return True

                v = self._vehicle
                msg = v.message_factory.mount_control_encode(
                    0, 0,
                    pitch_deg * 100,  # Pitch in centidegrees
                    0,                # Roll (not used for T2-2D)
                    0,                # Yaw (not used)
                    0,                # Save position
                )
                v.send_mavlink(msg)
                logger.debug(f"Gimbal pitch set to {pitch_deg}°")
                return True
            except Exception as e:
                logger.error(f"Gimbal error: {e}")
                return False

    def drop_payload(self) -> bool:
        """
        Actuate AUX2 servo to release payload, then close after dwell time.
        Servo channel: RC10 = AUX2 on Pixhawk 6C Mini.

        Returns:
            True if servo commands sent successfully
        """
        if not self._connected:
            return False
        with self._lock:
            try:
                if not DRONEKIT_AVAILABLE:
                    logger.info("[SIM] Payload released")
                    return True

                v = self._vehicle
                # AUX2 = channel 10 on Pixhawk 6C Mini
                self._set_servo(10, config.PAYLOAD_SERVO_OPEN_PWM)
                logger.info("Payload servo OPEN")
                time.sleep(config.PAYLOAD_RELEASE_DWELL_S)
                self._set_servo(10, config.PAYLOAD_SERVO_CLOSED_PWM)
                logger.info("Payload servo CLOSED")
                return True
            except Exception as e:
                logger.error(f"Payload drop error: {e}")
                return False

    def _set_servo(self, channel: int, pwm: int) -> None:
        """Send MAVLink DO_SET_SERVO command for a specific RC channel."""
        msg = self._vehicle.message_factory.command_long_encode(
            0, 0,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
            0,
            channel,  # Servo instance (1-indexed)
            pwm,      # PWM value in microseconds
            0, 0, 0, 0, 0
        )
        self._vehicle.send_mavlink(msg)

    # ──────────────────────────────────────────
    # Telemetry
    # ──────────────────────────────────────────

    def get_telemetry(self) -> dict:
        """
        Return current drone state as a dictionary.
        Values come from the continuously-updated telemetry cache
        (no blocking MAVLink read per call).

        Returns:
            dict with keys:
                battery_percent, battery_voltage, altitude_m,
                latitude, longitude, heading_deg, groundspeed_ms,
                mode, armed, gps_fix, distance_to_home_m
        """
        with self._telemetry_lock:
            return dict(self._telemetry_cache)

    def _start_telemetry_thread(self) -> None:
        """Poll vehicle state and update cache at defined interval."""
        def _poll():
            while self._connected:
                try:
                    v = self._vehicle
                    loc = v.location.global_relative_frame
                    home = v.home_location

                    dist_home = 0.0
                    if home:
                        dist_home = self._haversine(
                            loc.lat, loc.lon, home.lat, home.lon
                        )

                    data = {
                        "battery_percent": v.battery.level or 0,
                        "battery_voltage": v.battery.voltage or 0.0,
                        "altitude_m": loc.alt,
                        "latitude": loc.lat,
                        "longitude": loc.lon,
                        "heading_deg": v.heading,
                        "groundspeed_ms": v.groundspeed,
                        "airspeed_ms": v.airspeed,
                        "mode": v.mode.name,
                        "armed": v.armed,
                        "gps_fix": v.gps_0.fix_type,
                        "satellites": v.gps_0.satellites_visible,
                        "distance_to_home_m": dist_home,
                        "ekf_ok": v.ekf_ok,
                        "is_armable": v.is_armable,
                        "last_update": time.time(),
                    }
                    with self._telemetry_lock:
                        self._telemetry_cache = data

                except Exception as e:
                    logger.debug(f"Telemetry poll error: {e}")

                time.sleep(self._telemetry_interval_s)

        self._telemetry_thread = threading.Thread(target=_poll, name="TelemetryPoller", daemon=True)
        self._telemetry_thread.start()

    # ──────────────────────────────────────────
    # Simulation Stubs (when DroneKit unavailable)
    # ──────────────────────────────────────────

    def _start_fake_telemetry(self) -> None:
        """Populate telemetry cache with plausible fake values for testing."""
        self._telemetry_cache = {
            "battery_percent": 85,
            "battery_voltage": 24.2,
            "altitude_m": 0.0,
            "latitude": 34.0522,
            "longitude": -118.2437,
            "heading_deg": 0,
            "groundspeed_ms": 0.0,
            "airspeed_ms": 0.0,
            "mode": "STABILIZE",
            "armed": False,
            "gps_fix": 3,
            "satellites": 12,
            "distance_to_home_m": 0.0,
            "ekf_ok": True,
            "is_armable": True,
            "last_update": time.time(),
        }

    def _update_sim_telemetry(self, **kwargs) -> None:
        """Update simulation telemetry fields."""
        with self._telemetry_lock:
            self._telemetry_cache.update(kwargs)
            self._telemetry_cache["last_update"] = time.time()

    # ──────────────────────────────────────────
    # Utility Methods
    # ──────────────────────────────────────────

    def _wait_for_mode(self, mode_name: str, timeout: float = 10) -> bool:
        """Block until vehicle mode matches, or timeout."""
        deadline = time.time() + timeout
        while self._vehicle.mode.name != mode_name:
            if time.time() > deadline:
                return False
            time.sleep(0.1)
        return True

    def _yaw_toward(self, lat: float, lon: float) -> None:
        """Yaw the vehicle to face a GPS coordinate."""
        try:
            current = self._vehicle.location.global_relative_frame
            bearing = self._bearing(current.lat, current.lon, lat, lon)
            msg = self._vehicle.message_factory.command_long_encode(
                0, 0,
                mavutil.mavlink.MAV_CMD_CONDITION_YAW,
                0,
                bearing,  # Target angle (deg)
                20,       # Yaw rate (deg/s)
                1,        # Direction (1 = clockwise)
                0,        # 0 = absolute angle
                0, 0, 0
            )
            self._vehicle.send_mavlink(msg)
        except Exception as e:
            logger.debug(f"Yaw error: {e}")

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate great-circle distance between two GPS points (meters)."""
        R = 6371000  # Earth radius in meters
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate compass bearing from point 1 to point 2 (degrees, 0=North)."""
        lat1, lat2 = math.radians(lat1), math.radians(lat2)
        dlon = math.radians(lon2 - lon1)
        x = math.sin(dlon) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    @staticmethod
    def _offset_gps(lat: float, lon: float, bearing_deg: float, distance_m: float):
        """
        Return GPS coordinates offset from (lat, lon) by distance_m in direction bearing_deg.
        Used for orbit waypoint computation.
        """
        R = 6371000
        bearing = math.radians(bearing_deg)
        lat1, lon1 = math.radians(lat), math.radians(lon)
        d = distance_m / R

        lat2 = math.asin(math.sin(lat1) * math.cos(d) +
                          math.cos(lat1) * math.sin(d) * math.cos(bearing))
        lon2 = lon1 + math.atan2(
            math.sin(bearing) * math.sin(d) * math.cos(lat1),
            math.cos(d) - math.sin(lat1) * math.sin(lat2)
        )
        return math.degrees(lat2), math.degrees(lon2)
