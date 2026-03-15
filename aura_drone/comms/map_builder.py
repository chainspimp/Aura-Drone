"""
comms/map_builder.py — Geo-Tagged Image Map Builder

Stitches geo-tagged images captured during scout and patrol missions
into an interactive HTML overhead situation map using Folium.
Detection markers are overlaid with tooltips showing threat type and timestamp.
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger("AURA.map_builder")

try:
    import folium
    from folium.plugins import MarkerCluster
    FOLIUM_AVAILABLE = True
except ImportError:
    logger.warning("folium not installed — map building disabled")
    FOLIUM_AVAILABLE = False


# Marker colors by detection class
MARKER_COLORS = {
    "person": "red",
    "car": "orange",
    "truck": "orange",
    "motorcycle": "orange",
    "thermal": "darkred",
    "water": "blue",
    "crops/farmland": "green",
    "vegetation": "lightgreen",
    "backpack": "cadetblue",
    "boat": "darkblue",
    "default": "gray",
}

MARKER_ICONS = {
    "person": "user",
    "car": "car",
    "truck": "truck",
    "thermal": "fire",
    "water": "tint",
    "crops/farmland": "leaf",
    "default": "info-sign",
}


class MapBuilder:
    """
    Interactive HTML situation map from geo-tagged detections and images.

    Generates a Folium map with:
    - Drone flight path (polyline)
    - Detection markers (colored by threat level)
    - Image thumbnails on click (if photos available)
    - Patrol waypoint markers
    - Camp/home position marker

    Output: maps/situation_map_{timestamp}.html
    """

    def __init__(self, home_lat: float = 0.0, home_lon: float = 0.0) -> None:
        self.home_lat = home_lat
        self.home_lon = home_lon
        self._flight_path: list[tuple] = []
        self._detections: list[dict] = []

    # ──────────────────────────────────────────
    # Data Ingestion
    # ──────────────────────────────────────────

    def add_position(self, lat: float, lon: float) -> None:
        """Add a GPS position to the flight path trail."""
        self._flight_path.append((lat, lon))

    def add_detection(self, detection: dict) -> None:
        """
        Add a detection event to the map.

        Args:
            detection: Dict with 'class'/'type', 'gps':{lat,lon}, 'timestamp', etc.
        """
        self._detections.append(detection)

    def load_from_scout_report(self, json_path: str) -> int:
        """
        Load detections from a scout mission JSON file.

        Args:
            json_path: Path to scout_detections_*.json file

        Returns:
            Number of detections loaded
        """
        try:
            with open(json_path) as f:
                detections = json.load(f)
            self._detections.extend(detections)
            logger.info(f"Loaded {len(detections)} detections from {json_path}")
            return len(detections)
        except Exception as e:
            logger.error(f"Failed to load scout report {json_path}: {e}")
            return 0

    # ──────────────────────────────────────────
    # Map Generation
    # ──────────────────────────────────────────

    def build(
        self,
        output_filename: Optional[str] = None,
        title: str = "AURA Situation Map",
    ) -> Optional[str]:
        """
        Generate the interactive HTML map.

        Args:
            output_filename: Output file path (auto-generated if None)
            title: Map title shown in the HTML

        Returns:
            Path to generated HTML file, or None on failure
        """
        if not FOLIUM_AVAILABLE:
            logger.error("folium not available — cannot build map")
            return None

        os.makedirs(config.MAP_OUTPUT_DIR, exist_ok=True)

        if output_filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = os.path.join(config.MAP_OUTPUT_DIR, f"situation_map_{ts}.html")

        # Determine map center
        center_lat, center_lon = self._compute_center()

        # Create base map
        fmap = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=config.MAP_DEFAULT_ZOOM,
            tiles="OpenStreetMap",
        )

        # Add title
        title_html = f'<h3 style="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:1000;background:rgba(0,0,0,0.7);color:white;padding:5px 15px;border-radius:4px;">{title}</h3>'
        fmap.get_root().html.add_child(folium.Element(title_html))

        # Home position marker
        if self.home_lat != 0.0:
            folium.Marker(
                location=[self.home_lat, self.home_lon],
                tooltip="HOME / BASE CAMP",
                icon=folium.Icon(color="green", icon="home"),
            ).add_to(fmap)

        # Flight path
        if len(self._flight_path) >= 2:
            folium.PolyLine(
                locations=self._flight_path,
                color="blue",
                weight=2,
                opacity=0.7,
                tooltip="Flight path",
            ).add_to(fmap)

        # Detection markers (clustered for readability)
        cluster = MarkerCluster(name="Detections").add_to(fmap)

        for det in self._detections:
            gps = det.get("gps", {})
            lat = gps.get("lat", 0)
            lon = gps.get("lon", 0)

            if lat == 0.0 and lon == 0.0:
                continue

            cls = det.get("class", det.get("type", det.get("sensor", "unknown")))
            ts = det.get("timestamp", det.get("datetime", "?"))
            conf = det.get("confidence", det.get("coverage_percent", ""))
            conf_str = f" ({conf:.0%})" if isinstance(conf, float) and conf <= 1.0 else \
                       f" ({conf}%)" if isinstance(conf, (int, float)) else ""

            color = MARKER_COLORS.get(cls, MARKER_COLORS["default"])
            icon_name = MARKER_ICONS.get(cls, MARKER_ICONS["default"])

            tooltip = f"{cls.upper()}{conf_str} @ {ts}"
            popup_html = (
                f"<b>{cls.upper()}</b><br>"
                f"Confidence: {conf_str}<br>"
                f"GPS: {lat:.5f}, {lon:.5f}<br>"
                f"Time: {ts}"
            )

            # Add photo thumbnail if available
            photo = det.get("photo_path", "")
            if photo and os.path.exists(photo):
                popup_html += f'<br><img src="{photo}" width="200">'

            folium.Marker(
                location=[lat, lon],
                tooltip=tooltip,
                popup=folium.Popup(popup_html, max_width=250),
                icon=folium.Icon(color=color, icon=icon_name, prefix="glyphicon"),
            ).add_to(cluster)

        # Layer control
        folium.LayerControl().add_to(fmap)

        # Legend
        legend_html = self._build_legend_html()
        fmap.get_root().html.add_child(folium.Element(legend_html))

        # Save
        fmap.save(output_filename)
        logger.info(f"Situation map saved: {output_filename}")
        return output_filename

    def _compute_center(self) -> tuple[float, float]:
        """Compute map center from available data points."""
        lats, lons = [], []

        if self.home_lat != 0.0:
            lats.append(self.home_lat)
            lons.append(self.home_lon)

        for lat, lon in self._flight_path:
            lats.append(lat)
            lons.append(lon)

        for det in self._detections:
            gps = det.get("gps", {})
            if gps.get("lat") and gps.get("lon"):
                lats.append(gps["lat"])
                lons.append(gps["lon"])

        if lats and lons:
            return sum(lats) / len(lats), sum(lons) / len(lons)

        return 34.0522, -118.2437  # Default: LA (arbitrary fallback)

    @staticmethod
    def _build_legend_html() -> str:
        """Generate HTML legend for the map."""
        items = [
            ("red", "Person (hostile/unknown)"),
            ("orange", "Vehicle"),
            ("darkred", "Thermal contact"),
            ("blue", "Water source"),
            ("green", "Vegetation / Crops"),
            ("cadetblue", "Supply cache"),
        ]
        rows = "".join(
            f'<div><span style="background:{c};display:inline-block;width:12px;height:12px;margin-right:5px;border-radius:50%;"></span>{label}</div>'
            for c, label in items
        )
        return (
            f'<div style="position:fixed;bottom:30px;left:10px;z-index:1000;'
            f'background:rgba(0,0,0,0.75);color:white;padding:10px;border-radius:6px;'
            f'font-size:12px;">'
            f"<b>AURA Legend</b><br>{rows}</div>"
        )
