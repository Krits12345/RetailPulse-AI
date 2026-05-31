"""
Zone classification using polygon definitions from store_layout.json.
Uses ray-casting (point-in-polygon) to assign a person's centroid to a zone.
Also determines entry/exit direction based on vertical position change at the threshold line.
"""
import json
from pathlib import Path
from typing import Optional


def _point_in_polygon(px: float, py: float, polygon: list[list[float]]) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


class ZoneClassifier:
    def __init__(self, store_layout: dict, camera_id: str):
        self.camera_id = camera_id
        self.zones: list[dict] = []
        self.entry_line_y: Optional[int] = None

        for zone in store_layout.get("zones", []):
            if camera_id in zone.get("camera_ids", []):
                self.zones.append(zone)
                if zone["zone_id"] == "ENTRY":
                    self.entry_line_y = zone.get("entry_line_y", 100)

        # track last y-position per track_id for direction determination
        self._prev_y: dict[int, float] = {}

    def get_zone(self, cx: float, cy: float) -> Optional[str]:
        """Return the zone_id whose polygon contains (cx, cy), or None."""
        for zone in self.zones:
            if _point_in_polygon(cx, cy, zone["polygon"]):
                return zone["zone_id"]
        return None

    def get_sku_zone(self, zone_id: str) -> Optional[str]:
        for z in self.zones:
            if z["zone_id"] == zone_id:
                return z.get("sku_zone")
        return None

    def get_direction(self, track_id: int, cx: float, cy: float) -> Optional[str]:
        """
        For entry cameras only: compare current y to previous y relative to entry line.
        Moving downward (increasing y) past the line = ENTRY.
        Moving upward (decreasing y) past the line = EXIT.
        Returns None if not an entry camera or direction is unclear.
        """
        if self.entry_line_y is None:
            return None

        prev_y = self._prev_y.get(track_id)
        self._prev_y[track_id] = cy

        if prev_y is None:
            return None

        if prev_y < self.entry_line_y <= cy:
            return "ENTRY"
        if prev_y > self.entry_line_y >= cy:
            return "EXIT"
        return None


def load_store_layout(store_id: str, layout_path: str = "data/store_layout.json") -> dict:
    """Load and return the layout dict for a specific store."""
    with open(layout_path) as f:
        data = json.load(f)
    for store in data["stores"]:
        if store["store_id"] == store_id:
            return store
    raise ValueError(f"Store '{store_id}' not found in {layout_path}")
