"""DEADGRID: a small, data-driven city containment strategy game."""

from __future__ import annotations

import json
import heapq
import math
import os
import queue
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pygame
import numpy as np
import requests


WIDTH, HEIGHT = 1360, 820
NATIVE_WIDTH, NATIVE_HEIGHT = 1920, 1080
WORLD_W, WORLD_H = 1000.0, 700.0
MAP_RECT = pygame.Rect(276, 118, 796, 636)
AREA_MAP_RECT = pygame.Rect(220, 112, 920, 594)


def _scenario_path() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).with_name("deadgrid_scenarios.json")
    if sys.platform == "win32":
        config_root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        config_root = Path.home() / "Library" / "Application Support"
    else:
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_root / "Deadgrid" / "deadgrid_scenarios.json"


def _asset_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


SCENARIO_PATH = _scenario_path()
LOGO_PATH = _asset_path("DeadGrid_ Gritty Urban Target Logo.png")

INK = (11, 17, 18)
PANEL = (18, 27, 28)
PANEL_HI = (25, 38, 39)
LINE = (43, 61, 59)
TEXT = (226, 235, 228)
MUTED = (128, 148, 140)
LIME = (186, 234, 112)
CYAN = (103, 204, 185)
AMBER = (232, 173, 96)
RED = (233, 103, 94)
MAP_BASE = (188, 201, 178)
MAP_ROAD = (234, 234, 218)
MAP_BUILDING = (143, 158, 145)
GRID_W, GRID_H = 60, 42

UNITS = {
    "SNIPER": {"cost": 220, "range": 205, "damage": 27, "rate": 1.05, "color": (105, 193, 178), "description": "LONG RANGE / HIGH DAMAGE"},
    "SENTRY": {"cost": 300, "range": 140, "damage": 12, "rate": 0.45, "color": (183, 216, 111), "description": "FAST AUTO-FIRE / CONTROL"},
    "MORTAR": {"cost": 440, "range": 275, "damage": 38, "rate": 2.3, "color": (232, 173, 96), "description": "AREA BLAST / SLOW RELOAD"},
    "RIFLE SQUAD": {"cost": 350, "range": 125, "damage": 18, "rate": 0.7, "color": (126, 168, 209), "description": "RELIABLE CLOSE DEFENSE"},
    "BARRICADE": {"cost": 80, "range": 0, "damage": 0, "rate": 0, "color": (210, 182, 127), "description": "SLOWS THE CROWD"},
    "EVAC STATION": {"cost": 175, "range": 64, "damage": 0, "rate": 0, "color": (115, 189, 211), "description": "STREET EXTRACTION / 14 AGENTS SEC"},
    "SAFE ZONE": {"cost": 420, "range": 128, "damage": 0, "rate": 0, "color": (106, 190, 150), "description": "SHELTER / CAPACITY 6,000 PEOPLE"},
    "SUPPLY DROP": {"cost": 100, "range": 0, "damage": 0, "rate": 0, "color": (213, 195, 127), "description": "AIRDROP / +$400 AFTER 8 SEC"},
}

PRESETS = ["New York, USA", "Paris, France", "Los Angeles, USA", "Venice, Italy"]


@dataclass
class CityMap:
    name: str
    center: tuple[float, float] | None = None
    place_bounds: tuple[float, float, float, float] | None = None
    map_aspect_ratio: float = 1.0
    map_width_km: float = 0.0
    map_height_km: float = 0.0
    radius_km: float = 0.8
    population: int = 0
    base_population: int = 0
    population_source: str = "UNAVAILABLE"
    roads: list[list[tuple[float, float]]] = field(default_factory=list)
    road_types: list[str] = field(default_factory=list)
    road_widths_m: list[float] = field(default_factory=list)
    buildings: list[list[tuple[float, float]]] = field(default_factory=list)
    building_weights: list[float] = field(default_factory=list)
    parks: list[list[tuple[float, float]]] = field(default_factory=list)
    source: str = "SCHEMATIC"
    note: str = ""
    water: list[list[tuple[float, float]]] = field(default_factory=list)
    waterways: list[list[tuple[float, float]]] = field(default_factory=list)
    building_types: list[str] = field(default_factory=list)
    query_bounds: tuple[float, float, float, float] | None = None
    place_population: int = 0
    preview_radius_km: float = 0.0


def _normalized(lon: float, lat: float, bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    west, south, east, north = bounds
    return ((lon - west) / (east - west), (north - lat) / (north - south))


def _fit_rect(rect: pygame.Rect, aspect_ratio: float) -> pygame.Rect:
    """Return the largest centered rectangle with the requested width/height ratio."""
    aspect_ratio = max(0.01, aspect_ratio)
    if rect.w / rect.h > aspect_ratio:
        width = round(rect.h * aspect_ratio)
        return pygame.Rect(rect.centerx - width // 2, rect.y, width, rect.h)
    height = round(rect.w / aspect_ratio)
    return pygame.Rect(rect.x, rect.centery - height // 2, rect.w, height)


def _stitch_polygon_rings(paths: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join relation outer-member ways into closed polygon rings."""
    pending = [path[:] for path in paths if len(path) >= 2]
    rings: list[list[tuple[float, float]]] = []
    while pending:
        ring = pending.pop()
        changed = True
        while changed and len(ring) >= 2 and ring[0] != ring[-1]:
            changed = False
            for index, path in enumerate(pending):
                if ring[-1] == path[0]:
                    ring.extend(path[1:])
                elif ring[-1] == path[-1]:
                    ring.extend(reversed(path[:-1]))
                elif ring[0] == path[-1]:
                    ring = path[:-1] + ring
                elif ring[0] == path[0]:
                    ring = list(reversed(path[1:])) + ring
                else:
                    continue
                pending.pop(index)
                changed = True
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def schematic_map(name: str, seed: int = 12) -> CityMap:
    """Create a deterministic street plan when a live map cannot be reached."""
    rng = random.Random(seed)
    result = CityMap(name=name, source="SCHEMATIC", note="LIVE MAP UNAVAILABLE · GENERATED CITY GRID")
    xs = [0.07, 0.19, 0.32, 0.45, 0.59, 0.74, 0.88]
    ys = [0.08, 0.19, 0.31, 0.44, 0.58, 0.73, 0.88]
    for y in ys:
        result.roads.append([(x, y) for x in [0.0, *xs, 1.0]])
        result.road_types.append("residential")
    for x in xs:
        result.roads.append([(x, y) for y in [0.0, *ys, 1.0]])
        result.road_types.append("residential")
    result.parks.extend([
        [(0.04, 0.53), (0.21, 0.53), (0.21, 0.70), (0.04, 0.70)],
        [(0.70, 0.12), (0.87, 0.12), (0.87, 0.27), (0.70, 0.27)],
    ])
    for row in range(len(ys) - 1):
        for col in range(len(xs) - 1):
            left, right = xs[col] + 0.026, xs[col + 1] - 0.025
            top, bottom = ys[row] + 0.023, ys[row + 1] - 0.023
            if left > 0.19 and left < 0.21 and top > 0.53 and top < 0.70:
                continue
            if 0.70 < left < 0.87 and 0.12 < top < 0.27:
                continue
            for _ in range(rng.randint(2, 4)):
                bx = rng.uniform(left, max(left + 0.01, right - 0.08))
                by = rng.uniform(top, max(top + 0.01, bottom - 0.08))
                bw = rng.uniform(0.025, min(0.075, max(0.03, right - bx)))
                bh = rng.uniform(0.023, min(0.065, max(0.03, bottom - by)))
                result.buildings.append([(bx, by), (bx + bw, by), (bx + bw, by + bh), (bx, by + bh)])
                result.building_weights.append(rng.choice((0.3, 0.5, 1.2, 2.2, 3.8)))
                result.building_types.append(rng.choice(("residential", "commercial", "civic", "industrial")))
    return result


def _building_population_weight(tags: dict[str, Any]) -> float:
    """Approximate occupied residential capacity from OSM/Overture attributes."""
    category = str(tags.get("building") or tags.get("subtype") or tags.get("class") or "yes").lower()
    weights = {
        "apartments": 10.0,
        "residential": 8.0,
        "dormitory": 7.0,
        "house": 3.0,
        "terrace": 4.5,
        "detached": 2.0,
        "semidetached_house": 2.5,
        "houseboat": 0.7,
        "hotel": 1.2,
        "commercial": 0.16,
        "retail": 0.12,
        "office": 0.10,
        "industrial": 0.025,
        "warehouse": 0.02,
        "garage": 0.025,
        "yes": 0.55,
    }
    levels = _parse_population(tags.get("building:levels") or tags.get("num_floors") or 1)
    level_factor = min(4.0, max(0.55, levels ** 0.55)) if levels else 0.55
    return weights.get(category, 0.45) * level_factor


def _building_category(value: Any) -> str:
    category = str(value or "").lower()
    if category in {"industrial", "warehouse", "factory", "manufacture", "hangar"}:
        return "industrial"
    if category in {"commercial", "retail", "office", "hotel", "supermarket"}:
        return "commercial"
    if category in {"civic", "government", "hospital", "school", "university", "church", "public"}:
        return "civic"
    return "residential"


def _geometry_rings(geometry: Any) -> list[list[tuple[float, float]]]:
    """Read polygon rings from Shapely geometries without requiring Shapely."""
    if geometry is None or getattr(geometry, "is_empty", True):
        return []
    parts = [geometry] if geometry.geom_type == "Polygon" else list(getattr(geometry, "geoms", []))
    rings = []
    for part in parts:
        exterior = getattr(part, "exterior", None)
        if exterior is not None:
            ring = [(float(x), float(y)) for x, y in exterior.coords]
            if len(ring) >= 4:
                rings.append(ring)
    return rings


def _add_overture_buildings(city: CityMap, bounds: tuple[float, float, float, float]) -> bool:
    """Load public Overture building footprints when the optional client is installed."""
    try:
        from overturemaps import core  # type: ignore[import-not-found]

        frame = core.geodataframe("building", bbox=bounds)
        added = 0
        for _, feature in frame.iterrows():
            geometry = feature.geometry
            for ring in _geometry_rings(geometry):
                normalized = [_normalized(lon, lat, bounds) for lon, lat in ring]
                if all(-0.05 <= x <= 1.05 and -0.05 <= y <= 1.05 for x, y in normalized):
                    city.buildings.append(normalized)
                    city.building_weights.append(_building_population_weight(feature.to_dict()))
                    city.building_types.append(_building_category(feature.get("subtype", feature.get("class", "residential"))))
                    added += 1
                    if added >= 1100:
                        return added > 0
        return added > 0
    except Exception:
        return False


def _area_km2(bounds: tuple[float, float, float, float]) -> float:
    west, south, east, north = bounds
    center_lat = (south + north) / 2
    width = (east - west) * 111.32 * math.cos(math.radians(center_lat))
    height = (north - south) * 110.57
    return max(0.01, abs(width * height))


def _parse_population(value: Any) -> int:
    parts = str(value or "").lower().replace(",", "").split()
    if not parts:
        return 0
    try:
        count = float(parts[0])
    except ValueError:
        return 0
    if len(parts) > 1 and parts[1].startswith("million"):
        count *= 1_000_000
    elif len(parts) > 1 and parts[1].startswith("thousand"):
        count *= 1_000
    return max(0, round(count))


def _road_width_m(tags: dict[str, Any]) -> float:
    try:
        tagged_width = float(str(tags.get("width", "")).lower().replace("m", "").strip())
    except ValueError:
        tagged_width = 0.0
    if tagged_width > 0:
        return min(60.0, max(0.5, tagged_width))
    highway = str(tags.get("highway", "residential"))
    defaults = {
        "motorway": 18.0, "motorway_link": 10.0, "trunk": 15.0, "trunk_link": 9.0,
        "primary": 12.0, "primary_link": 8.0, "secondary": 10.0, "secondary_link": 7.0,
        "tertiary": 8.0, "tertiary_link": 6.0, "residential": 6.0, "living_street": 5.0,
        "service": 4.5, "unclassified": 5.0, "pedestrian": 4.0, "cycleway": 2.5,
        "footway": 2.0, "path": 1.8, "track": 3.0,
    }
    try:
        lanes = max(0, int(tags.get("lanes", 0)))
    except (TypeError, ValueError):
        lanes = 0
    lane_width = 3.2 if highway in {"motorway", "trunk", "primary", "secondary"} else 2.8
    return min(60.0, max(defaults.get(highway, 5.0), lanes * lane_width))


def load_city(place: str, use_overture: bool = True, radius_km: float = 1.5, preview_radius_km: float | None = None) -> CityMap:
    """Geocode a place and fetch its street/building geometry from open map data."""
    headers = {"User-Agent": "DeadgridStrategy/1.0 (open map data game)"}
    fallback = schematic_map(place, sum(ord(char) for char in place))
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place, "format": "jsonv2", "limit": 1, "extratags": 1},
            headers=headers,
            timeout=14,
        )
        response.raise_for_status()
        matches = response.json()
        if not matches:
            raise RuntimeError("No matching place was found.")
        match = matches[0]
        lat, lon = float(match["lat"]), float(match["lon"])
        display_name = str(match.get("name") or place)
        place_bbox = match.get("boundingbox")
        if place_bbox and len(place_bbox) == 4:
            place_bounds = (float(place_bbox[2]), float(place_bbox[0]), float(place_bbox[3]), float(place_bbox[1]))
        else:
            place_bounds = (lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)
        radius_km = max(0.35, min(2.0, radius_km))
        preview_radius_km = max(radius_km, min(5.0, preview_radius_km or radius_km))
        radius_lat = preview_radius_km / 110.57
        radius_lon = min(0.08, radius_lat / max(0.22, math.cos(math.radians(lat))))
        bounds = (lon - radius_lon, lat - radius_lat, lon + radius_lon, lat + radius_lat)
        city_population = _parse_population(match.get("extratags", {}).get("population"))
    except Exception as error:
        fallback.note = f"LIVE MAP UNAVAILABLE · {str(error)[:74].upper()}"
        return fallback

    west, south, east, north = bounds
    query = (
        "[out:json][timeout:30];("
        f'way["highway"]({south},{west},{north},{east});'
        f'way["building"]({south},{west},{north},{east});'
        f'way["leisure"="park"]({south},{west},{north},{east});'
        f'way["natural"="water"]({south},{west},{north},{east});'
        f'way["natural"="coastline"]({south},{west},{north},{east});'
        f'way["waterway"]({south},{west},{north},{east});'
        f'relation["natural"="water"]({south},{west},{north},{east});'
        f'relation["waterway"="riverbank"]({south},{west},{north},{east});'
        ");out geom qt;"
    )
    map_width_km = (east - west) * 111.32 * math.cos(math.radians(lat))
    map_height_km = (north - south) * 110.57
    city = CityMap(
        name=display_name,
        center=(lat, lon),
        place_bounds=place_bounds,
        map_aspect_ratio=map_width_km / max(map_height_km, 0.01),
        map_width_km=map_width_km,
        map_height_km=map_height_km,
        radius_km=radius_km,
        query_bounds=bounds,
        place_population=city_population,
        preview_radius_km=preview_radius_km,
    )
    if city_population > 0:
        selected_area_km2 = math.pi * radius_km ** 2
        area_share = min(1.0, selected_area_km2 / _area_km2(place_bounds))
        city.population = max(1, round(city_population * area_share))
        city.population_source = f"OSM POPULATION TAG · {city_population:,} PLACE TOTAL · AREA SHARE ESTIMATE"
    elements = None
    osm_error: Exception | None = None
    for endpoint in (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ):
        try:
            response = requests.post(endpoint, data={"data": query}, headers=headers, timeout=42)
            response.raise_for_status()
            elements = response.json().get("elements", [])
            break
        except Exception as error:
            osm_error = error
    for element in elements or []:
        tags = element.get("tags", {})
        if element.get("type") == "relation":
            member_paths = []
            for member in element.get("members", []):
                if member.get("role", "outer") != "outer":
                    continue
                geometry = member.get("geometry") or []
                path = [_normalized(float(node["lon"]), float(node["lat"]), bounds) for node in geometry]
                if len(path) >= 2:
                    member_paths.append(path)
            city.water.extend(_stitch_polygon_rings(member_paths))
            continue

        geometry = element.get("geometry") or []
        points = [_normalized(float(node["lon"]), float(node["lat"]), bounds) for node in geometry]
        if len(points) < 2:
            continue
        is_closed = len(points) >= 4 and points[0] == points[-1]
        if tags.get("natural") == "water" or tags.get("waterway") == "riverbank":
            if is_closed:
                city.water.append(points)
            else:
                city.waterways.append(points)
        elif tags.get("natural") == "coastline" or "waterway" in tags:
            city.waterways.append(points)
        elif "highway" in tags:
            city.roads.append(points)
            city.road_widths_m.append(_road_width_m(tags))
            highway = str(tags.get("highway", "residential"))
            road_class = highway
            if tags.get("bridge") not in (None, "no", "false", "0"):
                road_class += "|bridge"
            oneway = str(tags.get("oneway", "")).lower()
            if oneway in {"yes", "true", "1"}:
                road_class += "|oneway"
            elif oneway == "-1":
                road_class += "|oneway_reverse"
            try:
                width = float(str(tags.get("width", "")).replace("m", "").strip())
            except ValueError:
                width = 0
            try:
                lanes = int(tags.get("lanes", 0))
            except (TypeError, ValueError):
                lanes = 0
            if (width and width <= 5.5) or lanes == 1 or highway in {"residential", "living_street", "service", "unclassified", "footway", "path", "track", "pedestrian"}:
                road_class += "|narrow"
            city.road_types.append(road_class)
        elif "building" in tags and len(points) >= 4:
            city.buildings.append(points)
            city.building_weights.append(_building_population_weight(tags))
            city.building_types.append(_building_category(tags.get("building")))
        elif tags.get("leisure") == "park" and len(points) >= 4:
            city.parks.append(points)

    overture_loaded = use_overture and _add_overture_buildings(city, bounds)
    if city.roads:
        city.source = "OSM + OVERTURE" if overture_loaded else "OPENSTREETMAP"
    elif overture_loaded:
        city.source = "OVERTURE MAPS"
        city.note = "OPENSTREETMAP STREETS UNAVAILABLE · REAL OVERTURE BUILDINGS"
    else:
        city.source = "SCHEMATIC"
        city.note = f"LIVE STREETS UNAVAILABLE · {str(osm_error or 'NO STREET DATA')[:58].upper()}"
    if not city.roads:
        city.roads = fallback.roads
        city.road_types = fallback.road_types.copy()
    if not city.water:
        city.water = fallback.water.copy()
    if not city.buildings:
        city.buildings = fallback.buildings
        city.building_weights = fallback.building_weights.copy()
        city.building_types = fallback.building_types.copy()
        if not city.note:
            city.note = "LIVE BUILDINGS UNAVAILABLE · GENERATED FOOTPRINTS"
    if not city.population:
        footprint_population = max(500, len(city.buildings) * 18)
        city.population = footprint_population
        city.population_source = "BUILDING FOOTPRINT ESTIMATE · NO PLACE POPULATION TAG"
    if not city.base_population:
        city.base_population = city.population
    return city


def _build_road_graph(city: CityMap) -> tuple[np.ndarray, list[list[tuple[int, float, bool, bool]]], list[tuple[int, int, float, float, float, bool, bool]]]:
    """Turn connected mapped street polylines into a waypoint graph."""
    penalties = {
        "motorway": 2.4,
        "trunk": 1.8,
        "primary": 1.45,
        "secondary": 1.25,
        "tertiary": 1.1,
        "residential": 0.9,
        "living_street": 0.75,
        "service": 0.85,
        "footway": 0.8,
        "path": 0.82,
        "pedestrian": 0.72,
        "steps": 1.3,
    }
    node_ids: dict[tuple[int, int], int] = {}
    nodes: list[tuple[float, float]] = []
    edges: dict[tuple[int, int], tuple[float, bool, bool, bool]] = {}

    def add_node(x: float, y: float) -> int:
        x, y = max(0.0, min(WORLD_W, x)), max(0.0, min(WORLD_H, y))
        key = (round(x / 2), round(y / 2))
        if key not in node_ids:
            node_ids[key] = len(nodes)
            nodes.append((x, y))
        return node_ids[key]

    for road_index, road in enumerate(city.roads):
        if len(road) < 2:
            continue
        road_type = city.road_types[road_index] if road_index < len(city.road_types) else "residential"
        kind, *attributes = road_type.split("|")
        factor = penalties.get(kind, 1.0)
        is_bridge = "bridge" in attributes
        is_narrow = "narrow" in attributes
        is_oneway = "oneway" in attributes or "oneway_reverse" in attributes
        reverse_oneway = "oneway_reverse" in attributes
        if is_narrow:
            factor *= 1.12
        previous_id = None
        previous_point = None
        for normalized in road:
            point = (normalized[0] * WORLD_W, normalized[1] * WORLD_H)
            if previous_point is not None:
                dx, dy = point[0] - previous_point[0], point[1] - previous_point[1]
                length = math.hypot(dx, dy)
                segment_count = max(1, math.ceil(length / 32))
                for segment in range(1, segment_count + 1):
                    fraction = segment / segment_count
                    current = (previous_point[0] + dx * fraction, previous_point[1] + dy * fraction)
                    current_id = add_node(*current)
                    if previous_id is not None and current_id != previous_id:
                        first = current_id if reverse_oneway else previous_id
                        second = previous_id if reverse_oneway else current_id
                        edge_key = (first, second) if is_oneway else (min(first, second), max(first, second))
                        edge_cost = length / segment_count * factor
                        existing = edges.get(edge_key)
                        if existing is None or edge_cost < existing[0]:
                            edges[edge_key] = (edge_cost, is_bridge, is_narrow, is_oneway)
                    previous_id = current_id
            else:
                previous_id = add_node(*point)
            previous_point = point

    adjacency: list[list[tuple[int, float, bool, bool]]] = [[] for _ in nodes]
    graph_edges = []
    for (first, second), (cost, is_bridge, is_narrow, is_oneway) in edges.items():
        if is_oneway:
            adjacency[second].append((first, cost, is_bridge, is_narrow))
        else:
            adjacency[first].append((second, cost, is_bridge, is_narrow))
            adjacency[second].append((first, cost, is_bridge, is_narrow))
        midpoint_x = (nodes[first][0] + nodes[second][0]) / 2
        midpoint_y = (nodes[first][1] + nodes[second][1]) / 2
        graph_edges.append((first, second, cost, midpoint_x, midpoint_y, is_bridge, is_narrow))
    return np.asarray(nodes, dtype=np.float32).reshape((-1, 2)), adjacency, graph_edges


class MapWorker:
    """Keep geocoding and public map queries off the pygame event loop."""

    def __init__(self) -> None:
        self.results: queue.Queue[CityMap] = queue.Queue()

    def request(self, place: str, use_overture: bool, radius_km: float, preview_radius_km: float | None = None) -> None:
        thread = threading.Thread(target=self._work, args=(place, use_overture, radius_km, preview_radius_km), daemon=True)
        thread.start()

    def _work(self, place: str, use_overture: bool, radius_km: float, preview_radius_km: float | None = None) -> None:
        self.results.put(load_city(place, use_overture, radius_km, preview_radius_km))


class Deadgrid:
    def __init__(self) -> None:
        pygame.init()
        pygame.display.set_caption("DEADGRID // City Containment")
        self.screen = pygame.display.set_mode((NATIVE_WIDTH, NATIVE_HEIGHT), pygame.RESIZABLE)
        self.logo = pygame.image.load(str(LOGO_PATH)).convert()
        self.logo.set_colorkey((0, 0, 0))
        self.menu_logo = pygame.transform.smoothscale(self.logo, (612, 204))
        self.menu_logo.set_colorkey((0, 0, 0))
        window_icon = pygame.Surface((64, 64))
        window_icon.fill(INK)
        icon_wordmark = pygame.transform.smoothscale(self.logo, (60, 20))
        icon_wordmark.set_colorkey((0, 0, 0))
        window_icon.blit(icon_wordmark, (2, 22))
        pygame.display.set_icon(window_icon)
        self.canvas = pygame.Surface((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("dejavusans", 14)
        self.bold = pygame.font.SysFont("dejavusans", 14, bold=True)
        self.small = pygame.font.SysFont("dejavusans", 11, bold=True)
        self.large = pygame.font.SysFont("dejavusans", 34, bold=True)
        self.worker = MapWorker()
        self.preview_map = schematic_map("CITY GRID", 42)
        self.city = self.preview_map
        self.screen_name = "menu"
        self.place_input = "New York, USA"
        self.input_active = False
        self.use_overture = True
        self.radius_km = 1.5
        self.initial_infection_percent = 1.0
        self.population_density = 1.0
        self.zombie_behavior = "BALANCED"
        self.starting_resources = 1500
        self.patient_zero = (0.5, 0.5)
        self.area_selection = (0.5, 0.5)
        self.area_zoom = 1.0
        self.area_view_center = (0.5, 0.5)
        self.area_map_surface: pygame.Surface | None = None
        self.area_map_city: CityMap | None = None
        self.area_pan_drag = False
        self.area_pan_last = (0, 0)
        self.fullscreen = False
        self.resolution_1080 = True
        self.windowed_size = (WIDTH, HEIGHT)
        self.city_surface_cache: dict[tuple[Any, ...], pygame.Surface] = {}
        self.emergency_policy = "EVACUATE"
        self.supply_deliveries = 0
        self.infection_casualties = 0
        self.strike_casualties = 0
        self.route_failure_agents = 0
        self.route_failure_population = 0
        self.route_failure_history: list[str] = []
        self.route_failure_locations: list[tuple[float, float, int]] = []
        self.route_failure_signature: tuple[tuple[int, int], ...] = ()
        self.containment_timeline: list[dict[str, Any]] = []
        self.collapse_cause = ""
        self.scenario_name = "New scenario"
        self.scenario_name_active = False
        self.saved_scenarios: list[dict[str, Any]] = []
        self.selected_scenario_index = -1
        self.loading = False
        self.message = "SELECT A CITY · CONFIGURE THE WORLD"
        self.message_timer = 0.0
        self.mouse = (0, 0)
        self.hit_targets: dict[str, pygame.Rect] = {}
        self.selected_unit = "SNIPER"
        self.target_mode: str | None = None
        self.moving_unit: dict[str, Any] | None = None
        self.barricade_drag_start: tuple[float, float] | None = None
        self.units: list[dict[str, Any]] = []
        self.civilians: dict[str, np.ndarray] = {}
        self.zombies: dict[str, np.ndarray] = {}
        self.population_agents = 0
        self.people_per_agent = 1
        self.represented_population = 0
        self.initial_infected_agents = 0
        self.initial_infected_population = 0
        self.evacuated_agents = 0
        self.infection_origin = (0.5, 0.5)
        self.road_nodes = np.empty((0, 2), dtype=np.float32)
        self.road_node_cell: list[int] = []
        self.road_adjacency: list[list[tuple[int, float, bool, bool]]] = []
        self.road_edges: list[tuple[int, int, float, float, float, bool, bool]] = []
        self.road_edge_midpoints = np.empty((0, 2), dtype=np.float32)
        self._penalized_edge_keys: set[tuple[int, int]] = set()
        self._barricade_signature: tuple[tuple[float, float], ...] | None = None
        self._barricade_slow_grid = np.ones((GRID_H, GRID_W), dtype=np.float32)
        self.road_next_nodes = np.empty(0, dtype=np.int32)
        self.road_next_narrow = np.zeros(0, dtype=bool)
        self.road_goal_positions: dict[int, tuple[float, float]] = {}
        self.evac_next_nodes = np.empty(0, dtype=np.int32)
        self.evac_next_narrow = np.zeros(0, dtype=bool)
        self.evac_goal_positions: dict[int, tuple[float, float]] = {}
        self.evac_goal_kinds: dict[int, str] = {}
        self.road_snap_grid = np.zeros((GRID_H, GRID_W), dtype=np.int32)
        self.road_node_bins: dict[tuple[int, int], list[int]] = {}
        self.route_refresh = 0.0
        self.evac_route_refresh = 0.0
        self.intel_modes = {"risk": False, "horde": False, "exposed": False, "survivors": False}
        self.intel_grids = {key: np.zeros((GRID_H, GRID_W), dtype=np.float32) for key in self.intel_modes}
        self.intel_layers: dict[str, pygame.Surface] = {}
        self.intel_refresh = 0.0
        self.effects: list[dict[str, Any]] = []
        self.budget = 1500
        self.rescued = 0
        self.casualties = 0
        self._eliminated_population = 0
        self.infected_count = 0
        self._eliminated = 0
        self.simulation_time = 0.0
        self.simulation_status = "Setup"
        self.end_reason = ""
        self.auto_collapse = True
        self.phase_time = 0.0
        self.gunship = 0.0
        self.gunship_target = (500.0, 350.0)
        self._load_scenarios()
        self.alive = True

    def _scenario_data(self) -> dict[str, Any]:
        name = self.scenario_name.strip()
        if not name or name == "New scenario":
            name = f"{self.place_input} · {self.radius_km:.2f} km"
        return {
            "name": name,
            "place": self.place_input,
            "radius_km": self.radius_km,
            "population_density": self.population_density,
            "initial_infection_percent": self.initial_infection_percent,
            "patient_zero": list(self.patient_zero),
            "zombie_behavior": self.zombie_behavior,
            "emergency_policy": self.emergency_policy,
            "starting_resources": self.starting_resources,
            "auto_collapse": self.auto_collapse,
            "use_overture": self.use_overture,
        }

    def _load_scenarios(self) -> None:
        try:
            data = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
            self.saved_scenarios = data if isinstance(data, list) else []
        except (OSError, ValueError, TypeError):
            self.saved_scenarios = []
        self.selected_scenario_index = len(self.saved_scenarios) - 1

    def _save_scenario(self) -> None:
        scenario = self._scenario_data()
        self.saved_scenarios = [item for item in self.saved_scenarios if item.get("name") != scenario["name"]]
        self.saved_scenarios.append(scenario)
        self.saved_scenarios = self.saved_scenarios[-12:]
        self.selected_scenario_index = len(self.saved_scenarios) - 1
        try:
            SCENARIO_PATH.parent.mkdir(parents=True, exist_ok=True)
            SCENARIO_PATH.write_text(json.dumps(self.saved_scenarios, indent=2), encoding="utf-8")
            self._notify(f"SCENARIO SAVED · {scenario['name'].upper()}")
        except OSError:
            self._notify("SCENARIO SAVE FAILED · CHECK WRITE PERMISSIONS")

    def _load_selected_scenario(self) -> None:
        if not self.saved_scenarios or not 0 <= self.selected_scenario_index < len(self.saved_scenarios):
            self._notify("NO SAVED SCENARIOS")
            return
        scenario = self.saved_scenarios[self.selected_scenario_index]
        self.scenario_name = str(scenario.get("name", "New scenario"))
        self.place_input = str(scenario.get("place", self.place_input))
        self.radius_km = max(0.35, min(2.0, float(scenario.get("radius_km", 1.5))))
        self.population_density = max(0.25, min(2.5, float(scenario.get("population_density", 1.0))))
        self.initial_infection_percent = float(scenario.get("initial_infection_percent", 1.0))
        self.patient_zero = tuple(scenario.get("patient_zero", (0.5, 0.5)))
        self.zombie_behavior = str(scenario.get("zombie_behavior", "BALANCED"))
        self.emergency_policy = str(scenario.get("emergency_policy", "EVACUATE"))
        self.starting_resources = int(scenario.get("starting_resources", 1500))
        self.auto_collapse = bool(scenario.get("auto_collapse", True))
        self.use_overture = bool(scenario.get("use_overture", True))
        self._request_map(self.place_input)

    def _notify(self, text: str, duration: float = 3.0) -> None:
        self.message, self.message_timer = text, duration

    def _request_map(self, place: str | None = None) -> None:
        if self.loading:
            return
        place = (place if place is not None else self.place_input).strip()
        if not place:
            self._notify("ENTER A CITY OR PLACE NAME")
            return
        self.place_input = place
        self.loading = True
        self.input_active = False
        self._notify("GEOCODING PLACE · FETCHING STREET AND BUILDING DATA", 60.0)
        preview_radius = max(3.5, self.radius_km + 1.5)
        self.worker.request(place, self.use_overture, self.radius_km, preview_radius)

    def _limit_area_selection(self) -> None:
        bounds = self.city.query_bounds
        if not bounds:
            preview_radius = max(self.city.preview_radius_km, self.radius_km + 1.5, 3.5)
            margin_y = min(0.49, self.radius_km / (2 * preview_radius))
            margin_x = margin_y
            self.area_selection = (
                max(margin_x, min(1.0 - margin_x, self.area_selection[0])),
                max(margin_y, min(1.0 - margin_y, self.area_selection[1])),
            )
            return
        west, south, east, north = bounds
        latitude = north - self.area_selection[1] * (north - south)
        radius_lat = self.radius_km / 110.57
        radius_lon = radius_lat / max(0.22, math.cos(math.radians(latitude)))
        margin_x = min(0.49, radius_lon / (east - west))
        margin_y = min(0.49, radius_lat / (north - south))
        self.area_selection = (
            max(margin_x, min(1.0 - margin_x, self.area_selection[0])),
            max(margin_y, min(1.0 - margin_y, self.area_selection[1])),
        )

    def _clip_line(self, points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
        segments: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        for start, end in zip(points, points[1:]):
            x0, y0 = start
            dx, dy = end[0] - x0, end[1] - y0
            lower, upper = 0.0, 1.0
            for p, q in ((-dx, x0), (dx, 1.0 - x0), (-dy, y0), (dy, 1.0 - y0)):
                if abs(p) < 1e-12:
                    if q < 0:
                        lower, upper = 1.0, 0.0
                        break
                    continue
                ratio = q / p
                if p < 0:
                    lower = max(lower, ratio)
                else:
                    upper = min(upper, ratio)
            if lower > upper:
                if len(current) > 1:
                    segments.append(current)
                current = []
                continue
            clipped_start = (x0 + lower * dx, y0 + lower * dy)
            clipped_end = (x0 + upper * dx, y0 + upper * dy)
            if current and math.dist(current[-1], clipped_start) < 1e-6:
                current.append(clipped_end)
            else:
                if len(current) > 1:
                    segments.append(current)
                current = [clipped_start, clipped_end]
        if len(current) > 1:
            segments.append(current)
        return segments

    def _clip_polygon(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        clipped = points
        for axis, boundary, keep_greater in ((0, 0.0, True), (0, 1.0, False), (1, 0.0, True), (1, 1.0, False)):
            if not clipped:
                return []
            output = []
            previous = clipped[-1]
            previous_inside = previous[axis] >= boundary if keep_greater else previous[axis] <= boundary
            for current in clipped:
                current_inside = current[axis] >= boundary if keep_greater else current[axis] <= boundary
                if current_inside != previous_inside:
                    delta = current[axis] - previous[axis]
                    fraction = 0.0 if abs(delta) < 1e-12 else (boundary - previous[axis]) / delta
                    other_axis = 1 - axis
                    intersection = [0.0, 0.0]
                    intersection[axis] = boundary
                    intersection[other_axis] = previous[other_axis] + fraction * (current[other_axis] - previous[other_axis])
                    output.append((intersection[0], intersection[1]))
                if current_inside:
                    output.append(current)
                previous, previous_inside = current, current_inside
            clipped = output
        return clipped

    def _select_city_area(self, city: CityMap, center: tuple[float, float], radius_km: float) -> CityMap:
        geographic = city.query_bounds is not None
        if geographic:
            west, south, east, north = city.query_bounds
            latitude = north - center[1] * (north - south)
            longitude = west + center[0] * (east - west)
            radius_lat = radius_km / 110.57
            radius_lon = radius_lat / max(0.22, math.cos(math.radians(latitude)))
            area_bounds = (
                max(west, longitude - radius_lon),
                max(south, latitude - radius_lat),
                min(east, longitude + radius_lon),
                min(north, latitude + radius_lat),
            )
            area_west, area_south, area_east, area_north = area_bounds
            if area_east <= area_west or area_north <= area_south:
                return city

            def to_area(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
                geographic_points = [(west + x * (east - west), north - y * (north - south)) for x, y in points]
                return [((lon - area_west) / (area_east - area_west), (area_north - lat) / (area_north - area_south)) for lon, lat in geographic_points]

            geographic_center = (latitude, longitude)
            map_width_km = (area_east - area_west) * 111.32 * math.cos(math.radians(latitude))
            map_height_km = (area_north - area_south) * 110.57
        else:
            preview_radius = max(city.preview_radius_km, radius_km + 1.5, 3.5)
            half_y = min(0.49, radius_km / (2 * preview_radius))
            half_x = half_y
            area_bounds = (center[0] - half_x, center[1] - half_y, center[0] + half_x, center[1] + half_y)
            area_west, area_south, area_east, area_north = area_bounds
            if area_east <= area_west or area_north <= area_south:
                return city

            def to_area(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
                return [((x - area_west) / (area_east - area_west), (y - area_south) / (area_north - area_south)) for x, y in points]

            geographic_center = city.center
            map_width_km = map_height_km = radius_km * 2

        def project_and_clip(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
            return self._clip_polygon(to_area(points))

        selected = CityMap(
            name=city.name,
            center=geographic_center,
            place_bounds=city.place_bounds,
            map_aspect_ratio=map_width_km / max(map_height_km, 0.01),
            map_width_km=map_width_km,
            map_height_km=map_height_km,
            radius_km=radius_km,
            population_source=city.population_source,
            source=city.source,
            note=city.note,
            query_bounds=area_bounds if geographic else None,
            place_population=city.place_population,
            preview_radius_km=radius_km,
        )
        for index, road in enumerate(city.roads):
            for segment in self._clip_line(to_area(road)):
                selected.roads.append(segment)
                selected.road_types.append(city.road_types[index] if index < len(city.road_types) else "residential")
                if index < len(city.road_widths_m):
                    selected.road_widths_m.append(city.road_widths_m[index])
        for index, polygon in enumerate(city.buildings):
            clipped = project_and_clip(polygon)
            if len(clipped) >= 3:
                selected.buildings.append(clipped)
                selected.building_weights.append(city.building_weights[index] if index < len(city.building_weights) else 1.0)
                selected.building_types.append(city.building_types[index] if index < len(city.building_types) else "residential")
        for polygons, destination in ((city.parks, selected.parks), (city.water, selected.water)):
            for polygon in polygons:
                clipped = project_and_clip(polygon)
                if len(clipped) >= 3:
                    destination.append(clipped)
        for line in city.waterways:
            selected.waterways.extend(self._clip_line(to_area(line)))

        if geographic and city.place_population and city.place_bounds:
            area_share = min(1.0, math.pi * radius_km ** 2 / _area_km2(city.place_bounds))
            selected.population = max(1, round(city.place_population * area_share))
            selected.population_source = f"OSM POPULATION TAG · {city.place_population:,} PLACE TOTAL · AREA SHARE ESTIMATE"
        else:
            selected.population = max(500, len(selected.buildings) * 18)
            selected.population_source = "BUILDING FOOTPRINT ESTIMATE · NO OSM POPULATION TAG"
        selected.base_population = selected.population
        return selected

    def _begin_operation(self, city: CityMap) -> None:
        self.city = city
        self.city_surface_cache.clear()
        self.intel_grids = {key: np.zeros((GRID_H, GRID_W), dtype=np.float32) for key in self.intel_modes}
        self.intel_layers = {}
        self.intel_refresh = 0.0
        self._prepare_road_network(city)
        self.screen_name = "deployment"
        self.selected_unit = "SNIPER"
        self.target_mode = None
        self.moving_unit = None
        self.barricade_drag_start = None
        self.units = []
        self.effects = []
        self.budget = self.starting_resources
        self.rescued = self.casualties = self.infected_count = 0
        self.simulation_status = "Deployment"
        self.end_reason = ""
        self._eliminated = 0
        self._eliminated_population = 0
        self.evacuated_agents = 0
        self.simulation_time = self.phase_time = self.gunship = 0.0
        rng = random.Random(int(time.time()) ^ sum(ord(c) for c in city.name))
        if not city.population:
            city.population = max(500, len(city.buildings) * 18)
            city.population_source = "BUILDING FOOTPRINT ESTIMATE · NO OSM POPULATION TAG"
        population = max(1, round((city.base_population or city.population) * self.population_density))
        city.base_population = city.base_population or city.population
        city.population = population
        self.population_agents = min(50_000, population)
        self.people_per_agent = (population + self.population_agents - 1) // self.population_agents
        self.represented_population = population
        initial_zombie_count = max(1, round(self.population_agents * self.initial_infection_percent / 100))
        initial_zombie_count = min(self.population_agents - 1, initial_zombie_count)
        agent_population = np.full(self.population_agents, population // self.population_agents, dtype=np.int64)
        agent_population[:population % self.population_agents] += 1
        agent_rng = np.random.default_rng(rng.randrange(2**32))
        agent_rng.shuffle(agent_population)
        self.initial_infected_agents = initial_zombie_count
        self.initial_infected_population = int(agent_population[:initial_zombie_count].sum())
        self.casualties = self.initial_infected_population
        self.infection_casualties = self.initial_infected_population
        self.strike_casualties = 0
        self.supply_deliveries = 0
        self.route_failure_agents = 0
        self.route_failure_population = 0
        self.route_failure_history = []
        self.route_failure_locations = []
        self.route_failure_signature = ()
        self.containment_timeline = []
        self.collapse_cause = ""
        civilian_count = self.population_agents - initial_zombie_count
        if city.buildings:
            polygons = [np.asarray(polygon, dtype=np.float32) for polygon in city.buildings if len(polygon) >= 3]
            if polygons:
                centers = np.asarray([polygon.mean(axis=0) for polygon in polygons], dtype=np.float32)
                extents = np.asarray([np.ptp(polygon, axis=0) for polygon in polygons], dtype=np.float32)
                weights = np.asarray(city.building_weights[:len(polygons)], dtype=np.float64)
                if len(weights) != len(polygons):
                    weights = np.ones(len(polygons), dtype=np.float64)
                footprint_area = np.maximum(0.00001, extents[:, 0] * extents[:, 1])
                capacity = footprint_area * np.maximum(weights, 0.001)
                probabilities = capacity / capacity.sum()
                selected = agent_rng.choice(len(polygons), civilian_count, p=probabilities)
                jitter = agent_rng.uniform(-0.46, 0.46, size=(civilian_count, 2))
                positions = centers[selected] + extents[selected] * jitter
                person_x = positions[:, 0] * WORLD_W
                person_y = positions[:, 1] * WORLD_H
            else:
                person_x = agent_rng.uniform(0.03, 0.97, civilian_count) * WORLD_W
                person_y = agent_rng.uniform(0.03, 0.97, civilian_count) * WORLD_H
        else:
            person_x = agent_rng.uniform(0.03, 0.97, civilian_count) * WORLD_W
            person_y = agent_rng.uniform(0.03, 0.97, civilian_count) * WORLD_H
        self.civilians = {
            "x": np.clip(person_x, 8, WORLD_W - 8).astype(np.float32),
            "y": np.clip(person_y, 8, WORLD_H - 8).astype(np.float32),
            "exposure": np.zeros(civilian_count, dtype=np.float32),
            "incubation": np.zeros(civilian_count, dtype=np.float32),
            "status": np.zeros(civilian_count, dtype=np.uint8),
            "susceptibility": agent_rng.uniform(0.72, 1.28, civilian_count).astype(np.float32),
            "wander": agent_rng.uniform(0.0, 6.0, civilian_count).astype(np.float32),
            "dx": agent_rng.uniform(-1.0, 1.0, civilian_count).astype(np.float32),
            "dy": agent_rng.uniform(-1.0, 1.0, civilian_count).astype(np.float32),
            "evacuating": np.zeros(civilian_count, dtype=bool),
            "safe": np.zeros(civilian_count, dtype=bool),
            "route_node": np.empty(civilian_count, dtype=np.int32),
            "route_narrow": np.zeros(civilian_count, dtype=bool),
            "population": agent_population[initial_zombie_count:],
        }
        self.civilians["route_node"] = self._snap_agents_to_roads(self.civilians["x"], self.civilians["y"])
        population_cells_x = np.clip((self.civilians["x"] * (GRID_W / WORLD_W)).astype(np.int32), 0, GRID_W - 1)
        population_cells_y = np.clip((self.civilians["y"] * (GRID_H / WORLD_H)).astype(np.int32), 0, GRID_H - 1)
        occupancy = np.bincount(population_cells_y * GRID_W + population_cells_x, minlength=GRID_W * GRID_H).reshape(GRID_H, GRID_W)
        self._rebuild_road_routes(occupancy)
        self.route_refresh = 2.0
        self.infection_origin = self.patient_zero
        angles = agent_rng.uniform(0, math.tau, initial_zombie_count)
        radii = np.sqrt(agent_rng.random(initial_zombie_count)) * 30
        origin_x, origin_y = self.infection_origin
        self.zombies = {
            "x": (WORLD_W * origin_x + np.cos(angles) * radii).astype(np.float32),
            "y": (WORLD_H * origin_y + np.sin(angles) * radii).astype(np.float32),
            "hp": np.full(initial_zombie_count, 40, dtype=np.float32),
            "population": agent_population[:initial_zombie_count],
        }
        self.zombies["route_node"] = self._snap_agents_to_roads(self.zombies["x"], self.zombies["y"])
        self.zombies["route_narrow"] = np.zeros(initial_zombie_count, dtype=bool)
        self.screen_name = "deployment"
        note = city.note or f"{city.source} DATA READY"
        self._notify(f"{city.name.upper()} · {note}", 5.0)
        self._record_containment_event(
            "Outbreak begins",
            f"{self.initial_infected_population:,} residents infected",
            "outbreak",
            [(self.patient_zero[0], self.patient_zero[1], self.initial_infected_population)],
        )

    def _map_content_rect(self) -> pygame.Rect:
        return _fit_rect(MAP_RECT, self.city.map_aspect_ratio)

    def _area_content_rect(self) -> pygame.Rect:
        return _fit_rect(AREA_MAP_RECT, self.city.map_aspect_ratio)

    def _map_point(self, nx: float, ny: float) -> tuple[int, int]:
        rect = self._map_content_rect()
        return int(rect.x + nx * rect.w), int(rect.y + ny * rect.h)

    def _world_point(self, point: tuple[int, int]) -> tuple[float, float]:
        rect = self._map_content_rect()
        return ((point[0] - rect.x) / rect.w * WORLD_W, (point[1] - rect.y) / rect.h * WORLD_H)

    def _prepare_road_network(self, city: CityMap) -> None:
        self.road_nodes, self.road_adjacency, self.road_edges = _build_road_graph(city)
        if not len(self.road_nodes):
            fallback = schematic_map(city.name)
            self.road_nodes, self.road_adjacency, self.road_edges = _build_road_graph(fallback)
        node_cell_x = np.clip((self.road_nodes[:, 0] * (GRID_W / WORLD_W)).astype(np.int32), 0, GRID_W - 1)
        node_cell_y = np.clip((self.road_nodes[:, 1] * (GRID_H / WORLD_H)).astype(np.int32), 0, GRID_H - 1)
        self.road_node_cell = (node_cell_y * GRID_W + node_cell_x).tolist()
        self.road_edge_midpoints = np.asarray(
            [(edge[3], edge[4]) for edge in self.road_edges], dtype=np.float32
        ).reshape((-1, 2))
        self._penalized_edge_keys = set()
        self._barricade_signature = None
        self._barricade_slow_grid = np.ones((GRID_H, GRID_W), dtype=np.float32)
        self.road_node_bins = {}
        bucket_size = 64.0
        for node_id, (x, y) in enumerate(self.road_nodes):
            bucket = (int(x // bucket_size), int(y // bucket_size))
            self.road_node_bins.setdefault(bucket, []).append(node_id)
        self.road_snap_grid = np.empty((GRID_H, GRID_W), dtype=np.int32)
        for grid_y in range(GRID_H):
            for grid_x in range(GRID_W):
                x = (grid_x + 0.5) * WORLD_W / GRID_W
                y = (grid_y + 0.5) * WORLD_H / GRID_H
                bucket_x, bucket_y = int(x // bucket_size), int(y // bucket_size)
                candidates: list[int] = []
                for radius in range(0, 12):
                    candidates.extend(
                        node_id
                        for by in range(bucket_y - radius, bucket_y + radius + 1)
                        for bx in range(bucket_x - radius, bucket_x + radius + 1)
                        if radius == 0 or max(abs(bx - bucket_x), abs(by - bucket_y)) == radius
                        for node_id in self.road_node_bins.get((bx, by), ())
                    )
                    if candidates and radius >= 1:
                        break
                candidate_array = np.asarray(candidates, dtype=np.int32)
                distance = (self.road_nodes[candidate_array, 0] - x) ** 2 + (self.road_nodes[candidate_array, 1] - y) ** 2
                self.road_snap_grid[grid_y, grid_x] = int(candidate_array[np.argmin(distance)])
        self.road_next_nodes = np.full(len(self.road_nodes), -1, dtype=np.int32)
        self.road_next_narrow = np.zeros(len(self.road_nodes), dtype=bool)
        self.road_goal_positions = {}
        self.route_refresh = 0.0
        self.evac_next_nodes = np.full(len(self.road_nodes), -1, dtype=np.int32)
        self.evac_next_narrow = np.zeros(len(self.road_nodes), dtype=bool)
        self.evac_goal_positions = {}
        self.evac_goal_kinds = {}
        self.evac_route_refresh = 0.0

    def _snap_agents_to_roads(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        cell_x = np.clip((x * (GRID_W / WORLD_W)).astype(np.int32), 0, GRID_W - 1)
        cell_y = np.clip((y * (GRID_H / WORLD_H)).astype(np.int32), 0, GRID_H - 1)
        return self.road_snap_grid[cell_y, cell_x].copy()

    def _refresh_barricade_caches(self) -> None:
        signature = tuple(
            (float(unit["x"]), float(unit["y"]))
            for unit in self.units
            if unit["kind"] == "BARRICADE"
        )
        if signature == self._barricade_signature:
            return
        self._barricade_signature = signature
        self._penalized_edge_keys = set()
        self._barricade_slow_grid = np.ones((GRID_H, GRID_W), dtype=np.float32)
        if not signature:
            return

        grid_y, grid_x = np.ogrid[:GRID_H, :GRID_W]
        cell_x = (grid_x + 0.5) * WORLD_W / GRID_W
        cell_y = (grid_y + 0.5) * WORLD_H / GRID_H
        for barricade_x, barricade_y in signature:
            if len(self.road_edge_midpoints):
                dx = self.road_edge_midpoints[:, 0] - barricade_x
                dy = self.road_edge_midpoints[:, 1] - barricade_y
                for edge_index in np.flatnonzero(dx * dx + dy * dy < 48 ** 2):
                    first, second = self.road_edges[int(edge_index)][:2]
                    self._penalized_edge_keys.add((min(first, second), max(first, second)))

            near = (cell_x - barricade_x) ** 2 + (cell_y - barricade_y) ** 2 < 38 ** 2
            self._barricade_slow_grid[near] *= 0.28

    def _rebuild_road_routes(self, occupancy: np.ndarray) -> None:
        if not self.road_adjacency:
            return
        self._refresh_barricade_caches()
        flat = occupancy.ravel()
        candidates = np.argsort(flat)[::-1]
        seeds: list[int] = []
        selected_cells: list[tuple[int, int]] = []
        goal_positions: dict[int, tuple[float, float]] = {}
        for cell in candidates:
            if flat[cell] == 0:
                break
            grid_y, grid_x = divmod(int(cell), GRID_W)
            if any((grid_x - old_x) ** 2 + (grid_y - old_y) ** 2 < 16 for old_x, old_y in selected_cells):
                continue
            node = int(self.road_snap_grid[grid_y, grid_x])
            if node not in seeds:
                seeds.append(node)
                selected_cells.append((grid_x, grid_y))
                goal_positions[node] = ((grid_x + 0.5) * WORLD_W / GRID_W, (grid_y + 0.5) * WORLD_H / GRID_H)
            if len(seeds) >= 16:
                break
        if not seeds:
            node = int(self.road_snap_grid[GRID_H // 2, GRID_W // 2])
            seeds.append(node)
            goal_positions[node] = ((GRID_W // 2 + 0.5) * WORLD_W / GRID_W, (GRID_H // 2 + 0.5) * WORLD_H / GRID_H)

        distance = np.full(len(self.road_nodes), np.inf, dtype=np.float64)
        next_node = np.full(len(self.road_nodes), -1, dtype=np.int32)
        next_narrow = np.zeros(len(self.road_nodes), dtype=bool)
        pending: list[tuple[float, int]] = []
        for node in seeds:
            distance[node] = 0.0
            heapq.heappush(pending, (0.0, node))
        while pending:
            current_cost, node = heapq.heappop(pending)
            if current_cost != distance[node]:
                continue
            for neighbor, edge_cost, _, is_narrow in self.road_adjacency[node]:
                edge_key = (min(node, neighbor), max(node, neighbor))
                if edge_key in self._penalized_edge_keys:
                    edge_cost *= 22.0
                candidate_cost = current_cost + edge_cost
                if candidate_cost < distance[neighbor]:
                    distance[neighbor] = candidate_cost
                    next_node[neighbor] = node
                    next_narrow[neighbor] = is_narrow
                    heapq.heappush(pending, (candidate_cost, neighbor))
        self.road_next_nodes = next_node
        self.road_next_narrow = next_narrow
        self.road_goal_positions = goal_positions

    def _rebuild_evac_routes(self, horde_pressure: np.ndarray) -> None:
        self._refresh_barricade_caches()
        if self.emergency_policy == "EVACUATE":
            destinations = [unit for unit in self.units if unit["kind"] == "EVAC STATION"]
        elif self.emergency_policy == "SHELTER":
            destinations = [unit for unit in self.units if unit["kind"] == "SAFE ZONE" and unit.get("safe_agents", 0) < unit.get("capacity_agents", 0)]
        else:
            destinations = []
        if not destinations or not self.road_adjacency:
            self.evac_next_nodes = np.full(len(self.road_nodes), -1, dtype=np.int32)
            self.evac_next_narrow = np.zeros(len(self.road_nodes), dtype=bool)
            self.evac_goal_positions = {}
            self.evac_goal_kinds = {}
            failed_mask = self.civilians["evacuating"] & (self.civilians["status"] == 0) if self.emergency_policy in ("EVACUATE", "SHELTER") else np.zeros(len(self.civilians["x"]), dtype=bool)
            self._record_route_failures(failed_mask)
            return
        goals: dict[int, tuple[float, float]] = {}
        goal_kinds: dict[int, str] = {}
        for destination in destinations:
            node = int(destination.get("route_node", self._snap_agents_to_roads(np.array([destination["x"]]), np.array([destination["y"]]))[0]))
            destination["route_node"] = node
            goals[node] = (destination["x"], destination["y"])
            goal_kinds[node] = destination["kind"]
        distance = np.full(len(self.road_nodes), np.inf, dtype=np.float64)
        next_node = np.full(len(self.road_nodes), -1, dtype=np.int32)
        next_narrow = np.zeros(len(self.road_nodes), dtype=bool)
        pending: list[tuple[float, int]] = []
        pressure_flat = horde_pressure.ravel().tolist()
        for node in goals:
            distance[node] = 0.0
            heapq.heappush(pending, (0.0, node))
        while pending:
            current_cost, node = heapq.heappop(pending)
            if current_cost != distance[node]:
                continue
            for neighbor, edge_cost, _, is_narrow in self.road_adjacency[node]:
                edge_key = (min(node, neighbor), max(node, neighbor))
                if edge_key in self._penalized_edge_keys:
                    edge_cost *= 22.0
                edge_cost *= 1.0 + min(16.0, pressure_flat[self.road_node_cell[neighbor]] * 0.7)
                candidate_cost = current_cost + edge_cost
                if candidate_cost < distance[neighbor]:
                    distance[neighbor] = candidate_cost
                    next_node[neighbor] = node
                    next_narrow[neighbor] = is_narrow
                    heapq.heappush(pending, (candidate_cost, neighbor))
        self.evac_next_nodes = next_node
        self.evac_next_narrow = next_narrow
        self.evac_goal_positions = goals
        self.evac_goal_kinds = goal_kinds
        active = self.civilians["evacuating"] & (self.civilians["status"] == 0)
        current_nodes = self.civilians["route_node"][active]
        reachable = (next_node[current_nodes] >= 0) | np.isin(current_nodes, np.fromiter(goals, dtype=np.int32))
        failed_mask = np.zeros(len(self.civilians["x"]), dtype=bool)
        if self.emergency_policy in ("EVACUATE", "SHELTER"):
            failed_mask[np.flatnonzero(active)] = ~reachable
        self._record_route_failures(failed_mask)

    def _record_route_failures(self, failed_mask: np.ndarray) -> None:
        failed_indices = np.flatnonzero(failed_mask)
        self.route_failure_agents = len(failed_indices)
        self.route_failure_population = int(self.civilians["population"][failed_indices].sum())
        self.route_failure_locations = []
        if not len(failed_indices):
            if self.route_failure_signature:
                self._record_containment_event("Routes restored", "No residents currently stranded", "recovered")
            self.route_failure_signature = ()
            return
        cell_x = np.clip((self.civilians["x"][failed_indices] * (GRID_W / WORLD_W)).astype(np.int32), 0, GRID_W - 1)
        cell_y = np.clip((self.civilians["y"][failed_indices] * (GRID_H / WORLD_H)).astype(np.int32), 0, GRID_H - 1)
        counts = np.bincount(
            cell_y * GRID_W + cell_x,
            weights=self.civilians["population"][failed_indices],
            minlength=GRID_W * GRID_H,
        ).round().astype(np.int64)
        hot_cells = np.argsort(counts)[::-1]
        for cell in hot_cells:
            if counts[cell] == 0 or len(self.route_failure_locations) == 3:
                break
            grid_y, grid_x = divmod(int(cell), GRID_W)
            location = ((grid_x + 0.5) / GRID_W, (grid_y + 0.5) / GRID_H, int(counts[cell]))
            self.route_failure_locations.append(location)
        signature = tuple((round(x * 100), round(y * 100)) for x, y, _ in self.route_failure_locations)
        if signature != self.route_failure_signature:
            self.route_failure_history.append(f"{self.route_failure_population:,} residents have no route; inspect the highlighted blocks, one-way links, or barricades.")
            self.route_failure_history = self.route_failure_history[-5:]
            self.route_failure_signature = signature
            self._record_containment_event(
                "Evacuation routes failed",
                f"{self.route_failure_population:,} residents stranded",
                "route_failure",
                self.route_failure_locations.copy(),
            )

    def _record_containment_event(
        self,
        label: str,
        detail: str,
        kind: str,
        locations: list[tuple[float, float, int]] | None = None,
    ) -> None:
        self.containment_timeline.append({
            "time": self.simulation_time,
            "label": label,
            "detail": detail,
            "kind": kind,
            "locations": locations or [],
        })
        self.containment_timeline = self.containment_timeline[-40:]

    def _population_hotspots(self) -> list[tuple[float, float, int]]:
        if not len(self.zombies.get("x", ())):
            return []
        cell_x = np.clip((self.zombies["x"] * (GRID_W / WORLD_W)).astype(np.int32), 0, GRID_W - 1)
        cell_y = np.clip((self.zombies["y"] * (GRID_H / WORLD_H)).astype(np.int32), 0, GRID_H - 1)
        weights = self.zombies.get("population", np.ones(len(cell_x), dtype=np.int64))
        counts = np.bincount(cell_y * GRID_W + cell_x, weights=weights, minlength=GRID_W * GRID_H).round().astype(np.int64)
        locations = []
        for cell in np.argsort(counts)[::-1]:
            if counts[cell] == 0 or len(locations) == 3:
                break
            grid_y, grid_x = divmod(int(cell), GRID_W)
            locations.append(((grid_x + 0.5) / GRID_W, (grid_y + 0.5) / GRID_H, int(counts[cell])))
        return locations

    def _move_evacuating(self, dt: float) -> None:
        crowd = self.civilians
        if not self.evac_goal_positions or not len(crowd["x"]):
            return
        route_nodes = crowd["route_node"]
        next_nodes = self.evac_next_nodes[route_nodes]
        goal_nodes = np.fromiter(self.evac_goal_positions, dtype=np.int32)
        reachable = (next_nodes >= 0) | np.isin(route_nodes, goal_nodes)
        active_route = crowd["evacuating"] & (crowd["status"] == 0) & reachable
        if not np.any(active_route):
            return
        x, y = crowd["x"], crowd["y"]
        waypoint_x, waypoint_y = self.road_nodes[route_nodes, 0], self.road_nodes[route_nodes, 1]
        at_waypoint = active_route & ((x - waypoint_x) ** 2 + (y - waypoint_y) ** 2 < 12 ** 2)
        advance = at_waypoint & (next_nodes >= 0)
        crowd["route_narrow"][advance] = self.evac_next_narrow[route_nodes[advance]]
        route_nodes[advance] = next_nodes[advance]
        waypoint_x, waypoint_y = self.road_nodes[route_nodes, 0], self.road_nodes[route_nodes, 1]
        destination_x, destination_y = waypoint_x.copy(), waypoint_y.copy()
        terminal = self.evac_next_nodes[route_nodes] < 0
        for goal_node, (goal_x, goal_y) in self.evac_goal_positions.items():
            approach_station = active_route & terminal & (route_nodes == goal_node)
            destination_x[approach_station] = goal_x
            destination_y[approach_station] = goal_y
        dx, dy = destination_x - x, destination_y - y
        distance = np.maximum(1, np.hypot(dx, dy))
        speed = np.full(len(x), 28.0, dtype=np.float32)
        speed[crowd["route_narrow"] & active_route] *= 0.72
        x[active_route] += dx[active_route] / distance[active_route] * speed[active_route] * dt
        y[active_route] += dy[active_route] / distance[active_route] * speed[active_route] * dt
        x[active_route] = np.clip(x[active_route], 8, WORLD_W - 8)
        y[active_route] = np.clip(y[active_route], 8, WORLD_H - 8)

        removed = np.zeros(len(x), dtype=bool)
        for zone in (unit for unit in self.units if unit["kind"] == "SAFE ZONE"):
            goal_node = int(zone["route_node"])
            goal_route = active_route & terminal & (route_nodes == goal_node) & (self.evac_goal_kinds.get(goal_node) == "SAFE ZONE")
            inside = (x - zone["x"]) ** 2 + (y - zone["y"]) ** 2 <= zone["range"] ** 2
            candidates = np.flatnonzero(goal_route & inside)
            capacity = max(1, int(zone.get("capacity_agents", 1)))
            available = max(0, capacity - int(zone.get("safe_agents", 0)))
            if available and len(candidates):
                sheltered = candidates[:available]
                crowd["safe"][sheltered] = True
                crowd["evacuating"][sheltered] = False
                zone["safe_agents"] = int(zone.get("safe_agents", 0)) + len(sheltered)

        for station in (unit for unit in self.units if unit["kind"] == "EVAC STATION"):
            station["evac_progress"] = min(14.0, station.get("evac_progress", 0.0) + 14.0 * dt)
            quota = int(station["evac_progress"])
            if not quota:
                continue
            distance_squared = (x - station["x"]) ** 2 + (y - station["y"]) ** 2
            reached = active_route & (~removed) & (~crowd["safe"]) & (distance_squared <= UNITS["EVAC STATION"]["range"] ** 2)
            indices = np.flatnonzero(reached)[:quota]
            if len(indices):
                removed[indices] = True
                station["evac_progress"] -= len(indices)
                self.evacuated_agents += len(indices)
                self.rescued += int(crowd["population"][indices].sum())
        if np.any(removed):
            self.civilians = {key: values[~removed] for key, values in crowd.items()}

    def _update_safe_zones(self, crowd: dict[str, np.ndarray]) -> None:
        crowd["safe"].fill(False)
        for zone in (unit for unit in self.units if unit["kind"] == "SAFE ZONE"):
            dx, dy = crowd["x"] - zone["x"], crowd["y"] - zone["y"]
            inside = (crowd["status"] == 0) & (dx * dx + dy * dy <= zone["range"] ** 2)
            agents = np.flatnonzero(inside)
            capacity = max(1, int(zone.get("capacity_agents", 1)))
            selected = agents[:capacity]
            crowd["safe"][selected] = True
            zone["safe_agents"] = len(selected)

    def _set_emergency_policy(self, policy: str) -> None:
        self.emergency_policy = policy
        susceptible = (self.civilians["status"] == 0) & (~self.civilians["safe"])
        has_destination = (policy == "EVACUATE" and any(unit["kind"] == "EVAC STATION" for unit in self.units)) or (policy == "SHELTER" and any(unit["kind"] == "SAFE ZONE" for unit in self.units))
        self.civilians["evacuating"] = susceptible if has_destination else np.zeros(len(susceptible), dtype=bool)
        self.civilians["route_node"] = self._snap_agents_to_roads(self.civilians["x"], self.civilians["y"])
        self.civilians["route_narrow"].fill(False)
        self.evac_route_refresh = 0.0
        self._notify(f"EMERGENCY POLICY · {policy}")

    def _refresh_intelligence(self, grids: dict[str, np.ndarray]) -> None:
        self.intel_grids = grids
        self.intel_layers = {}
        palette = {"risk": (230, 76, 64), "horde": (217, 122, 57), "exposed": (242, 187, 79), "survivors": (71, 185, 191)}
        for mode, enabled in self.intel_modes.items():
            if not enabled:
                continue
            values = grids[mode]
            nonzero = values[values > 0]
            if not len(nonzero):
                continue
            scale = max(1.0, float(np.percentile(nonzero, 90)))
            alpha_grid = (np.clip(values / scale, 0, 1) * 185).astype(np.uint8)
            layer = pygame.Surface((GRID_W, GRID_H), pygame.SRCALPHA)
            rgb = pygame.surfarray.pixels3d(layer)
            rgb[:] = np.asarray(palette[mode], dtype=np.uint8)
            del rgb
            alpha = pygame.surfarray.pixels_alpha(layer)
            alpha[:] = alpha_grid.T
            del alpha
            self.intel_layers[mode] = pygame.transform.scale(layer, self._map_content_rect().size)

    def _draw_population(self, surface: pygame.Surface) -> None:
        civilians = self.civilians
        if not len(civilians["x"]) and not len(self.zombies["x"]):
            return
        pixels = pygame.surfarray.pixels3d(surface)
        map_rect = self._map_content_rect()
        if len(civilians["x"]):
            pixels_x = np.clip((civilians["x"] * (map_rect.w / WORLD_W)).astype(np.int32), 0, map_rect.w - 1) + map_rect.x
            pixels_y = np.clip((civilians["y"] * (map_rect.h / WORLD_H)).astype(np.int32), 0, map_rect.h - 1) + map_rect.y
            colors = np.empty((len(pixels_x), 3), dtype=np.uint8)
            colors[:] = (250, 250, 242)
            colors[civilians["status"] == 1] = LIME
            pixels[pixels_x, pixels_y] = colors
            pixels[np.minimum(pixels_x + 1, map_rect.right - 1), pixels_y] = colors
        if len(self.zombies["x"]):
            pixels_x = np.clip((self.zombies["x"] * (map_rect.w / WORLD_W)).astype(np.int32), 0, map_rect.w - 1) + map_rect.x
            pixels_y = np.clip((self.zombies["y"] * (map_rect.h / WORLD_H)).astype(np.int32), 0, map_rect.h - 1) + map_rect.y
            pixels[pixels_x, pixels_y] = LIME
            pixels[np.minimum(pixels_x + 1, map_rect.right - 1), pixels_y] = LIME
        del pixels

    def _draw_area_map(self, surface: pygame.Surface) -> None:
        if self.area_map_surface is None or self.area_map_city is not self.city:
            scale = 2
            size = (AREA_MAP_RECT.w * scale, AREA_MAP_RECT.h * scale)
            self.area_map_surface = pygame.Surface(size)
            self._draw_city_uncached(self.area_map_surface, self.area_map_surface.get_rect(), self.city)
            self.area_map_city = self.city

        source = self.area_map_surface
        source_content = _fit_rect(source.get_rect(), self.city.map_aspect_ratio)
        target_content = self._area_content_rect()
        source_width = max(1, round(source_content.w / self.area_zoom))
        source_height = max(1, round(source_content.h / self.area_zoom))
        source_rect = pygame.Rect(
            round(source_content.x + self.area_view_center[0] * source_content.w - source_width / 2),
            round(source_content.y + self.area_view_center[1] * source_content.h - source_height / 2),
            source_width,
            source_height,
        )
        source_rect.clamp_ip(source_content)
        view = pygame.transform.scale(source.subsurface(source_rect), target_content.size)
        surface.blit(view, target_content.topleft)

    def _draw_city(self, surface: pygame.Surface, rect: pygame.Rect, city: CityMap, zoom: float = 1.0, view_center: tuple[float, float] = (0.5, 0.5)) -> None:
        key = (id(city), rect.w, rect.h, round(zoom, 2), round(view_center[0], 4), round(view_center[1], 4))
        cached = self.city_surface_cache.get(key)
        if cached is None:
            cached = pygame.Surface(rect.size)
            self._draw_city_uncached(cached, cached.get_rect(), city, zoom, view_center)
            if len(self.city_surface_cache) >= 8:
                self.city_surface_cache.pop(next(iter(self.city_surface_cache)))
            self.city_surface_cache[key] = cached
        surface.blit(cached, rect.topleft)

    def _draw_city_uncached(self, surface: pygame.Surface, rect: pygame.Rect, city: CityMap, zoom: float = 1.0, view_center: tuple[float, float] = (0.5, 0.5)) -> None:
        pygame.draw.rect(surface, MAP_BASE, rect)
        previous_clip = surface.get_clip()
        surface.set_clip(rect)
        map_rect = _fit_rect(rect, city.map_aspect_ratio)
        display_width_km = city.map_width_km or max(0.1, city.preview_radius_km * 2 or city.radius_km * 2)
        pixels_per_meter = map_rect.w / (display_width_km * 1000)

        def screen_points(points: list[tuple[float, float]]) -> list[tuple[int, int]]:
            return [
                (int(map_rect.centerx + (x - view_center[0]) * map_rect.w * zoom), int(map_rect.centery + (y - view_center[1]) * map_rect.h * zoom))
                for x, y in points
            ]

        # Terrain, parks, and water sit beneath every built feature.
        for park in city.parks:
            points = screen_points(park)
            if len(points) >= 3:
                pygame.draw.polygon(surface, (162, 190, 147), points)
        for water in city.water:
            points = screen_points(water)
            if len(points) >= 3:
                pygame.draw.polygon(surface, (91, 153, 166), points)
                outline_width = max(1, round(1.2 * pixels_per_meter * zoom))
                pygame.draw.polygon(surface, (115, 177, 187), points, outline_width)
        waterway_width = max(1, round(12 * pixels_per_meter * zoom))
        for waterway in city.waterways:
            points = screen_points(waterway)
            if len(points) >= 2:
                pygame.draw.lines(surface, (91, 153, 166), False, points, waterway_width)

        # Streets are drawn before footprints so buildings stay visually distinct.
        minor_types = {"residential", "living_street", "service", "unclassified", "footway", "path", "track", "pedestrian", "cycleway"}
        major_types = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"}
        roads_by_layer: dict[str, list[tuple[list[tuple[int, int]], str, float]]] = {"minor": [], "major": [], "bridge": []}
        for index, road in enumerate(city.roads):
            points = screen_points(road)
            if len(points) < 2:
                continue
            road_type = city.road_types[index] if index < len(city.road_types) else "residential"
            attributes = set(road_type.split("|"))
            road_class = next(iter(attributes - {"bridge", "narrow", "oneway", "oneway_reverse"}), "residential")
            layer = "bridge" if "bridge" in attributes else ("major" if road_class in major_types else "minor")
            width_m = city.road_widths_m[index] if index < len(city.road_widths_m) else _road_width_m({"highway": road_class})
            roads_by_layer[layer].append((points, road_class, width_m))

        def draw_markings(points: list[tuple[int, int]], color: tuple[int, int, int], dash_length: float) -> None:
            for start, end in zip(points, points[1:]):
                dx, dy = end[0] - start[0], end[1] - start[1]
                length = math.hypot(dx, dy)
                offset = 0.0
                while offset < length:
                    finish = min(length, offset + dash_length * 0.58)
                    first = (round(start[0] + dx * offset / length), round(start[1] + dy * offset / length))
                    second = (round(start[0] + dx * finish / length), round(start[1] + dy * finish / length))
                    pygame.draw.line(surface, color, first, second, 1)
                    offset += dash_length

        for layer, roads in roads_by_layer.items():
            for points, road_class, width_m in roads:
                if layer == "bridge":
                    width_m = max(width_m, 10.0)
                inner_width = max(1, round(width_m * pixels_per_meter * zoom))
                casing_width = max(inner_width, round((width_m + 2.5) * pixels_per_meter * zoom))
                casing, pavement = (75, 113, 124), (177, 195, 188)
                if layer == "major":
                    casing, pavement = (107, 122, 119), (211, 210, 185)
                elif layer == "minor":
                    casing, pavement = (135, 149, 136), (224, 225, 204)
                pygame.draw.lines(surface, casing, False, points, casing_width)
                pygame.draw.lines(surface, pavement, False, points, inner_width)
                if layer != "minor" and inner_width >= 3:
                    marking = (219, 195, 132) if layer == "bridge" else (169, 151, 108)
                    draw_markings(points, marking, max(3.0, 13 * pixels_per_meter * zoom))

        building_colors = {
            "residential": (219, 216, 188),
            "commercial": (222, 194, 151),
            "industrial": (173, 193, 193),
            "civic": (224, 181, 158),
        }
        for index, polygon in enumerate(city.buildings):
            points = screen_points(polygon)
            if len(points) >= 3:
                category = city.building_types[index] if index < len(city.building_types) else "residential"
                pygame.draw.polygon(surface, building_colors.get(category, MAP_BUILDING), points)
                bounds = pygame.Rect(
                    min(point[0] for point in points),
                    min(point[1] for point in points),
                    max(point[0] for point in points) - min(point[0] for point in points),
                    max(point[1] for point in points) - min(point[1] for point in points),
                )
                if bounds.w >= 3 and bounds.h >= 3:
                    pygame.draw.polygon(surface, (74, 91, 83), points, max(1, round(pixels_per_meter * zoom)))

        surface.set_clip(previous_clip)
        pygame.draw.rect(surface, LINE, rect, 1)

    def _text(self, surface: pygame.Surface, text: str, x: int, y: int, color: tuple[int, int, int] = TEXT, font: pygame.font.Font | None = None) -> None:
        surface.blit((font or self.font).render(str(text), True, color), (x, y))

    def _button(self, surface: pygame.Surface, key: str, rect: pygame.Rect, label: str, *, active: bool = False, disabled: bool = False, accent: tuple[int, int, int] = LIME, subtitle: str | None = None) -> None:
        self.hit_targets[key] = rect
        fill = PANEL_HI if active else PANEL
        border = accent if active else LINE
        if disabled:
            fill, border = (20, 27, 27), (34, 45, 44)
        pygame.draw.rect(surface, fill, rect, border_radius=5)
        pygame.draw.rect(surface, border, rect, 1, border_radius=5)
        tint = (75, 85, 81) if disabled else (accent if active else TEXT)
        compact = rect.h <= 28
        self._text(surface, label, rect.x + (7 if compact else 10), rect.y + (4 if compact else (8 if subtitle else 11)), tint, self.small if compact else self.bold)
        if subtitle:
            self._text(surface, subtitle, rect.x + 10, rect.y + 28, MUTED if not disabled else (68, 79, 75), self.small)

    def _draw_tooltip(self, surface: pygame.Surface) -> None:
        if not self.mouse:
            return
        hovered = next((key for key, rect in self.hit_targets.items() if rect.collidepoint(self.mouse)), None)
        if hovered is None:
            return
        tooltips = {
            **{
                f"unit_{kind}": (
                    kind,
                    f"${item['cost']} · {item['description'].lower()}. "
                    + ("Drag to lay a wall; ends snap to nearby buildings." if kind == "BARRICADE" else "Click map to place the preview.")
                )
                for kind, item in UNITS.items()
            },
            "strike": ("BOMBER STRIKE", "$180 · Preview the blast radius, then click to confirm."),
            "gunship": ("GUNSHIP", "$290 · Preview its coverage, then click to confirm."),
            "origin": ("PATIENT ZERO", "Choose a new outbreak origin during deployment."),
            "policy_EVACUATE": ("EVACUATE", "Route residents toward deployed evacuation stations."),
            "policy_SHELTER": ("SHELTER", "Route residents to safe zones within capacity."),
            "policy_FLEE": ("FLEE", "Residents flee local horde pressure without a destination."),
            "intel_risk": ("INFECTION RISK", "Show local infection pressure on the map."),
            "intel_horde": ("HORDE DENSITY", "Show zombie concentration by map cell."),
            "intel_exposed": ("EXPOSED", "Show residents incubating before conversion."),
            "intel_survivors": ("SURVIVORS", "Show remaining uninfected residents by map cell."),
        }
        tooltip = tooltips.get(hovered)
        if tooltip is None:
            return
        title, detail = tooltip
        max_text_width = 268
        words = detail.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and self.small.size(candidate)[0] > max_text_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        width = min(294, max(self.bold.size(title)[0], *(self.small.size(line)[0] for line in lines)) + 20)
        height = 26 + len(lines) * 14
        button = self.hit_targets[hovered]
        x = button.x - width - 10 if button.x > width + 20 else button.right + 10
        y = max(84, min(HEIGHT - height - 8, button.centery - height // 2))
        box = pygame.Rect(x, y, width, height)
        pygame.draw.rect(surface, PANEL_HI, box, border_radius=4)
        pygame.draw.rect(surface, CYAN, box, 1, border_radius=4)
        self._text(surface, title, x + 10, y + 6, TEXT, self.bold)
        for index, line in enumerate(lines):
            self._text(surface, line, x + 10, y + 24 + index * 14, MUTED, self.small)

    def _header(self, surface: pygame.Surface, mode: str) -> None:
        pygame.draw.rect(surface, PANEL, (0, 0, WIDTH, 78))
        pygame.draw.line(surface, LINE, (0, 77), (WIDTH, 77), 1)
        pygame.draw.rect(surface, LIME, (20, 19, 4, 34), border_radius=2)
        self._text(surface, "DEADGRID", 36, 16, TEXT, self.bold)
        self._text(surface, "CITY CONTAINMENT  /  FIELD COMMAND", 36, 38, MUTED, self.small)
        self._text(surface, mode, 600, 27, LIME, self.small)
        if not mode.startswith("LIVE SIMULATION"):
            self._text(surface, "F10 1080P  ·  F11 FULLSCREEN", 1055, 49, MUTED, self.small)
        if mode.startswith("LIVE SIMULATION"):
            self._text(surface, f"T+ {int(self.simulation_time // 60):02}:{int(self.simulation_time % 60):02}", 1010, 27, AMBER, self.bold)
            self._button(surface, "stop_simulation", pygame.Rect(1203, 16, 132, 45), "STOP SIMULATION", accent=RED)

    def _draw_menu(self) -> None:
        surface = self.canvas
        surface.fill(INK)
        self._header(surface, "INCIDENT COMMAND  /  01")
        preview = pygame.Rect(695, 105, 645, 665)
        self._draw_city(surface, preview, self.preview_map)
        shade = pygame.Surface(preview.size, pygame.SRCALPHA)
        shade.fill((13, 23, 24, 65))
        surface.blit(shade, preview.topleft)
        ox, oy = preview.x + preview.w // 2, preview.y + preview.h // 2
        pygame.draw.circle(surface, (222, 83, 73), (ox, oy), 76, 1)
        pygame.draw.circle(surface, (222, 83, 73), (ox, oy), 38, 1)
        pygame.draw.circle(surface, RED, (ox, oy), 5)
        surface.blit(self.menu_logo, (36, 112))
        pygame.draw.line(surface, LINE, (36, 339), (654, 339), 1)
        self._text(surface, "AREA OF OPERATIONS", 36, 361, CYAN, self.small)
        input_rect = pygame.Rect(36, 389, 615, 47)
        self.hit_targets["place_input"] = input_rect
        pygame.draw.rect(surface, PANEL_HI if self.input_active else PANEL, input_rect, border_radius=5)
        pygame.draw.rect(surface, LIME if self.input_active else LINE, input_rect, 1, border_radius=5)
        shown = self.place_input[-52:]
        self._text(surface, shown + ("|" if self.input_active and int(time.time() * 2) % 2 else ""), input_rect.x + 13, input_rect.y + 14, TEXT)
        self._text(surface, "QUICK DEPLOY", 36, 454, MUTED, self.small)
        for index, place in enumerate(PRESETS):
            col, row = index % 2, index // 2
            key = f"preset_{index}"
            self._button(surface, key, pygame.Rect(36 + col * 310, 478 + row * 47, 300, 38), place, accent=CYAN)
        self._button(surface, "overture", pygame.Rect(36, 591, 24, 24), "", active=self.use_overture, accent=CYAN)
        pygame.draw.circle(surface, CYAN if self.use_overture else MUTED, (48, 603), 5 if self.use_overture else 3)
        self._text(surface, "SUPPLEMENT BUILDINGS WITH OVERTURE MAPS", 70, 595, TEXT, self.small)
        self._text(surface, "AREA RADIUS", 36, 624, CYAN, self.small)
        radius_rect = pygame.Rect(36, 646, 266, 12)
        self.hit_targets["radius_slider"] = radius_rect
        pygame.draw.rect(surface, LINE, (radius_rect.x, radius_rect.y + 4, radius_rect.w, 4), border_radius=2)
        radius_ratio = (self.radius_km - 0.35) / (2.0 - 0.35)
        pygame.draw.rect(surface, LIME, (radius_rect.x, radius_rect.y + 4, int(radius_rect.w * radius_ratio), 4), border_radius=2)
        pygame.draw.circle(surface, TEXT, (int(radius_rect.x + radius_rect.w * radius_ratio), radius_rect.centery), 7)
        self._text(surface, f"{self.radius_km:.2f} KM  ·  {math.pi * self.radius_km ** 2:.1f} KM²", 36, 663, MUTED, self.small)
        self._text(surface, "INITIAL HORDE", 359, 624, CYAN, self.small)
        for index, percentage in enumerate((0.5, 1.0, 2.0, 5.0)):
            label = f"{percentage:g}%"
            self._button(surface, f"infect_{label}", pygame.Rect(359 + index * 67, 641, 61, 31), label, active=self.initial_infection_percent == percentage, accent=RED)
        self._text(surface, "© OpenStreetMap contributors  ·  Overture Maps Foundation", 718, 736, (67, 83, 73), self.small)
        if self.loading:
            pygame.draw.rect(surface, PANEL, (36, 680, 615, 54), border_radius=5)
            self._text(surface, "QUERYING OPEN MAP DATA", 52, 689, LIME, self.small)
            self._text(surface, "Geocoding place · loading streets and building footprints…", 52, 709, MUTED, self.small)
            pygame.draw.rect(surface, LIME, (36, 729, 615, 2))
        else:
            self._button(surface, "build_plan", pygame.Rect(36, 676, 300, 57), "BUILD RESPONSE PLAN  →", accent=LIME, subtitle="REVIEW MAP · DEPLOY")
            self._button(surface, "config", pygame.Rect(346, 676, 305, 57), "WORLD SETTINGS", accent=CYAN, subtitle="TUNE & SAVE SCENARIOS")
        self._text(surface, self.message if self.message_timer > 0 else f"SAVED SCENARIOS  {len(self.saved_scenarios)}  ·  STARTING INFECTION  {self.initial_infection_percent:g}%", 36, 779, MUTED, self.small)

    def _draw_area_select(self) -> None:
        surface = self.canvas
        surface.fill(INK)
        self.hit_targets = {}
        self._header(surface, "SELECT OPERATION AREA")
        self._text(surface, self.city.name.upper(), 40, 88, TEXT, self.bold)
        self._text(surface, "CLICK TO CENTER  ·  RIGHT-DRAG TO PAN  ·  WHEEL OR +/- TO ZOOM", 365, 91, MUTED, self.small)
        self._draw_area_map(surface)
        map_rect = self._area_content_rect()
        center_point = (
            int(map_rect.centerx + (self.area_selection[0] - self.area_view_center[0]) * map_rect.w * self.area_zoom),
            int(map_rect.centery + (self.area_selection[1] - self.area_view_center[1]) * map_rect.h * self.area_zoom),
        )
        bounds = self.city.query_bounds
        if bounds:
            west, south, east, north = bounds
            latitude = north - self.area_selection[1] * (north - south)
            radius_lat = self.radius_km / 110.57
            radius_lon = radius_lat / max(0.22, math.cos(math.radians(latitude)))
            radius_x = max(2, round(radius_lon / (east - west) * map_rect.w * self.area_zoom))
            radius_y = max(2, round(radius_lat / (north - south) * map_rect.h * self.area_zoom))
        else:
            preview_radius = max(self.city.preview_radius_km, self.radius_km + 1.5, 3.5)
            radius_y = max(2, round(self.radius_km / (2 * preview_radius) * map_rect.h * self.area_zoom))
            radius_x = max(2, round(self.radius_km / (2 * preview_radius) * map_rect.w * self.area_zoom))
        area_rect = pygame.Rect(center_point[0] - radius_x, center_point[1] - radius_y, radius_x * 2, radius_y * 2)
        shade = pygame.Surface(map_rect.size, pygame.SRCALPHA)
        shade.fill((8, 15, 13, 104))
        pygame.draw.ellipse(shade, (0, 0, 0, 0), area_rect.move(-map_rect.x, -map_rect.y))
        surface.blit(shade, map_rect.topleft)
        previous_clip = surface.get_clip()
        surface.set_clip(map_rect)
        pygame.draw.ellipse(surface, (20, 28, 25), area_rect, 9)
        pygame.draw.ellipse(surface, LIME, area_rect, 3)
        pygame.draw.circle(surface, RED, center_point, 7)
        pygame.draw.line(surface, RED, (center_point[0] - 16, center_point[1]), (center_point[0] + 16, center_point[1]), 2)
        pygame.draw.line(surface, RED, (center_point[0], center_point[1] - 16), (center_point[0], center_point[1] + 16), 2)
        surface.set_clip(previous_clip)
        pygame.draw.rect(surface, PANEL, (40, 718, 1280, 72), border_radius=4)
        self._text(surface, f"AREA RADIUS  {self.radius_km:.2f} KM", 58, 730, CYAN, self.small)
        radius_slider = pygame.Rect(58, 755, 435, 14)
        self.hit_targets["area_radius_slider"] = radius_slider
        pygame.draw.rect(surface, LINE, (radius_slider.x, radius_slider.y + 5, radius_slider.w, 4), border_radius=2)
        ratio = (self.radius_km - 0.35) / (2.0 - 0.35)
        pygame.draw.rect(surface, LIME, (radius_slider.x, radius_slider.y + 5, int(radius_slider.w * ratio), 4), border_radius=2)
        pygame.draw.circle(surface, TEXT, (int(radius_slider.x + radius_slider.w * ratio), radius_slider.centery), 7)
        self._text(surface, f"{math.pi * self.radius_km ** 2:.1f} KM²  /  SELECTED FOOTPRINT", 520, 738, TEXT, self.small)
        self._button(surface, "zoom_out", pygame.Rect(770, 730, 44, 42), "−", accent=CYAN)
        self._text(surface, f"{self.area_zoom:.1f}×", 824, 744, TEXT, self.bold)
        self._button(surface, "zoom_in", pygame.Rect(867, 730, 44, 42), "+", accent=CYAN)
        self._button(surface, "area_back", pygame.Rect(1000, 730, 132, 46), "BACK", accent=AMBER)
        self._button(surface, "area_confirm", pygame.Rect(1142, 730, 160, 46), "USE THIS AREA", accent=LIME)
        self._text(surface, f"{self.city.source}  ·  {len(self.city.roads):,} STREETS  ·  {len(self.city.buildings):,} BUILDINGS", 58, 779, MUTED, self.small)

    def _draw_config(self) -> None:
        surface = self.canvas
        surface.fill(INK)
        self.hit_targets = {}
        self._header(surface, "SANDBOX  /  WORLD SETTINGS")
        self._text(surface, "CONFIGURE THE OUTBREAK", 66, 115, LIME, self.large)
        self._text(surface, "These settings define a reusable world simulation.", 68, 164, MUTED)
        self._text(surface, "SCENARIO NAME", 68, 211, CYAN, self.small)
        name_rect = pygame.Rect(68, 232, 530, 47)
        self.hit_targets["scenario_name"] = name_rect
        pygame.draw.rect(surface, PANEL_HI if self.scenario_name_active else PANEL, name_rect, border_radius=5)
        pygame.draw.rect(surface, LIME if self.scenario_name_active else LINE, name_rect, 1, border_radius=5)
        self._text(surface, self.scenario_name + ("|" if self.scenario_name_active and int(time.time() * 2) % 2 else ""), name_rect.x + 13, name_rect.y + 14, TEXT)
        self._text(surface, f"AREA  /  {self.place_input[:24]}  /  {self.radius_km:.2f} KM RADIUS", 640, 248, MUTED, self.small)

        self._text(surface, "POPULATION DENSITY", 68, 318, CYAN, self.small)
        self._text(surface, "Scale the estimated population inside the selected area.", 68, 340, MUTED, self.small)
        density_choices = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
        for index, density in enumerate(density_choices):
            self._button(surface, f"density_{density:g}", pygame.Rect(68 + index * 112, 369, 100, 42), f"{density:g}×", active=self.population_density == density, accent=CYAN)

        self._text(surface, "ZOMBIE BEHAVIOR", 68, 451, CYAN, self.small)
        behavior_info = {"BALANCED": "BASELINE SPEED / EXPOSURE", "RUNNER": "FASTER STREET PURSUIT", "SWARM": "SLOWER / HIGHER EXPOSURE"}
        for index, behavior in enumerate(("BALANCED", "RUNNER", "SWARM")):
            self._button(surface, f"behavior_{behavior}", pygame.Rect(68 + index * 260, 478, 244, 54), behavior, active=self.zombie_behavior == behavior, accent=AMBER, subtitle=behavior_info[behavior])

        self._text(surface, "STARTING RESOURCES", 68, 563, CYAN, self.small)
        for index, resources in enumerate((1000, 1500, 2500, 5000)):
            self._button(surface, f"resources_{resources}", pygame.Rect(68 + index * 176, 589, 160, 43), f"${resources:,}", active=self.starting_resources == resources, accent=LIME)
        self._button(surface, "auto_collapse", pygame.Rect(68, 659, 220, 44), "AUTO COLLAPSE  /  " + ("ON" if self.auto_collapse else "OFF"), active=self.auto_collapse, accent=RED)
        self._text(surface, "Collapse detection ends only when the remaining population falls below 2%.", 306, 675, MUTED, self.small)
        self._text(surface, "SAVED SCENARIOS", 820, 211, CYAN, self.small)
        visible_scenarios = list(enumerate(self.saved_scenarios[-6:], start=max(0, len(self.saved_scenarios) - 6)))
        for row, (scenario_index, scenario) in enumerate(visible_scenarios):
            y = 239 + row * 62
            detail = f"{scenario.get('place', 'Unknown place')}  ·  {scenario.get('radius_km', 1.5):.1f} km  ·  {scenario.get('population_density', 1.0):g}× density"
            self._button(surface, f"scenario_{scenario_index}", pygame.Rect(820, y, 474, 52), str(scenario.get("name", "Saved scenario"))[:42], active=self.selected_scenario_index == scenario_index, accent=CYAN, subtitle=detail[:66])
        self._button(surface, "load_scenario", pygame.Rect(820, 623, 474, 50), "LOAD SELECTED SCENARIO", disabled=not self.saved_scenarios, accent=AMBER)
        self._button(surface, "back_config", pygame.Rect(68, 743, 190, 54), "← BACK TO MAP", accent=CYAN)
        self._button(surface, "save_scenario", pygame.Rect(838, 743, 220, 54), "SAVE SCENARIO", accent=LIME, subtitle="STORE THESE SETTINGS")
        self._text(surface, self.message if self.message_timer > 0 else f"SAVED SCENARIOS  {len(self.saved_scenarios)}", 820, 695, MUTED, self.small)
        self._text(surface, self.message if self.message_timer > 0 else f"SAVED SCENARIOS  {len(self.saved_scenarios)}", 68, 807, MUTED, self.small)

    def _draw_map_overlay(self, surface: pygame.Surface) -> None:
        self._draw_city(surface, MAP_RECT, self.city)
        intel_labels = self._draw_intelligence(surface)

        # Moving cohorts stay above the intelligence heatmaps.
        self._draw_population(surface)
        map_rect = self._map_content_rect()

        for effect in self.effects:
            progress = effect["age"] / effect["duration"]
            radius_x = max(1, round(effect["radius"] * (0.45 + progress * 0.7) * map_rect.w / WORLD_W))
            radius_y = max(1, round(effect["radius"] * (0.45 + progress * 0.7) * map_rect.h / WORLD_H))
            color = AMBER if effect["kind"] == "strike" else CYAN
            center = self._map_point(effect["x"] / WORLD_W, effect["y"] / WORLD_H)
            pygame.draw.ellipse(surface, color, pygame.Rect(center[0] - radius_x, center[1] - radius_y, radius_x * 2, radius_y * 2), 2)

        # Deployed assets are intentionally visible over crowds and streets.
        for structure in self.units:
            center = self._map_point(structure["x"] / WORLD_W, structure["y"] / WORLD_H)
            kind = structure["kind"]
            data = UNITS[kind]
            if kind == "BARRICADE":
                pygame.draw.circle(surface, (165, 135, 83), center, 10, 2)
                pygame.draw.line(surface, data["color"], (center[0] - 8, center[1] - 5), (center[0] + 8, center[1] + 5), 3)
            elif kind in ("EVAC STATION", "SAFE ZONE"):
                pygame.draw.circle(surface, data["color"], center, 9, 2)
                pygame.draw.line(surface, data["color"], (center[0] - 4, center[1]), (center[0] + 4, center[1]), 2)
                pygame.draw.line(surface, data["color"], (center[0], center[1] - 4), (center[0], center[1] + 4), 2)
            elif kind == "SUPPLY DROP":
                pygame.draw.rect(surface, data["color"], pygame.Rect(center[0] - 7, center[1] - 7, 14, 14), border_radius=2)
                pygame.draw.line(surface, INK, (center[0] - 4, center[1]), (center[0] + 4, center[1]), 2)
            else:
                pygame.draw.circle(surface, (14, 25, 25), center, 11)
                pygame.draw.circle(surface, data["color"], center, 8, 2)
                pygame.draw.circle(surface, data["color"], center, 3)

        # Route highlights, selection, and labels form the top map layer.
        if self.screen_name == "active" and len(self.evac_next_nodes) == len(self.road_nodes):
            evacuating = np.flatnonzero(self.civilians["evacuating"] & (self.civilians["status"] == 0))
            if len(evacuating):
                node = int(self.civilians["route_node"][evacuating[0]])
                route = []
                visited = set()
                while 0 <= node < len(self.road_nodes) and node not in visited and len(route) < len(self.road_nodes):
                    visited.add(node)
                    road_x, road_y = self.road_nodes[node]
                    route.append(self._map_point(float(road_x / WORLD_W), float(road_y / WORLD_H)))
                    node = int(self.evac_next_nodes[node])
                    if node < 0:
                        break
                if len(route) > 1:
                    pygame.draw.lines(surface, CYAN, False, route, 2)

        infection = self._map_point(*self.infection_origin)
        pygame.draw.circle(surface, (214, 77, 70), infection, 47, 1)
        pygame.draw.circle(surface, (214, 77, 70), infection, 24, 1)
        pygame.draw.circle(surface, RED, infection, 4)
        if self.moving_unit in self.units:
            center = self._map_point(self.moving_unit["x"] / WORLD_W, self.moving_unit["y"] / WORLD_H)
            radius_x = max(1, round(self.moving_unit["range"] * map_rect.w / WORLD_W))
            radius_y = max(1, round(self.moving_unit["range"] * map_rect.h / WORLD_H))
            pygame.draw.ellipse(surface, UNITS[self.moving_unit["kind"]]["color"], pygame.Rect(center[0] - radius_x, center[1] - radius_y, radius_x * 2, radius_y * 2), 1)
            pygame.draw.circle(surface, TEXT, center, 14, 1)
        self._text(surface, "PATIENT ZERO", infection[0] + 10, infection[1] - 20, RED, self.small)
        if intel_labels:
            title = "INTEL  /  " + "  ·  ".join(intel_labels)
            self._text(surface, title[:86], MAP_RECT.right - 390, MAP_RECT.y + 13, TEXT, self.small)
        if self.target_mode:
            cursor = self._map_point(*self._target_preview())
            color = RED if self.target_mode == "origin" else (AMBER if self.target_mode in ("strike", "deploy") else CYAN)
            if self.target_mode == "deploy" and self.selected_unit == "BARRICADE":
                if self.barricade_drag_start:
                    end = self._snap_barricade_anchor(self._world_point(cursor))
                    start = self.barricade_drag_start
                    start_point = self._map_point(start[0] / WORLD_W, start[1] / WORLD_H)
                    end_point = self._map_point(end[0] / WORLD_W, end[1] / WORLD_H)
                    pygame.draw.line(surface, (34, 42, 39), start_point, end_point, 9)
                    pygame.draw.line(surface, AMBER, start_point, end_point, 4)
                    length = math.dist(start, end)
                    count = max(1, math.ceil(length / 48.0) + (1 if length >= 18.0 else 0))
                    for index in range(count):
                        fraction = index / max(1, count - 1)
                        post = (round(start_point[0] + (end_point[0] - start_point[0]) * fraction), round(start_point[1] + (end_point[1] - start_point[1]) * fraction))
                        pygame.draw.circle(surface, AMBER, post, 5, 2)
                    self._text(surface, f"{count} POSTS  ·  ${count * UNITS['BARRICADE']['cost']}", min(start_point[0], end_point[0]) + 8, min(start_point[1], end_point[1]) - 18, AMBER, self.small)
                else:
                    pygame.draw.circle(surface, AMBER, cursor, 7, 2)
                    pygame.draw.line(surface, AMBER, (cursor[0] - 8, cursor[1]), (cursor[0] + 8, cursor[1]), 2)
            else:
                if self.target_mode == "deploy":
                    radius_world = UNITS[self.selected_unit]["range"]
                elif self.target_mode == "strike":
                    radius_world = 60
                elif self.target_mode == "gunship":
                    radius_world = 92
                else:
                    radius_world = 30
                radius_x = max(1, round(radius_world * map_rect.w / WORLD_W))
                radius_y = max(1, round(radius_world * map_rect.h / WORLD_H))
                pygame.draw.ellipse(surface, color, pygame.Rect(cursor[0] - radius_x, cursor[1] - radius_y, radius_x * 2, radius_y * 2), 1)
                pygame.draw.circle(surface, color, cursor, 5, 2)
                pygame.draw.line(surface, color, (cursor[0] - 12, cursor[1]), (cursor[0] + 12, cursor[1]), 1)
                pygame.draw.line(surface, color, (cursor[0], cursor[1] - 12), (cursor[0], cursor[1] + 12), 1)

    def _draw_intelligence(self, surface: pygame.Surface) -> list[str]:
        labels = {"risk": "INFECTION RISK", "horde": "HORDE DENSITY", "exposed": "EXPOSED CIVILIANS", "survivors": "EST. SURVIVORS"}
        shown = []
        map_rect = self._map_content_rect()
        for mode, enabled in self.intel_modes.items():
            if enabled and mode in self.intel_layers:
                surface.blit(self.intel_layers[mode], map_rect.topleft)
                shown.append(labels[mode])
        return shown

    def _target_preview(self) -> tuple[float, float]:
        x, y = self.mouse
        rect = self._map_content_rect()
        return (max(0.0, min(1.0, (x - rect.x) / rect.w)), max(0.0, min(1.0, (y - rect.y) / rect.h)))

    def _draw_operation(self, active: bool) -> None:
        surface = self.canvas
        surface.fill(INK)
        self.hit_targets = {}
        self._header(surface, f"LIVE SIMULATION  /  {self.simulation_status.upper()}" if active else "DEPLOYMENT PHASE")
        # Left: command picture and incident telemetry.
        pygame.draw.rect(surface, PANEL, (20, 100, 236, 664), border_radius=5)
        self._text(surface, "INCIDENT BRIEF", 36, 118, CYAN, self.small)
        self._text(surface, self.city.name[:25].upper(), 36, 142, TEXT, self.bold)
        self._text(surface, f"MAP SOURCE  /  {self.city.source}", 36, 168, MUTED, self.small)
        pygame.draw.line(surface, LINE, (36, 193), (240, 193), 1)
        self._text(surface, "ESTIMATED RESIDENTS", 36, 209, MUTED, self.small)
        self._text(surface, f"~{self.city.population:,}", 36, 231, TEXT, self.large)
        self._text(surface, f"MAX {self.people_per_agent:,} PEOPLE / AGENT", 36, 269, MUTED, self.small)
        pygame.draw.line(surface, LINE, (36, 290), (240, 290), 1)
        self._text(surface, "INITIAL INFECTED", 36, 302, RED, self.small)
        self._text(surface, f"{self.initial_infected_agents:,} AGENTS (~{self.initial_infected_population:,} PEOPLE)", 36, 322, TEXT, self.small)
        self._text(surface, "RESCUED POPULATION", 36, 350, LIME, self.small)
        self._text(surface, f"{self.rescued:,}", 36, 371, LIME, self.large)
        self._text(surface, "CASUALTIES", 148, 382, RED, self.small)
        self._text(surface, f"{self.casualties:,}", 148, 402, RED, self.bold)
        pygame.draw.line(surface, LINE, (36, 430), (240, 430), 1)
        self._text(surface, "THREAT TRACKER", 36, 441, AMBER, self.small)
        self._text(surface, f"HOSTILES     {len(self.zombies['x']) + self.infected_count:,}", 36, 465, RED, self.bold)
        self._text(surface, f"INCUBATING   {self.infected_count:,}", 36, 488, MUTED, self.small)
        self._text(surface, f"RESOURCES    ${self.budget:,}", 36, 511, LIME, self.bold)
        if active:
            pygame.draw.line(surface, LINE, (36, 540), (240, 540), 1)
            self._text(surface, "CONTAINMENT", 36, 551, CYAN, self.small)
            rate = len(self.zombies["x"]) / max(1, self.simulation_time / 60)
            self._text(surface, f"OUTBREAK RATE   {rate:0.1f}/MIN", 36, 575, AMBER, self.small)
            self._text(surface, f"DEPLOYED ASSETS {len(self.units):02}", 36, 598, MUTED, self.small)
            evacuees = int(np.count_nonzero(self.civilians["evacuating"] & (self.civilians["status"] == 0)))
            self._text(surface, f"EVACUATING {evacuees:,}  ·  SPACE PAUSE", 36, 628, MUTED, self.small)
        else:
            self._text(surface, "1–6 SELECT  ·  CLICK MAP TO DEPLOY", 36, 562, MUTED, self.small)
            self._text(surface, "CLICK ASSET BUTTON  ·  PLACE TO DEPLOY", 36, 582, MUTED, self.small)
            self._text(surface, self.city.population_source[:31], 36, 620, MUTED, self.small)
            self._button(surface, "launch", pygame.Rect(36, 690, 204, 48), "BEGIN OPERATION  →", accent=LIME, subtitle="PATIENT ZERO IS ACTIVE")
        # Map and source bar.
        self._draw_map_overlay(surface)
        pygame.draw.rect(surface, PANEL, (MAP_RECT.x, MAP_RECT.bottom, MAP_RECT.w, 27))
        self._text(surface, f"{self.city.name.upper()}   ·   {self.city.source}   ·   © OPENSTREETMAP CONTRIBUTORS / OVERTURE MAPS", MAP_RECT.x + 12, MAP_RECT.bottom + 7, (118, 140, 128), self.small)
        if self.target_mode == "deploy":
            instruction = "DRAG TO LAY WALL · RELEASE TO PLACE" if self.selected_unit == "BARRICADE" and self.barricade_drag_start else ("BARRICADE · CLICK OR DRAG TO PLACE" if self.selected_unit == "BARRICADE" else "PLACEMENT PREVIEW · CLICK TO CONFIRM")
            self._text(surface, instruction, MAP_RECT.x + 15, MAP_RECT.y + 13, AMBER, self.small)
        elif self.target_mode == "origin":
            self._text(surface, "SET OUTBREAK ORIGIN  ·  CLICK MAP", MAP_RECT.x + 15, MAP_RECT.y + 13, RED, self.small)
        elif self.target_mode == "move":
            self._text(surface, "SELECT NEW POSITION  ·  CLICK MAP TO CONFIRM", MAP_RECT.x + 15, MAP_RECT.y + 13, CYAN, self.small)
        elif self.target_mode:
            mode_name = "BOMBER STRIKE" if self.target_mode == "strike" else ("GUNSHIP TASKING" if self.target_mode == "gunship" else "REPOSITION ASSET")
            self._text(surface, f"AIMING  /  {mode_name}  ·  CLICK MAP TO CONFIRM", MAP_RECT.x + 15, MAP_RECT.y + 13, AMBER if self.target_mode == "strike" else CYAN, self.small)
        else:
            label = "SET PERIMETER  /  CLICK TO DEPLOY" if not active else ("PAUSED  ·  SPACE TO RESUME" if self.phase_time == -1 else "LIVE FIELD VIEW  /  RIGHT-CLICK TO CANCEL")
            self._text(surface, label, MAP_RECT.x + 15, MAP_RECT.y + 13, LIME if not active else CYAN, self.small)
        # Right: deployable forces and support.
        pygame.draw.rect(surface, PANEL, (1092, 100, 248, 664), border_radius=5)
        self._text(surface, "DEPLOYMENT", 1108, 118, CYAN, self.small)
        self._text(surface, "GROUND ASSETS", 1108, 146, MUTED, self.small)
        unit_order = list(UNITS)
        for index, kind in enumerate(unit_order):
            y = 168 + index * 43
            item = UNITS[kind]
            active_tool = self.selected_unit == kind and self.target_mode in (None, "deploy")
            self._button(surface, f"unit_{kind}", pygame.Rect(1108, y, 216, 40), kind, active=active_tool, accent=item["color"], subtitle=f"${item['cost']}  /  {item['description']}")
            if active_tool:
                pygame.draw.rect(surface, item["color"], pygame.Rect(1104, y - 2, 4, 44), border_radius=2)
            if index < 6:
                self._text(surface, str(index + 1), 1310, y + 13, MUTED, self.small)
        pygame.draw.line(surface, LINE, (1108, 516), (1324, 516), 1)
        self._text(surface, "AIR SUPPORT", 1108, 531, MUTED, self.small)
        strike_cost = 180
        gunship_cost = 290
        self._button(surface, "strike", pygame.Rect(1108, 556, 102, 55), "BOMBER", active=self.target_mode == "strike", disabled=self.budget < strike_cost, accent=AMBER, subtitle=f"${strike_cost} · AOE")
        self._button(surface, "gunship", pygame.Rect(1220, 556, 104, 55), "GUNSHIP", active=self.target_mode == "gunship", disabled=self.budget < gunship_cost, accent=CYAN, subtitle=f"${gunship_cost} · 10 SEC")
        self._button(surface, "origin", pygame.Rect(1108, 629, 216, 40), "SET OUTBREAK ORIGIN", active=self.target_mode == "origin", disabled=active, accent=RED, subtitle="CLICK MAP TO PLACE PATIENT ZERO")
        self._text(surface, f"POLICY  /  {self.emergency_policy}", 1108, 677, CYAN, self.small)
        for index, policy in enumerate(("EVACUATE", "SHELTER", "FLEE")):
            label = {"EVACUATE": "EVAC", "SHELTER": "SHELTER", "FLEE": "FLEE"}[policy]
            self._button(surface, f"policy_{policy}", pygame.Rect(1108 + index * 74, 695, 68, 28), label, active=self.emergency_policy == policy, accent=CYAN)
        self._text(surface, "MAP INTELLIGENCE", 1108, 727, MUTED, self.small)
        overlay_names = (("risk", "RISK"), ("horde", "HORDE"), ("exposed", "EXPO"), ("survivors", "SAFE"))
        for index, (mode, label) in enumerate(overlay_names):
            self._button(surface, f"intel_{mode}", pygame.Rect(1108 + index * 54, 739, 52, 24), label, active=self.intel_modes[mode], accent={"risk": RED, "horde": AMBER, "exposed": (242, 187, 79), "survivors": CYAN}[mode])
        if self.message_timer > 0:
            pygame.draw.rect(surface, (15, 24, 24), (292, 715, 764, 25), border_radius=3)
            self._text(surface, self.message[:91], 302, 721, AMBER if "FAILED" in self.message else TEXT, self.small)
        if self.screen_name == "deployment" and self.mouse and self._map_content_rect().collidepoint(self.mouse):
            cursor = self.mouse
            color = UNITS[self.selected_unit]["color"]
            pygame.draw.circle(surface, color, cursor, 10, 1)
            pygame.draw.line(surface, color, (cursor[0] - 15, cursor[1]), (cursor[0] + 15, cursor[1]), 1)
        self._draw_tooltip(surface)

    def _draw_analysis(self) -> None:
        self.canvas.fill(INK)
        self._header(self.canvas, "SIMULATION ANALYSIS")
        outcome_color = AMBER if self.simulation_status == "Collapsed" else (LIME if self.simulation_status == "Evacuated" else CYAN)
        self._text(self.canvas, f"{self.end_reason or 'SIMULATION STOPPED'}  /  {self.simulation_status.upper()}", 50, 98, outcome_color, self.bold)
        self._text(self.canvas, self.city.name.upper(), 50, 121, TEXT, self.bold)
        self._text(self.canvas, f"RUN TIME  {int(self.simulation_time // 60):02}:{int(self.simulation_time % 60):02}  ·  PATIENT ZERO  {self.patient_zero[0]:.0%}/{self.patient_zero[1]:.0%}  ·  RADIUS {self.radius_km:.2f} KM", 465, 121, MUTED, self.small)

        ledger = self._population_ledger()
        ledger_rect = pygame.Rect(45, 153, 380, 390)
        pygame.draw.rect(self.canvas, PANEL, ledger_rect, border_radius=4)
        pygame.draw.rect(self.canvas, LINE, ledger_rect, 1, border_radius=4)
        self._text(self.canvas, "POPULATION LEDGER", 64, 169, CYAN, self.bold)
        rows = (
            ("STARTING RESIDENTS", ledger["starting"], TEXT),
            ("CURRENTLY UNINFECTED", ledger["uninfected"], LIME),
            ("EXPOSED", ledger["exposed"], AMBER),
            ("INFECTED", ledger["infected"], RED),
            ("RESCUED", ledger["rescued"], CYAN),
            ("DEAD", ledger["dead"], (220, 115, 105)),
        )
        for index, (label, value, color) in enumerate(rows):
            y = 205 + index * 46
            self._text(self.canvas, label, 64, y, MUTED, self.small)
            value_text = f"{value:,}"
            value_width = self.bold.size(value_text)[0]
            self._text(self.canvas, value_text, ledger_rect.right - 18 - value_width, y - 2, color, self.bold)
            if index < len(rows) - 1:
                pygame.draw.line(self.canvas, LINE, (64, y + 30), (ledger_rect.right - 18, y + 30), 1)
        reconciles = ledger["accounted"] == ledger["starting"]
        balance_text = "RECONCILED · 0 UNASSIGNED" if reconciles else f"UNRECONCILED · {ledger['starting'] - ledger['accounted']:,}"
        self._text(self.canvas, balance_text, 64, 493, LIME if reconciles else RED, self.small)
        self._text(self.canvas, f"ZOMBIES ELIMINATED  {self._eliminated:,}  ·  FIELD FUNDS  ${self.budget:,}", 64, 518, MUTED, self.small)

        self._text(self.canvas, "CONTAINMENT FAILURE MAP", 465, 145, CYAN, self.small)
        map_rect = pygame.Rect(465, 163, 850, 380)
        self._draw_city(self.canvas, map_rect, self.city)
        content_rect = _fit_rect(map_rect, self.city.map_aspect_ratio)
        marker_colors = {"outbreak": RED, "route_failure": AMBER, "critical": (234, 130, 76), "collapsed": (235, 75, 68), "evacuated": LIME}
        for event in self.containment_timeline:
            color = marker_colors.get(event["kind"])
            if color is None:
                continue
            for x, y, population in event["locations"]:
                point = (int(content_rect.x + x * content_rect.w), int(content_rect.y + y * content_rect.h))
                radius = 5 if event["kind"] == "outbreak" else min(20, 7 + int(math.log10(max(1, population)) * 2))
                pygame.draw.circle(self.canvas, color, point, radius, 2)
                pygame.draw.circle(self.canvas, color, point, 3)
        legend_items = ((RED, "OUTBREAK"), (AMBER, "ROUTE FAILURE"), ((234, 130, 76), "CRITICAL SPREAD"), ((235, 75, 68), "COLLAPSE"), (LIME, "EVACUATED"))
        legend_x = 465
        for color, label in legend_items:
            pygame.draw.circle(self.canvas, color, (legend_x + 5, 559), 4)
            self._text(self.canvas, label, legend_x + 15, 553, MUTED, self.small)
            legend_x += 151

        self._text(self.canvas, "CONTAINMENT TIMELINE", 50, 580, CYAN, self.small)
        events = self.containment_timeline[-5:]
        if events:
            pygame.draw.line(self.canvas, LINE, (78, 605), (78, 693), 1)
            for index, event in enumerate(events):
                y = 600 + index * 19
                color = marker_colors.get(event["kind"], MUTED)
                pygame.draw.circle(self.canvas, color, (78, y + 5), 3)
                elapsed = int(event["time"])
                self._text(self.canvas, f"T+{elapsed // 60:02}:{elapsed % 60:02}", 94, y, MUTED, self.small)
                self._text(self.canvas, event["label"].upper(), 170, y, TEXT, self.small)
                self._text(self.canvas, event["detail"][:105], 390, y, MUTED, self.small)
        else:
            self._text(self.canvas, "No containment events recorded.", 94, 606, MUTED, self.small)
        self._button(self.canvas, "restart_scenario", pygame.Rect(915, 710, 210, 62), "RESTART SCENARIO", accent=LIME, subtitle="SAME CITY AND SETTINGS")
        self._button(self.canvas, "change_parameters", pygame.Rect(1140, 710, 175, 62), "NEW OPERATION", accent=CYAN, subtitle="CHANGE PARAMETERS")

    def _screen_position(self, event: pygame.event.Event) -> tuple[int, int]:
        width, height = self.screen.get_size()
        scale = min(width / WIDTH, height / HEIGHT)
        offset_x, offset_y = (width - WIDTH * scale) / 2, (height - HEIGHT * scale) / 2
        return (int((event.pos[0] - offset_x) / scale), int((event.pos[1] - offset_y) / scale))

    def _pay(self, amount: int) -> bool:
        if self.budget < amount:
            self._notify("INSUFFICIENT FUNDS · HOLD YOUR RESERVES")
            return False
        self.budget -= amount
        return True

    def _snap_barricade_anchor(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        nearest = point
        nearest_distance = 48.0 ** 2
        for polygon in self.city.buildings:
            vertices = [(px * WORLD_W, py * WORLD_H) for px, py in polygon]
            for start, end in zip(vertices, vertices[1:] + vertices[:1]):
                dx, dy = end[0] - start[0], end[1] - start[1]
                length_squared = dx * dx + dy * dy
                fraction = 0.0 if not length_squared else max(0.0, min(1.0, ((x - start[0]) * dx + (y - start[1]) * dy) / length_squared))
                candidate = (start[0] + fraction * dx, start[1] + fraction * dy)
                distance = (candidate[0] - x) ** 2 + (candidate[1] - y) ** 2
                if distance < nearest_distance:
                    nearest, nearest_distance = candidate, distance
        for unit in self.units:
            if unit["kind"] != "BARRICADE":
                continue
            distance = (unit["x"] - x) ** 2 + (unit["y"] - y) ** 2
            if distance < nearest_distance:
                nearest, nearest_distance = (unit["x"], unit["y"]), distance
        return nearest

    def _deploy_barricade_line(self, start: tuple[float, float], end: tuple[float, float]) -> int:
        start = self._snap_barricade_anchor(start)
        end = self._snap_barricade_anchor(end)
        length = math.dist(start, end)
        count = max(1, math.ceil(length / 48.0) + (1 if length >= 18.0 else 0))
        positions = [
            (start[0] + (end[0] - start[0]) * index / max(1, count - 1),
             start[1] + (end[1] - start[1]) * index / max(1, count - 1))
            for index in range(count)
        ]
        cost = UNITS["BARRICADE"]["cost"] * count
        if not self._pay(cost):
            return 0
        for x, y in positions:
            self.units.append({"kind": "BARRICADE", "x": x, "y": y, "range": 0, "timer": 0.0})
        self.route_refresh = 0.0
        self.evac_route_refresh = 0.0
        self._notify(f"BARRICADE LINE DEPLOYED · {count} POSTS · ${cost} COMMITTED")
        return count

    def _finish_barricade_drag(self, point: tuple[int, int]) -> None:
        start = self.barricade_drag_start
        self.barricade_drag_start = None
        if start is None or not self._map_content_rect().collidepoint(point):
            return
        self._deploy_barricade_line(start, self._world_point(point))
        self.target_mode = None

    def _deploy(self, point: tuple[int, int]) -> None:
        if not self._map_content_rect().collidepoint(point):
            return
        x, y = self._world_point(point)
        kind = self.selected_unit
        cost = UNITS[kind]["cost"]
        if not self._pay(cost):
            return
        cooldown = {"timer": random.random() * 0.4}
        unit = {"kind": kind, "x": x, "y": y, "range": UNITS[kind]["range"], **cooldown}
        if kind == "EVAC STATION":
            unit["route_node"] = int(self._snap_agents_to_roads(np.array([x]), np.array([y]))[0])
        elif kind == "SAFE ZONE":
            unit["route_node"] = int(self._snap_agents_to_roads(np.array([x]), np.array([y]))[0])
            unit["capacity_agents"] = max(1, math.ceil(6000 / self.people_per_agent))
            unit["safe_agents"] = 0
        elif kind == "SUPPLY DROP":
            unit["delivers_at"] = self.simulation_time + 8.0
            unit["delivered"] = False
        self.units.append(unit)
        if kind == "BARRICADE":
            self.route_refresh = 0.0
            self.evac_route_refresh = 0.0
        elif kind == "EVAC STATION":
            if self.emergency_policy == "EVACUATE":
                self._set_emergency_policy(self.emergency_policy)
            else:
                self.evac_route_refresh = 0.0
        elif kind == "SAFE ZONE" and self.emergency_policy == "SHELTER":
            self._set_emergency_policy(self.emergency_policy)
        elif kind == "SAFE ZONE":
            self.evac_route_refresh = 0.0
        self._notify(f"{kind} DEPLOYED · ${cost} COMMITTED")

    def _air_support(self, point: tuple[int, int]) -> None:
        x, y = self._world_point(point)
        if self.target_mode == "strike":
            cost = 180
            if not self._pay(cost):
                return
            radius = 60.0
            killed = self._damage_zombies(x, y, radius, 115)
            civilian_range = 28.0
            lost = ((self.civilians["x"] - x) ** 2 + (self.civilians["y"] - y) ** 2 < civilian_range ** 2)
            lost_population = int(self.civilians["population"][lost].sum())
            self.casualties += lost_population
            self.strike_casualties += lost_population
            self.civilians = {key: values[~lost] for key, values in self.civilians.items()}
            self.effects.append({"x": x, "y": y, "radius": radius, "duration": 1.1, "age": 0, "kind": "strike"})
            self._notify(f"BOMBER STRIKE · {killed:,} HOSTILES · {lost_population:,} CIVILIAN CASUALTIES", 4.0)
        elif self.target_mode == "gunship":
            cost = 290
            if not self._pay(cost):
                return
            self.gunship = 10.0
            self.gunship_target = (x, y)
            self.effects.append({"x": x, "y": y, "radius": 92, "duration": 10, "age": 0, "kind": "gunship"})
            self._notify("GUNSHIP ON STATION · TEN-SECOND STRAFING RUN", 4.0)
        self.target_mode = None

    def _remove_dead_zombies(self) -> int:
        alive = self.zombies["hp"] > 0
        dead = ~alive
        killed = int(np.count_nonzero(dead))
        if killed:
            self._eliminated += killed
            self._eliminated_population += int(self.zombies["population"][dead].sum())
            self.zombies = {key: values[alive] for key, values in self.zombies.items()}
        return killed

    def _damage_zombies(self, x: float, y: float, radius: float, damage: float) -> int:
        within = (self.zombies["x"] - x) ** 2 + (self.zombies["y"] - y) ** 2 <= radius ** 2
        self.zombies["hp"][within] -= damage
        return self._remove_dead_zombies()

    def _population_ledger(self) -> dict[str, int]:
        civilian_weights = self.civilians.get("population", np.ones(len(self.civilians.get("x", ())), dtype=np.int64))
        civilian_status = self.civilians.get("status", np.zeros(len(civilian_weights), dtype=np.uint8))
        zombie_weights = self.zombies.get("population", np.ones(len(self.zombies.get("x", ())), dtype=np.int64))
        ledger = {
            "starting": self.represented_population,
            "uninfected": int(civilian_weights[civilian_status == 0].sum()),
            "exposed": int(civilian_weights[civilian_status == 1].sum()),
            "infected": int(zombie_weights.sum()),
            "rescued": self.rescued,
            "dead": self.strike_casualties + getattr(self, "_eliminated_population", 0),
        }
        ledger["accounted"] = sum(value for key, value in ledger.items() if key != "starting")
        return ledger

    def _update_simulation_status(self) -> None:
        total = max(1, self.represented_population)
        lost_or_rescued = self.rescued + self.casualties
        remaining = max(0, total - lost_or_rescued)
        zombies = len(self.zombies["x"])
        previous_status = self.simulation_status
        if remaining <= max(1, round(total * 0.02)):
            if self.rescued >= self.casualties:
                self.simulation_status = "Evacuated"
                self.collapse_cause = f"Evacuation completed with {self.rescued:,} residents rescued."
                if previous_status != "Evacuated":
                    self._record_containment_event(
                        "Evacuation complete",
                        self.collapse_cause,
                        "evacuated",
                    )
                self.end_reason = "EVACUATION COMPLETE"
                self.screen_name = "analysis"
                return
            self.simulation_status = "Collapsed"
            if self.infection_casualties >= self.strike_casualties:
                self.collapse_cause = "Infection and exposure exhausted the remaining population."
            else:
                self.collapse_cause = "Civilian casualties from air support exceeded infection losses."
            if previous_status != "Collapsed":
                self._record_containment_event(
                    "Containment collapsed",
                    self.collapse_cause,
                    "collapsed",
                    self._population_hotspots(),
                )
            if self.auto_collapse:
                self.end_reason = "POPULATION COLLAPSE"
                self.screen_name = "analysis"
            return
        if zombies == 0 and self.infected_count == 0:
            next_status = "Contained"
        elif lost_or_rescued / total >= 0.65 or zombies >= max(1, self.population_agents // 3):
            next_status = "Critical"
        else:
            next_status = "Spreading"
        self.simulation_status = next_status
        if next_status != previous_status and next_status in ("Contained", "Critical"):
            event_kind = next_status.lower()
            if not any(event["kind"] == event_kind for event in self.containment_timeline):
                self._record_containment_event(
                    f"Containment {next_status.lower()}",
                    f"{self._population_ledger()['infected']:,} residents infected",
                    event_kind,
                    self._population_hotspots(),
                )

    def _stop_simulation(self) -> None:
        if self.screen_name != "active":
            return
        self.end_reason = "MANUAL STOP"
        self._update_simulation_status()
        self._record_containment_event("Simulation stopped", self.simulation_status, "stopped")
        if self.screen_name == "active":
            self.screen_name = "analysis"

    def _start_simulation(self) -> None:
        self.screen_name = "active"
        self.simulation_status = "Spreading"
        self.end_reason = ""
        self.target_mode = None
        self.barricade_drag_start = None
        self.phase_time = 0.0
        self._eliminated = 0
        self._eliminated_population = 0
        self.simulation_time = 0.0
        if self.emergency_policy == "EVACUATE" and any(unit["kind"] == "EVAC STATION" for unit in self.units):
            self._set_emergency_policy(self.emergency_policy)
        elif self.emergency_policy == "SHELTER" and any(unit["kind"] == "SAFE ZONE" for unit in self.units):
            self._set_emergency_policy(self.emergency_policy)
        self._notify("SIMULATION RUNNING · STOP AT ANY TIME WITH THE HEADER CONTROL", 4.0)

    def _update(self, dt: float) -> None:
        self.message_timer = max(0, self.message_timer - dt)
        if self.loading:
            try:
                city = self.worker.results.get_nowait()
                self.loading = False
                self.city = city
                self.city_surface_cache.clear()
                self.screen_name = "area_select"
                self.area_selection = (0.5, 0.5)
                self.area_view_center = (0.5, 0.5)
                self.area_zoom = 1.3
                self._limit_area_selection()
                self._notify("MAP READY · CLICK THE CENTER OF YOUR OPERATION AREA", 6.0)
            except queue.Empty:
                pass
        if self.screen_name != "active" or self.phase_time < 0:
            return
        dt = min(dt, 0.05)
        self.simulation_time += dt
        for supply in (unit for unit in self.units if unit["kind"] == "SUPPLY DROP" and not unit.get("delivered")):
            if self.simulation_time >= supply["delivers_at"]:
                supply["delivered"] = True
                self.budget += 400
                self.supply_deliveries += 1
                self._notify("SUPPLY DROP RECEIVED · +$400 FIELD RESOURCES")
        self._update_crowd(dt)
        killed = []
        for unit in self.units:
            if unit["kind"] not in ("SNIPER", "SENTRY", "MORTAR", "RIFLE SQUAD"):
                continue
            unit["timer"] -= dt
            if unit["timer"] > 0:
                continue
            config = UNITS[unit["kind"]]
            bonus = 1.0
            distance_squared = (self.zombies["x"] - unit["x"]) ** 2 + (self.zombies["y"] - unit["y"]) ** 2
            valid = distance_squared <= config["range"] ** 2
            if not np.any(valid):
                continue
            target_index = int(np.argmin(np.where(valid, distance_squared, np.inf)))
            target_x, target_y = self.zombies["x"][target_index], self.zombies["y"][target_index]
            self.zombies["hp"][target_index] -= config["damage"] * bonus
            unit["timer"] = config["rate"]
            self.effects.append({"x": float(target_x), "y": float(target_y), "radius": 14, "duration": 0.25, "age": 0, "kind": "shot"})
            if unit["kind"] == "MORTAR":
                splash = (self.zombies["x"] - target_x) ** 2 + (self.zombies["y"] - target_y) ** 2 < 48 ** 2
                self.zombies["hp"][splash] -= 19 * bonus
        if self.gunship > 0:
            self.gunship = max(0, self.gunship - dt)
            self.phase_time += dt
            if int(self.phase_time * 5) != int((self.phase_time - dt) * 5):
                gx, gy = self.gunship_target
                distance_squared = (self.zombies["x"] - gx) ** 2 + (self.zombies["y"] - gy) ** 2
                valid = distance_squared <= 92 ** 2
                if np.any(valid):
                    target_index = int(np.argmin(np.where(valid, distance_squared, np.inf)))
                    self.zombies["hp"][target_index] -= 11
        self._remove_dead_zombies()
        for effect in self.effects:
            effect["age"] += dt
        self.effects = [e for e in self.effects if e["age"] < e["duration"]]
        self._update_simulation_status()

    def _update_crowd(self, dt: float) -> None:
        """Move agents in batches and spread infection through a local contact grid."""
        crowd = self.civilians
        count = len(crowd["x"])
        if count == 0 and len(self.zombies["x"]) == 0:
            self.infected_count = 0
            return

        self._update_safe_zones(crowd)
        crowd["wander"] -= dt
        turning = crowd["wander"] <= 0
        turning_count = int(np.count_nonzero(turning))
        if turning_count:
            crowd["wander"][turning] = np.random.uniform(1.2, 4.0, turning_count)
            crowd["dx"][turning] = np.random.uniform(-1, 1, turning_count)
            crowd["dy"][turning] = np.random.uniform(-1, 1, turning_count)
        wandering = (~crowd["evacuating"]) & (~crowd["safe"])
        crowd["x"][wandering] = np.clip(crowd["x"][wandering] + crowd["dx"][wandering] * (4.0 * dt), 8, WORLD_W - 8)
        crowd["y"][wandering] = np.clip(crowd["y"][wandering] + crowd["dy"][wandering] * (4.0 * dt), 8, WORLD_H - 8)
        self._move_evacuating(dt)
        crowd = self.civilians
        count = len(crowd["x"])
        self._update_safe_zones(crowd)

        grid_w, grid_h = GRID_W, GRID_H
        cell_x = np.clip((crowd["x"] * (grid_w / WORLD_W)).astype(np.int32), 0, grid_w - 1)
        cell_y = np.clip((crowd["y"] * (grid_h / WORLD_H)).astype(np.int32), 0, grid_h - 1)
        cell_count = grid_w * grid_h
        occupancy = np.bincount(cell_y * grid_w + cell_x, weights=(~crowd["safe"]).astype(np.float32), minlength=cell_count).reshape(grid_h, grid_w)
        self.route_refresh -= dt
        if self.route_refresh <= 0:
            self._rebuild_road_routes(occupancy)
            self.route_refresh = 2.0
        zombies = self.zombies
        zombie_count = len(zombies["x"])
        if zombie_count:
            if "route_node" not in zombies or len(zombies["route_node"]) != zombie_count:
                zombies["route_node"] = self._snap_agents_to_roads(zombies["x"], zombies["y"])
            if "route_narrow" not in zombies or len(zombies["route_narrow"]) != zombie_count:
                zombies["route_narrow"] = np.zeros(zombie_count, dtype=bool)
            route_nodes = zombies["route_node"]
            route_narrow = zombies["route_narrow"]
            waypoint_x = self.road_nodes[route_nodes, 0]
            waypoint_y = self.road_nodes[route_nodes, 1]
            at_waypoint = (zombies["x"] - waypoint_x) ** 2 + (zombies["y"] - waypoint_y) ** 2 < 10 ** 2
            next_waypoint = self.road_next_nodes[route_nodes]
            advance = at_waypoint & (next_waypoint >= 0)
            route_narrow[advance] = self.road_next_narrow[route_nodes[advance]]
            route_nodes[advance] = next_waypoint[advance]
            waypoint_x = self.road_nodes[route_nodes, 0]
            waypoint_y = self.road_nodes[route_nodes, 1]
            destination_x, destination_y = waypoint_x, waypoint_y
            terminal = self.road_next_nodes[route_nodes] < 0
            for goal_node, (goal_x, goal_y) in self.road_goal_positions.items():
                leaving_road = terminal & (route_nodes == goal_node)
                destination_x[leaving_road] = goal_x
                destination_y[leaving_road] = goal_y
            direction_x = destination_x - zombies["x"]
            direction_y = destination_y - zombies["y"]
            distance = np.maximum(1, np.hypot(direction_x, direction_y))
            speed_by_behavior = {"BALANCED": 42.0, "RUNNER": 62.0, "SWARM": 31.0}
            self._refresh_barricade_caches()
            speed = np.full(zombie_count, speed_by_behavior.get(self.zombie_behavior, 42.0), dtype=np.float32)
            speed[route_narrow] *= 0.58
            zombie_cell_x = np.clip((zombies["x"] * (grid_w / WORLD_W)).astype(np.int32), 0, grid_w - 1)
            zombie_cell_y = np.clip((zombies["y"] * (grid_h / WORLD_H)).astype(np.int32), 0, grid_h - 1)
            speed *= self._barricade_slow_grid[zombie_cell_y, zombie_cell_x]
            zombies["x"] = np.clip(zombies["x"] + direction_x / distance * speed * dt, 2, WORLD_W - 2)
            zombies["y"] = np.clip(zombies["y"] + direction_y / distance * speed * dt, 2, WORLD_H - 2)

            zombie_cell_x = np.clip((zombies["x"] * (grid_w / WORLD_W)).astype(np.int32), 0, grid_w - 1)
            zombie_cell_y = np.clip((zombies["y"] * (grid_h / WORLD_H)).astype(np.int32), 0, grid_h - 1)
            zombie_grid = np.bincount(zombie_cell_y * grid_w + zombie_cell_x, minlength=cell_count).reshape(grid_h, grid_w).astype(np.float32)
            padded = np.pad(zombie_grid, 2)
            pressure = np.zeros_like(zombie_grid)
            flee_x = np.zeros_like(zombie_grid)
            flee_y = np.zeros_like(zombie_grid)
            for offset_y in range(-2, 3):
                for offset_x in range(-2, 3):
                    weight = math.exp(-(offset_x * offset_x + offset_y * offset_y) / 2.8)
                    nearby = padded[2 + offset_y:2 + offset_y + grid_h, 2 + offset_x:2 + offset_x + grid_w] * weight
                    pressure += nearby
                    flee_x -= nearby * offset_x
                    flee_y -= nearby * offset_y
            self.evac_route_refresh -= dt
            if self.evac_route_refresh <= 0:
                self._rebuild_evac_routes(pressure)
                self.evac_route_refresh = 0.5

            route_nodes = crowd["route_node"]
            evac_next = self.evac_next_nodes[route_nodes]
            evac_goals = np.fromiter(self.evac_goal_positions, dtype=np.int32)
            route_reachable = (evac_next >= 0) | np.isin(route_nodes, evac_goals)
            following_evac_route = crowd["evacuating"] & (crowd["status"] == 0) & route_reachable
            sampled_risk = pressure[cell_y, cell_x]
            flee_direction_x = flee_x[cell_y, cell_x]
            flee_direction_y = flee_y[cell_y, cell_x]
            flee_distance = np.maximum(0.001, np.hypot(flee_direction_x, flee_direction_y))
            panic = (crowd["status"] == 0) & (~crowd["safe"]) & (~following_evac_route) & (sampled_risk > 0.2) & (flee_distance > 0.01)
            crowd["x"][panic] += flee_direction_x[panic] / flee_distance[panic] * (30.0 * dt)
            crowd["y"][panic] += flee_direction_y[panic] / flee_distance[panic] * (30.0 * dt)
            crowd["x"] = np.clip(crowd["x"], 8, WORLD_W - 8)
            crowd["y"] = np.clip(crowd["y"], 8, WORLD_H - 8)
            cell_x = np.clip((crowd["x"] * (grid_w / WORLD_W)).astype(np.int32), 0, grid_w - 1)
            cell_y = np.clip((crowd["y"] * (grid_h / WORLD_H)).astype(np.int32), 0, grid_h - 1)
            sampled_pressure = pressure[cell_y, cell_x]
            susceptible = (crowd["status"] == 0) & (~crowd["safe"])
            spread_rate = {"BALANCED": 0.14, "RUNNER": 0.11, "SWARM": 0.24}.get(self.zombie_behavior, 0.14)
            crowd["exposure"][susceptible] += np.minimum(sampled_pressure[susceptible], 4.0) * crowd["susceptibility"][susceptible] * (spread_rate * dt)
            newly_exposed = susceptible & (crowd["exposure"] >= 1.0)
            newly_exposed_count = int(np.count_nonzero(newly_exposed))
            crowd["status"][newly_exposed] = 1
            crowd["incubation"][newly_exposed] = np.random.uniform(3.5, 6.0, newly_exposed_count)
        else:
            zombie_grid = np.zeros((grid_h, grid_w), dtype=np.float32)
            pressure = np.zeros_like(zombie_grid)
            self.evac_route_refresh -= dt
            if self.evac_route_refresh <= 0:
                self._rebuild_evac_routes(pressure)
                self.evac_route_refresh = 0.5

        exposed = crowd["status"] == 1
        crowd["incubation"][exposed] -= dt
        converted = exposed & (crowd["incubation"] <= 0)
        converted_count = int(np.count_nonzero(converted))
        if converted_count:
            converted_x, converted_y = crowd["x"][converted], crowd["y"][converted]
            converted_population = int(crowd["population"][converted].sum())
            self.casualties += converted_population
            self.infection_casualties += converted_population
            keep = ~converted
            self.civilians = {key: values[keep] for key, values in crowd.items()}
            zombies = self.zombies
            self.zombies = {
                "x": np.concatenate((zombies["x"], converted_x)),
                "y": np.concatenate((zombies["y"], converted_y)),
                "hp": np.concatenate((zombies["hp"], np.full(converted_count, 40, dtype=np.float32))),
                "population": np.concatenate((zombies.get("population", np.empty(0, dtype=np.int64)), crowd["population"][converted])),
                "route_node": np.concatenate((zombies.get("route_node", np.empty(0, dtype=np.int32)), self._snap_agents_to_roads(converted_x, converted_y))),
                "route_narrow": np.concatenate((zombies.get("route_narrow", np.zeros(len(zombies["x"]), dtype=bool)), np.zeros(converted_count, dtype=bool))),
            }
        self.infected_count = int(np.count_nonzero(self.civilians["status"] == 1))
        remaining_x = np.clip((self.civilians["x"] * (grid_w / WORLD_W)).astype(np.int32), 0, grid_w - 1)
        remaining_y = np.clip((self.civilians["y"] * (grid_h / WORLD_H)).astype(np.int32), 0, grid_h - 1)
        exposed_cells = np.bincount(remaining_y * grid_w + remaining_x, weights=(self.civilians["status"] == 1), minlength=cell_count).reshape(grid_h, grid_w)
        survivor_cells = np.bincount(remaining_y * grid_w + remaining_x, weights=(self.civilians["status"] == 0), minlength=cell_count).reshape(grid_h, grid_w)
        self.intel_grids = {
            "risk": pressure,
            "horde": zombie_grid,
            "exposed": exposed_cells.astype(np.float32),
            "survivors": survivor_cells.astype(np.float32),
        }
        self.intel_refresh -= dt
        if self.intel_refresh <= 0:
            self._refresh_intelligence(self.intel_grids)
            self.intel_refresh = 0.15

    def _click(self, point: tuple[int, int], button: int) -> None:
        if button == 3:
            if self.screen_name == "area_select":
                self.area_pan_drag = True
                self.area_pan_last = point
                return
            self.target_mode = None
            self.barricade_drag_start = None
            return
        if button != 1:
            return
        if self.screen_name == "area_select":
            if self.hit_targets.get("area_radius_slider", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.dragging_radius = True
                self._set_area_radius_from_x(point[0])
            elif self.hit_targets.get("zoom_in", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.area_zoom = min(4.0, self.area_zoom * 1.25)
            elif self.hit_targets.get("zoom_out", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.area_zoom = max(1.0, self.area_zoom / 1.25)
            elif self.hit_targets.get("area_back", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.screen_name = "menu"
                self.city = self.preview_map
            elif self.hit_targets.get("area_confirm", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                selected_city = self._select_city_area(self.city, self.area_selection, self.radius_km)
                self._begin_operation(selected_city)
            elif self._area_content_rect().collidepoint(point):
                map_rect = self._area_content_rect()
                self.area_selection = (
                    self.area_view_center[0] + (point[0] - map_rect.centerx) / (map_rect.w * self.area_zoom),
                    self.area_view_center[1] + (point[1] - map_rect.centery) / (map_rect.h * self.area_zoom),
                )
                self._limit_area_selection()
        elif self.screen_name == "menu":
            if self.hit_targets.get("place_input", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.input_active = True
            elif self.hit_targets.get("radius_slider", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.dragging_radius = True
                self._set_radius_from_x(point[0])
            elif self.hit_targets.get("config", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.screen_name = "config"
            elif "build_plan" in self.hit_targets and self.hit_targets["build_plan"].collidepoint(point):
                self._request_map()
            elif "overture" in self.hit_targets and self.hit_targets["overture"].collidepoint(point):
                self.use_overture = not self.use_overture
            for percentage in (0.5, 1.0, 2.0, 5.0):
                key = f"infect_{percentage:g}%"
                if key in self.hit_targets and self.hit_targets[key].collidepoint(point):
                    self.initial_infection_percent = percentage
            for index in range(len(PRESETS)):
                key = f"preset_{index}"
                if key in self.hit_targets and self.hit_targets[key].collidepoint(point):
                    self.place_input = PRESETS[index]
                    self._request_map(PRESETS[index])
        elif self.screen_name == "config":
            if self.hit_targets.get("scenario_name", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.scenario_name_active = True
            elif self.hit_targets.get("back_config", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.scenario_name_active = False
                self.screen_name = "menu"
            elif self.hit_targets.get("save_scenario", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self._save_scenario()
            elif self.hit_targets.get("load_scenario", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self._load_selected_scenario()
            elif self.hit_targets.get("auto_collapse", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.auto_collapse = not self.auto_collapse
            for density in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
                key = f"density_{density:g}"
                if key in self.hit_targets and self.hit_targets[key].collidepoint(point):
                    self.population_density = density
            for behavior in ("BALANCED", "RUNNER", "SWARM"):
                key = f"behavior_{behavior}"
                if key in self.hit_targets and self.hit_targets[key].collidepoint(point):
                    self.zombie_behavior = behavior
            for resources in (1000, 1500, 2500, 5000):
                key = f"resources_{resources}"
                if key in self.hit_targets and self.hit_targets[key].collidepoint(point):
                    self.starting_resources = resources
            for scenario_index in range(max(0, len(self.saved_scenarios) - 6), len(self.saved_scenarios)):
                key = f"scenario_{scenario_index}"
                if key in self.hit_targets and self.hit_targets[key].collidepoint(point):
                    self.selected_scenario_index = scenario_index
        elif self.screen_name in ("deployment", "active"):
            if self.target_mode and self._map_content_rect().collidepoint(point):
                if self.target_mode == "origin" and self.screen_name == "deployment":
                    origin_x, origin_y = self._world_point(point)
                    self.infection_origin = (origin_x / WORLD_W, origin_y / WORLD_H)
                    self.patient_zero = self.infection_origin
                    count = len(self.zombies["x"])
                    angles = np.arange(count, dtype=np.float32) * (math.tau / count)
                    normalized_x, normalized_y = self.infection_origin
                    self.zombies["x"] = (normalized_x * WORLD_W + np.cos(angles) * 12).astype(np.float32)
                    self.zombies["y"] = (normalized_y * WORLD_H + np.sin(angles) * 12).astype(np.float32)
                    self.zombies["route_node"] = self._snap_agents_to_roads(self.zombies["x"], self.zombies["y"])
                    self.zombies["route_narrow"] = np.zeros(count, dtype=bool)
                    outbreak = next((event for event in self.containment_timeline if event["kind"] == "outbreak"), None)
                    if outbreak is not None:
                        outbreak["locations"] = [(normalized_x, normalized_y, self.initial_infected_population)]
                    self.target_mode = None
                    self._notify(f"PATIENT ZERO POSITIONED · {count:,} INITIAL INFECTED")
                elif self.target_mode == "move" and self.moving_unit in self.units:
                    self.moving_unit["x"], self.moving_unit["y"] = self._world_point(point)
                    if self.moving_unit["kind"] == "BARRICADE":
                        self.route_refresh = 0.0
                        self.evac_route_refresh = 0.0
                    elif self.moving_unit["kind"] == "EVAC STATION":
                        self.moving_unit["route_node"] = int(self._snap_agents_to_roads(np.array([self.moving_unit["x"]]), np.array([self.moving_unit["y"]]))[0])
                        self.civilians["route_node"] = self._snap_agents_to_roads(self.civilians["x"], self.civilians["y"])
                        self.evac_route_refresh = 0.0
                    self.target_mode = None
                    self._notify(f"{self.moving_unit['kind']} REPOSITIONED")
                elif self.target_mode == "deploy":
                    if self.selected_unit == "BARRICADE":
                        self.barricade_drag_start = self._snap_barricade_anchor(self._world_point(point))
                    else:
                        self._deploy(point)
                        self.target_mode = None
                elif self.target_mode in ("strike", "gunship"):
                    self._air_support(point)
            elif self.hit_targets.get("launch", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self._start_simulation()
            elif self.hit_targets.get("stop_simulation", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self._stop_simulation()
            elif "strike" in self.hit_targets and self.hit_targets["strike"].collidepoint(point):
                if self.screen_name == "active":
                    self.target_mode = "strike"
                else:
                    self._notify("AIR SUPPORT AVAILABLE WHEN THE OPERATION BEGINS")
            elif "gunship" in self.hit_targets and self.hit_targets["gunship"].collidepoint(point):
                if self.screen_name == "active":
                    self.target_mode = "gunship"
                else:
                    self._notify("AIR SUPPORT AVAILABLE WHEN THE OPERATION BEGINS")
            elif "origin" in self.hit_targets and self.hit_targets["origin"].collidepoint(point):
                if self.screen_name == "deployment":
                    self.target_mode = "origin"
                else:
                    self._notify("OUTBREAK ORIGIN IS LOCKED AFTER THE OPERATION BEGINS")
            elif any(key in self.hit_targets and self.hit_targets[key].collidepoint(point) for key in ("policy_EVACUATE", "policy_SHELTER", "policy_FLEE")):
                for policy in ("EVACUATE", "SHELTER", "FLEE"):
                    key = f"policy_{policy}"
                    if key in self.hit_targets and self.hit_targets[key].collidepoint(point):
                        self._set_emergency_policy(policy)
                        break
            elif any(key in self.hit_targets and self.hit_targets[key].collidepoint(point) for key in ("intel_risk", "intel_horde", "intel_exposed", "intel_survivors")):
                for mode in self.intel_modes:
                    key = f"intel_{mode}"
                    if key in self.hit_targets and self.hit_targets[key].collidepoint(point):
                        self.intel_modes[mode] = not self.intel_modes[mode]
                        self._refresh_intelligence(self.intel_grids)
                        break
            else:
                selected = False
                for kind in UNITS:
                    key = f"unit_{kind}"
                    if key in self.hit_targets and self.hit_targets[key].collidepoint(point):
                        self.selected_unit = kind
                        self.target_mode = "deploy"
                        self.barricade_drag_start = None
                        self._notify(f"{kind} PREVIEW · CLICK MAP TO PLACE" if kind != "BARRICADE" else "BARRICADE PREVIEW · CLICK OR DRAG A WALL")
                        selected = True
                        break
                if not selected and self._map_content_rect().collidepoint(point):
                    asset = None
                    if self.screen_name == "active":
                        for unit in reversed(self.units):
                            asset_point = self._map_point(unit["x"] / WORLD_W, unit["y"] / WORLD_H)
                            if math.hypot(point[0] - asset_point[0], point[1] - asset_point[1]) <= 13:
                                asset = unit
                                break
                    if asset:
                        self.moving_unit = asset
                        self.target_mode = "move"
                        self._notify(f"{asset['kind']} SELECTED · CLICK MAP TO REPOSITION")
                    else:
                        self.target_mode = "deploy"
                        self._notify(f"{self.selected_unit} PREVIEW · CLICK MAP TO PLACE" if self.selected_unit != "BARRICADE" else "BARRICADE PREVIEW · CLICK OR DRAG A WALL")
                elif self.target_mode and not selected:
                    self.target_mode = None
        elif self.screen_name == "analysis":
            if self.hit_targets.get("restart_scenario", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self._begin_operation(self.city)
            elif self.hit_targets.get("change_parameters", pygame.Rect(0, 0, 0, 0)).collidepoint(point):
                self.screen_name = "menu"
                self.input_active = False

    def _handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.alive = False
        elif event.type == pygame.MOUSEMOTION:
            self.mouse = self._screen_position(event)
            if self.screen_name == "menu" and getattr(self, "dragging_radius", False) and getattr(event, "buttons", (False,))[0]:
                self._set_radius_from_x(self.mouse[0])
            elif self.screen_name == "area_select" and self.dragging_radius and getattr(event, "buttons", (False,))[0]:
                self._set_area_radius_from_x(self.mouse[0])
            if self.screen_name == "area_select" and self.area_pan_drag and getattr(event, "buttons", (False, False, False))[2]:
                dx = self.mouse[0] - self.area_pan_last[0]
                dy = self.mouse[1] - self.area_pan_last[1]
                map_rect = self._area_content_rect()
                self.area_view_center = (
                    max(0.5 / self.area_zoom, min(1.0 - 0.5 / self.area_zoom, self.area_view_center[0] - dx / (map_rect.w * self.area_zoom))),
                    max(0.5 / self.area_zoom, min(1.0 - 0.5 / self.area_zoom, self.area_view_center[1] - dy / (map_rect.h * self.area_zoom))),
                )
                self.area_pan_last = self.mouse
        elif event.type == pygame.MOUSEBUTTONDOWN:
            point = self._screen_position(event)
            self.mouse = point
            self._click(point, event.button)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.mouse = self._screen_position(event)
            self.dragging_radius = False
            if self.barricade_drag_start is not None:
                self._finish_barricade_drag(self.mouse)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 3:
            self.area_pan_drag = False
        elif event.type == pygame.MOUSEWHEEL and self.screen_name == "area_select":
            if self._area_content_rect().collidepoint(self.mouse):
                self._zoom_area(1.2 ** event.y, self.mouse)
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_F10:
                self.resolution_1080 = not self.resolution_1080
                if not self.fullscreen:
                    size = (1920, 1080) if self.resolution_1080 else self.windowed_size
                    self.screen = pygame.display.set_mode(size, pygame.RESIZABLE)
                return
            if event.key == pygame.K_F11:
                if self.fullscreen:
                    size = (1920, 1080) if self.resolution_1080 else self.windowed_size
                    self.screen = pygame.display.set_mode(size, pygame.RESIZABLE)
                else:
                    self.windowed_size = self.screen.get_size()
                    self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                self.fullscreen = not self.fullscreen
                return
            if self.screen_name == "config" and self.scenario_name_active:
                if event.key == pygame.K_RETURN:
                    self.scenario_name_active = False
                elif event.key == pygame.K_BACKSPACE:
                    self.scenario_name = self.scenario_name[:-1]
                elif event.unicode and event.unicode.isprintable() and len(self.scenario_name) < 48:
                    self.scenario_name += event.unicode
            elif self.screen_name == "menu" and self.input_active:
                if event.key == pygame.K_RETURN:
                    self._request_map()
                elif event.key == pygame.K_BACKSPACE:
                    self.place_input = self.place_input[:-1]
                elif event.unicode and event.unicode.isprintable() and len(self.place_input) < 90:
                    self.place_input += event.unicode
            elif self.screen_name == "area_select":
                if event.key == pygame.K_ESCAPE:
                    self.screen_name = "menu"
                    self.city = self.preview_map
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                    self._zoom_area(1.2, self._area_content_rect().center)
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    self._zoom_area(1 / 1.2, self._area_content_rect().center)
            elif self.screen_name in ("deployment", "active"):
                if event.key == pygame.K_ESCAPE:
                    self.target_mode = None
                    self.barricade_drag_start = None
                elif event.key == pygame.K_SPACE and self.screen_name == "active":
                    self.phase_time = -1 if self.phase_time >= 0 else 0
                elif pygame.K_1 <= event.key <= pygame.K_6 and self.screen_name == "deployment":
                    self.selected_unit = list(UNITS)[event.key - pygame.K_1]
                    self.target_mode = "deploy"
                elif pygame.K_F1 <= event.key <= pygame.K_F4:
                    mode = ("risk", "horde", "exposed", "survivors")[event.key - pygame.K_F1]
                    self.intel_modes[mode] = not self.intel_modes[mode]
                    self._refresh_intelligence(self.intel_grids)

    def _zoom_area(self, factor: float, anchor: tuple[int, int]) -> None:
        old_zoom = self.area_zoom
        map_rect = self._area_content_rect()
        offset_x = (anchor[0] - map_rect.centerx) / map_rect.w
        offset_y = (anchor[1] - map_rect.centery) / map_rect.h
        anchor_x = self.area_view_center[0] + offset_x / old_zoom
        anchor_y = self.area_view_center[1] + offset_y / old_zoom
        self.area_zoom = max(1.0, min(4.0, old_zoom * factor))
        if self.area_zoom != old_zoom:
            self.area_view_center = (
                max(0.5 / self.area_zoom, min(1.0 - 0.5 / self.area_zoom, anchor_x - offset_x / self.area_zoom)),
                max(0.5 / self.area_zoom, min(1.0 - 0.5 / self.area_zoom, anchor_y - offset_y / self.area_zoom)),
            )

    def _set_radius_from_x(self, x: int) -> None:
        slider = self.hit_targets.get("radius_slider")
        if not slider:
            return
        fraction = max(0.0, min(1.0, (x - slider.x) / slider.w))
        self.radius_km = 0.35 + fraction * (2.0 - 0.35)

    def _set_area_radius_from_x(self, x: int) -> None:
        slider = self.hit_targets.get("area_radius_slider")
        if not slider:
            return
        fraction = max(0.0, min(1.0, (x - slider.x) / slider.w))
        self.radius_km = 0.35 + fraction * (2.0 - 0.35)
        self._limit_area_selection()

    def _render(self) -> None:
        self.hit_targets = {}
        if self.screen_name == "menu":
            self._draw_menu()
        elif self.screen_name == "area_select":
            self._draw_area_select()
        elif self.screen_name == "config":
            self._draw_config()
        elif self.screen_name == "deployment":
            self._draw_operation(False)
        elif self.screen_name == "active":
            self._draw_operation(True)
        elif self.screen_name == "analysis":
            self._draw_analysis()
        width, height = self.screen.get_size()
        scale = min(width / WIDTH, height / HEIGHT)
        target_w, target_h = int(WIDTH * scale), int(HEIGHT * scale)
        self.screen.fill((5, 9, 10))
        destination = ((width - target_w) // 2, (height - target_h) // 2)
        if (target_w, target_h) == self.canvas.get_size():
            self.screen.blit(self.canvas, destination)
        else:
            frame = pygame.transform.scale(self.canvas, (target_w, target_h))
            self.screen.blit(frame, destination)
        pygame.display.flip()

    def run(self) -> None:
        while self.alive:
            dt = self.clock.tick(60) / 1000.0
            for event in pygame.event.get():
                self._handle_event(event)
            self._update(dt)
            self._render()
        pygame.quit()


def self_test() -> None:
    """Small offline check for map generation, deployment, and combat simulation."""
    import tempfile
    from unittest.mock import Mock, patch

    pygame.init()
    city = schematic_map("Test City Blocks", 7)
    city.population = 30_000
    assert len(city.roads) >= 10 and len(city.buildings) >= 20
    assert len(city.buildings) == len(city.building_weights)
    layer_test = Deadgrid()
    layer_surface = pygame.Surface((WIDTH, HEIGHT))
    fitted = _fit_rect(pygame.Rect(0, 0, 800, 600), 1.0)
    assert fitted.w == fitted.h and fitted.center == (400, 300)
    fitted_city = CityMap("Aspect test", map_aspect_ratio=0.7)
    aspect_game = Deadgrid()
    aspect_game.city = fitted_city
    assert math.isclose(aspect_game._map_content_rect().w / aspect_game._map_content_rect().h, 0.7, rel_tol=0.01)
    assert np.allclose(aspect_game._world_point(aspect_game._map_point(0.31, 0.73)), (310, 511), atol=3)
    ring = _stitch_polygon_rings([
        [(0.1, 0.1), (0.9, 0.1)],
        [(0.9, 0.9), (0.9, 0.1)],
        [(0.1, 0.9), (0.9, 0.9)],
        [(0.1, 0.9), (0.1, 0.1)],
    ])
    assert len(ring) == 1 and ring[0][0] == ring[0][-1]

    def road_pixel_width(physical_width: float, map_width_km: float) -> int:
        road_test = Deadgrid()
        road_test.city = CityMap(
            "Road scale test",
            roads=[[(0.1, 0.5), (0.9, 0.5)]],
            road_types=["residential"],
            road_widths_m=[physical_width],
            map_width_km=map_width_km,
            map_height_km=map_width_km,
        )
        road_surface = pygame.Surface((WIDTH, HEIGHT))
        road_test._draw_city(road_surface, MAP_RECT, road_test.city)
        sample_x, sample_y = road_test._map_point(0.5, 0.5)
        return sum(road_surface.get_at((sample_x, y))[:3] == (224, 225, 204) for y in range(sample_y - 12, sample_y + 13))

    assert road_pixel_width(15, 2) > road_pixel_width(5, 2) > road_pixel_width(5, 4)
    layer_city = CityMap(
        "Layer order test",
        roads=[[(0.1, 0.5), (0.9, 0.5)]],
        road_types=["primary"],
        buildings=[[(0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)]],
        water=[[(0.05, 0.05), (0.2, 0.05), (0.2, 0.2), (0.05, 0.2), (0.05, 0.05)]],
        waterways=[[(0.1, 0.75), (0.5, 0.65), (0.9, 0.75)]],
        building_types=["civic"],
    )
    layer_test._draw_city(layer_surface, MAP_RECT, layer_city)
    road_pixel = layer_surface.get_at(layer_test._map_point(0.8, 0.5))[:3]
    building_pixel = layer_surface.get_at(layer_test._map_point(0.34, 0.34))[:3]
    road_building_crossing = layer_surface.get_at(layer_test._map_point(0.5, 0.5))[:3]
    water_pixel = layer_surface.get_at(layer_test._map_point(0.1, 0.1))[:3]
    assert road_pixel in {(107, 122, 119), (211, 210, 185), (169, 151, 108)}
    assert building_pixel == (224, 181, 158) and road_building_crossing == building_pixel
    assert water_pixel == (91, 153, 166)
    assert layer_surface.get_at(layer_test._map_point(0.5, 0.75))[:3] == MAP_BASE
    layer_test.intel_modes = {mode: True for mode in ("risk", "horde", "exposed", "survivors")}
    layer_test.intel_layers = {mode: pygame.Surface(layer_test._map_content_rect().size, pygame.SRCALPHA) for mode in layer_test.intel_modes}
    assert len(layer_test._draw_intelligence(layer_surface)) == 4

    crop_game = Deadgrid()
    clipped_waterway = crop_game._clip_line([(-0.5, 0.5), (1.5, 0.5)])
    assert len(clipped_waterway) == 1 and clipped_waterway[0] == [(0.0, 0.5), (1.0, 0.5)]
    geocode_response = Mock()
    geocode_response.json.return_value = [{
        "lat": "40.0",
        "lon": "-74.0",
        "name": "Water Test",
        "boundingbox": ["39.9", "40.1", "-74.1", "-73.9"],
        "extratags": {},
    }]
    relation_response = Mock()
    relation_response.json.return_value = {"elements": [{
        "type": "relation",
        "tags": {"natural": "water", "type": "multipolygon"},
        "members": [
            {"type": "way", "role": "outer", "geometry": [{"lon": -74.005, "lat": 40.005}, {"lon": -73.995, "lat": 40.005}]},
            {"type": "way", "role": "outer", "geometry": [{"lon": -73.995, "lat": 39.995}, {"lon": -73.995, "lat": 40.005}]},
            {"type": "way", "role": "outer", "geometry": [{"lon": -74.005, "lat": 39.995}, {"lon": -73.995, "lat": 39.995}]},
            {"type": "way", "role": "outer", "geometry": [{"lon": -74.005, "lat": 40.005}, {"lon": -74.005, "lat": 39.995}]},
            {"type": "way", "role": "inner", "geometry": [{"lon": -74.001, "lat": 40.001}, {"lon": -74.0, "lat": 40.001}]},
        ],
    }, {
        "type": "way",
        "tags": {"natural": "coastline"},
        "geometry": [{"lon": -74.05, "lat": 40.0}, {"lon": -73.95, "lat": 40.0}],
    }, {
        "type": "way",
        "tags": {"waterway": "river"},
        "geometry": [{"lon": -74.0, "lat": 40.05}, {"lon": -74.0, "lat": 39.95}],
    }, {
        "type": "way",
        "tags": {"highway": "primary", "width": "18"},
        "geometry": [{"lon": -74.01, "lat": 40.0}, {"lon": -73.99, "lat": 40.0}],
    }]}
    with patch("requests.get", return_value=geocode_response), patch("requests.post", return_value=relation_response) as overpass_request:
        fetched_water = load_city("Water Test", use_overture=False, radius_km=1.0, preview_radius_km=3.0)
    overpass_query = overpass_request.call_args.kwargs["data"]["data"]
    assert 'relation["natural"="water"]' in overpass_query and 'way["natural"="coastline"]' in overpass_query
    assert len(fetched_water.water) == 1 and fetched_water.water[0][0] == fetched_water.water[0][-1]
    assert len(fetched_water.waterways) == 2
    assert fetched_water.road_widths_m == [18.0]
    cropped_water = crop_game._select_city_area(fetched_water, (0.5, 0.5), 1.0)
    assert cropped_water.waterways and all(line[0] != line[-1] for line in cropped_water.waterways)
    assert cropped_water.road_widths_m == [18.0]
    geo_preview = CityMap(
        "Geographic crop test",
        roads=[[(0.0, 0.5), (1.0, 0.5)]],
        road_types=["primary"],
        buildings=[[(0.46, 0.46), (0.54, 0.46), (0.54, 0.54), (0.46, 0.54)]],
        building_weights=[4.0],
        building_types=["civic"],
        query_bounds=(-0.05, -0.05, 0.05, 0.05),
        place_bounds=(-0.1, -0.1, 0.1, 0.1),
        place_population=100_000,
        preview_radius_km=5.5,
    )
    cropped_geo = crop_game._select_city_area(geo_preview, (0.5, 0.5), 1.0)
    assert len(cropped_geo.roads) == 1 and cropped_geo.roads[0][0][0] == 0.0 and cropped_geo.roads[0][-1][0] == 1.0
    assert len(cropped_geo.buildings) == 1 and cropped_geo.center == (0.0, 0.0)
    assert cropped_geo.population > 0 and all(0 <= x <= 1 and 0 <= y <= 1 for x, y in cropped_geo.buildings[0])

    offline_preview = schematic_map("Offline area selection test", 5)
    offline_preview.preview_radius_km = 4.0
    offline_crop = crop_game._select_city_area(offline_preview, (0.5, 0.5), 1.0)
    assert offline_crop.roads and offline_crop.buildings
    assert math.isclose(offline_crop.map_aspect_ratio, 1.0)
    assert all(0 <= x <= 1 and 0 <= y <= 1 for road in offline_crop.roads for x, y in road)

    menu_game = Deadgrid()
    assert menu_game.screen.get_size() == (NATIVE_WIDTH, NATIVE_HEIGHT)
    menu_game._render()
    assert not menu_game.hit_targets["build_plan"].colliderect(menu_game.hit_targets["config"])

    selection_game = Deadgrid()
    selection_game.worker.results.put(offline_preview)
    selection_game.loading = True
    selection_game._update(0)
    assert selection_game.screen_name == "area_select" and math.isclose(selection_game.area_zoom, 1.3)
    selection_game._render()
    area_map_surface = selection_game.area_map_surface
    assert area_map_surface is not None
    radius_slider = selection_game.hit_targets["area_radius_slider"]
    selection_game._click((radius_slider.x, radius_slider.centery), 1)
    assert math.isclose(selection_game.radius_km, 0.35)
    selection_game._click((radius_slider.right - 1, radius_slider.centery), 1)
    assert selection_game.radius_km > 1.99
    area_content = selection_game._area_content_rect()
    selection_game._click((area_content.centerx + 80, area_content.centery), 1)
    selection_game._render()
    assert selection_game.area_map_surface is area_map_surface
    area_content = selection_game._area_content_rect()
    selection_game.mouse = (area_content.centerx + 180, area_content.centery + 90)
    zoom_offset = ((selection_game.mouse[0] - area_content.centerx) / area_content.w, (selection_game.mouse[1] - area_content.centery) / area_content.h)
    map_anchor_before = (selection_game.area_view_center[0] + zoom_offset[0] / selection_game.area_zoom, selection_game.area_view_center[1] + zoom_offset[1] / selection_game.area_zoom)
    selection_game._handle_event(pygame.event.Event(pygame.MOUSEWHEEL, {"x": 0, "y": 1}))
    map_anchor_after = (selection_game.area_view_center[0] + zoom_offset[0] / selection_game.area_zoom, selection_game.area_view_center[1] + zoom_offset[1] / selection_game.area_zoom)
    assert selection_game.area_zoom > 1 and np.allclose(map_anchor_before, map_anchor_after)
    keyboard_zoom = selection_game.area_zoom
    selection_game._handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_PLUS, "unicode": "+"}))
    assert selection_game.area_zoom > keyboard_zoom
    selection_game._render()
    selection_game._click(selection_game.hit_targets["area_confirm"].center, 1)
    assert selection_game.screen_name == "deployment"

    preview_game = Deadgrid()
    preview_game._begin_operation(CityMap("Placement preview test", population=1000))
    preview_point = (MAP_RECT.x + 100, MAP_RECT.y + 100)
    preview_game._click(preview_point, 1)
    assert not preview_game.units and preview_game.target_mode == "deploy"
    preview_game._draw_operation(False)
    preview_game._click(preview_point, 1)
    assert len(preview_game.units) == 1 and preview_game.target_mode is None

    wall_game = Deadgrid()
    wall_game.screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
    wall_city = CityMap(
        "Barricade drag test",
        population=1000,
        buildings=[[(0.2, 0.3), (0.3, 0.3), (0.3, 0.4), (0.2, 0.4)]],
    )
    wall_game._begin_operation(wall_city)
    wall_game._draw_operation(False)
    wall_game._click(wall_game.hit_targets["unit_BARRICADE"].center, 1)
    wall_start = wall_game._map_point(0.204, 0.35)
    wall_end = wall_game._map_point(0.296, 0.35)
    wall_game._handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": wall_start, "button": 1}))
    wall_game._handle_event(pygame.event.Event(pygame.MOUSEMOTION, {"pos": wall_end, "buttons": (True, False, False)}))
    wall_game._draw_operation(False)
    wall_game._handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": wall_end, "button": 1}))
    wall_posts = [unit for unit in wall_game.units if unit["kind"] == "BARRICADE"]
    assert len(wall_posts) == 4 and wall_game.budget == 1500 - 4 * UNITS["BARRICADE"]["cost"]
    assert wall_posts[0]["x"] == 200 and wall_posts[-1]["x"] == 300

    street_nodes, street_graph, street_edges = _build_road_graph(city)
    crossing = int(np.argmin((street_nodes[:, 0] - 450) ** 2 + (street_nodes[:, 1] - 308) ** 2))
    assert len(street_graph[crossing]) >= 4

    chokepoint_city = CityMap(
        "Bridge detour test",
        roads=[[(0.1, 0.5), (0.9, 0.5)], [(0.1, 0.5), (0.1, 0.2), (0.9, 0.2), (0.9, 0.5)]],
        road_types=["residential", "residential|bridge"],
    )
    route_test = Deadgrid()
    route_test._prepare_road_network(chokepoint_city)
    assert any(edge[-2] for edge in route_test.road_edges)
    target_density = np.zeros((GRID_H, GRID_W), dtype=np.int32)
    target_density[21, 54] = 100
    route_test._rebuild_road_routes(target_density)
    start_node = int(route_test.road_snap_grid[21, 6])
    open_route = int(route_test.road_next_nodes[start_node])
    route_test.units = [{"kind": "BARRICADE", "x": 500.0, "y": 350.0}]
    route_test._rebuild_road_routes(target_density)
    diverted_route = int(route_test.road_next_nodes[start_node])
    assert open_route != diverted_route and not np.array_equal(route_test.road_nodes[diverted_route], route_test.road_nodes[open_route])
    narrow_city = CityMap("Narrow street test", roads=[[(0.1, 0.1), (0.9, 0.1)]], road_types=["residential|narrow"])
    assert all(edge[-1] for edge in _build_road_graph(narrow_city)[2])
    one_way_city = CityMap("One-way test", roads=[[(0.1, 0.5), (0.9, 0.5)]], road_types=["primary|oneway"])
    oneway_game = Deadgrid()
    oneway_game._prepare_road_network(one_way_city)
    east_density = np.zeros((GRID_H, GRID_W), dtype=np.int32)
    east_density[21, 54] = 100
    oneway_game._rebuild_road_routes(east_density)
    west_node = int(oneway_game.road_snap_grid[21, 6])
    east_node = int(oneway_game.road_snap_grid[21, 54])
    assert oneway_game.road_nodes[oneway_game.road_next_nodes[west_node], 0] > oneway_game.road_nodes[west_node, 0]
    west_density = np.zeros_like(east_density)
    west_density[21, 6] = 100
    oneway_game._rebuild_road_routes(west_density)
    assert oneway_game.road_next_nodes[east_node] == -1

    route_failure_game = Deadgrid()
    route_failure_game._begin_operation(CityMap("Route failure test", population=1000, roads=one_way_city.roads, road_types=one_way_city.road_types))
    route_failure_game.civilians = {key: values[:8].copy() for key, values in route_failure_game.civilians.items()}
    route_failure_game.civilians["x"].fill(850)
    route_failure_game.civilians["y"].fill(350)
    route_failure_game.civilians["evacuating"].fill(True)
    route_failure_game.civilians["route_node"] = route_failure_game._snap_agents_to_roads(route_failure_game.civilians["x"], route_failure_game.civilians["y"])
    route_failure_game.units.append({"kind": "EVAC STATION", "x": 100.0, "y": 350.0, "range": 64, "route_node": int(route_failure_game._snap_agents_to_roads(np.array([100]), np.array([350]))[0])})
    route_failure_game._rebuild_evac_routes(np.zeros((GRID_H, GRID_W), dtype=np.float32))
    assert route_failure_game.route_failure_agents == 8 and route_failure_game.route_failure_population == 8
    assert route_failure_game.route_failure_locations
    route_event = route_failure_game.containment_timeline[-1]
    assert route_event["kind"] == "route_failure" and route_event["locations"]
    assert _parse_population("2,133,111") == 2_133_111
    assert _parse_population("1.5 million") == 1_500_000

    class GeocodeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[dict[str, str]]:
            return [{
                "lat": "48.8584",
                "lon": "2.2945",
                "name": "Eiffel Tower",
                "boundingbox": ["48.85", "48.87", "2.28", "2.31"],
                "extratags": {"population": "12000"},
            }]

    def unavailable_overpass(*args: Any, **kwargs: Any) -> None:
        raise requests.ConnectionError("offline test")

    def overture_footprint(target: CityMap, bounds: tuple[float, float, float, float]) -> bool:
        target.buildings.append([(0.45, 0.45), (0.55, 0.45), (0.55, 0.55), (0.45, 0.55)])
        target.building_weights.append(8.0)
        return True

    with patch("deadgrid.requests.get", return_value=GeocodeResponse()), patch("deadgrid.requests.post", side_effect=unavailable_overpass), patch.object(sys.modules[__name__], "_add_overture_buildings", side_effect=overture_footprint):
        live_city = load_city("Eiffel Tower", use_overture=True, radius_km=1.2)
    assert live_city.center == (48.8584, 2.2945) and live_city.source == "OVERTURE MAPS"
    assert live_city.roads and live_city.buildings[0][0] == (0.45, 0.45)
    assert len(live_city.building_weights) == len(live_city.buildings)
    assert live_city.population > 0 and live_city.population_source.startswith("OSM POPULATION TAG")
    assert live_city.radius_km == 1.2
    with patch("deadgrid.requests.get", return_value=GeocodeResponse()), patch("deadgrid.requests.post", side_effect=unavailable_overpass), patch.object(sys.modules[__name__], "_add_overture_buildings", return_value=False):
        fallback_city = load_city("Eiffel Tower", use_overture=True)
    assert len(fallback_city.buildings) == len(fallback_city.building_weights) > 0

    config_game = Deadgrid()
    config_game._render()
    config_game._click(config_game.hit_targets["config"].center, 1)
    config_game._render()
    config_game._click(config_game.hit_targets["density_1.5"].center, 1)
    config_game._click(config_game.hit_targets["behavior_RUNNER"].center, 1)
    config_game._click(config_game.hit_targets["resources_2500"].center, 1)
    config_game._click(config_game.hit_targets["auto_collapse"].center, 1)
    config_game.scenario_name = "Test scenario"
    with tempfile.TemporaryDirectory() as directory, patch.object(sys.modules[__name__], "SCENARIO_PATH", Path(directory) / "scenarios.json"):
        config_game._save_scenario()
        scenario_game = Deadgrid()
        assert len(scenario_game.saved_scenarios) == 1
        scenario_game.selected_scenario_index = 0
        with patch.object(scenario_game, "_request_map") as map_request:
            scenario_game._load_selected_scenario()
        assert scenario_game.population_density == 1.5 and scenario_game.zombie_behavior == "RUNNER"
        assert scenario_game.starting_resources == 2500 and not scenario_game.auto_collapse
        assert map_request.called

    game = Deadgrid()
    game._render()
    slider = game.hit_targets["radius_slider"]
    game._click((slider.right - 1, slider.centery), 1)
    assert game.radius_km > 1.99
    game._click(game.hit_targets["infect_5%"].center, 1)
    assert game.initial_infection_percent == 5.0
    game._begin_operation(city)
    expected_initial_zombies = max(1, round(game.population_agents * game.initial_infection_percent / 100))
    assert game.screen_name == "deployment" and len(game.civilians["x"]) == game.population_agents - expected_initial_zombies
    assert game.population_agents == 30_000 and len(game.zombies["x"]) == 1_500

    accounting_game = Deadgrid()
    accounting_game._begin_operation(CityMap("Population accounting test", population=10_000_003))
    cohort_populations = np.concatenate((accounting_game.civilians["population"], accounting_game.zombies["population"]))
    assert accounting_game.population_agents == 50_000
    assert accounting_game.represented_population == 10_000_003 == int(cohort_populations.sum())
    assert accounting_game.initial_infected_population == int(accounting_game.zombies["population"].sum())
    assert int(cohort_populations.max()) - int(cohort_populations.min()) <= 1
    ledger = accounting_game._population_ledger()
    assert ledger["accounted"] == ledger["starting"] == 10_000_003
    accounting_game.civilians["status"][0] = 1
    ledger = accounting_game._population_ledger()
    assert ledger["exposed"] == int(accounting_game.civilians["population"][0])
    assert ledger["accounted"] == ledger["starting"]
    accounting_game.zombies["hp"][0] = 0
    zombie_x, zombie_y = accounting_game.zombies["x"][0], accounting_game.zombies["y"][0]
    accounting_game._damage_zombies(float(zombie_x), float(zombie_y), 0.01, 1)
    ledger = accounting_game._population_ledger()
    assert ledger["dead"] == accounting_game._eliminated_population
    assert ledger["accounted"] == ledger["starting"]

    origin_game = Deadgrid()
    origin_game._begin_operation(CityMap("Moved outbreak test", population=2_000))
    original_zombie_population = origin_game.zombies["population"].copy()
    original_zombie_hp = origin_game.zombies["hp"].copy()
    origin_game._draw_operation(False)
    origin_game._click(origin_game.hit_targets["origin"].center, 1)
    moved_origin = (0.28, 0.72)
    origin_game._click(origin_game._map_point(*moved_origin), 1)
    assert np.array_equal(origin_game.zombies["population"], original_zombie_population)
    assert np.array_equal(origin_game.zombies["hp"], original_zombie_hp)
    assert len(origin_game.zombies["hp"]) == len(origin_game.zombies["x"]) == len(origin_game.zombies["route_node"])
    assert origin_game.containment_timeline[0]["locations"][0][:2] == origin_game.infection_origin
    origin_game.civilians["status"][0] = 1
    origin_game.civilians["incubation"][0] = 0
    origin_game._start_simulation()
    origin_game._update(0.05)
    assert len(origin_game.zombies["population"]) == len(origin_game.zombies["x"])
    assert origin_game._population_ledger()["accounted"] == origin_game.represented_population

    turret_game = Deadgrid()
    turret_game._begin_operation(CityMap("Turret accounting test", population=2_000))
    turret_game.zombies["hp"][:] = 1
    turret_x, turret_y = float(turret_game.zombies["x"][0]), float(turret_game.zombies["y"][0])
    turret_game.units.append({"kind": "SENTRY", "x": turret_x, "y": turret_y, "timer": 0.0})
    turret_game._start_simulation()
    turret_game._update(0.05)
    turret_ledger = turret_game._population_ledger()
    assert turret_game._eliminated > 0
    assert turret_ledger["dead"] == turret_game._eliminated_population
    assert turret_ledger["accounted"] == turret_ledger["starting"]

    barricade_cache_game = Deadgrid()
    barricade_cache_game._begin_operation(CityMap("Barricade cache test", population=1_000))
    cell_center = ((20.5) * WORLD_W / GRID_W, (20.5) * WORLD_H / GRID_H)
    barricade_cache_game.units.extend(
        {"kind": "BARRICADE", "x": cell_center[0], "y": cell_center[1]}
        for _ in range(2)
    )
    barricade_cache_game._refresh_barricade_caches()
    assert math.isclose(float(barricade_cache_game._barricade_slow_grid[20, 20]), 0.28 ** 2, rel_tol=1e-6)
    assert barricade_cache_game._penalized_edge_keys
    previous_penalized_edges = barricade_cache_game._penalized_edge_keys.copy()
    barricade_cache_game.units[0]["x"] += 200
    barricade_cache_game._refresh_barricade_caches()
    assert barricade_cache_game._penalized_edge_keys != previous_penalized_edges

    evacuation_game = Deadgrid()
    evacuation_game.represented_population = 1_000
    evacuation_game.zombies = {"x": np.empty(0, dtype=np.float32)}
    evacuation_game.rescued = 980
    evacuation_game.casualties = 10
    evacuation_game.infection_casualties = 10
    evacuation_game._update_simulation_status()
    assert evacuation_game.simulation_status == "Evacuated"
    assert evacuation_game.end_reason == "EVACUATION COMPLETE"
    assert evacuation_game.screen_name == "analysis"
    assert evacuation_game.containment_timeline[-1]["kind"] == "evacuated"
    evacuation_game._render()

    contained_game = Deadgrid()
    contained_game._begin_operation(CityMap("Containment test", population=10_000))
    contained_game.zombies = {"x": np.empty(0, dtype=np.float32), "y": np.empty(0, dtype=np.float32), "hp": np.empty(0, dtype=np.float32), "route_node": np.empty(0, dtype=np.int32), "route_narrow": np.empty(0, dtype=bool)}
    contained_game._start_simulation()
    contained_game._update(0.05)
    assert contained_game.screen_name == "active" and contained_game.simulation_status == "Contained"
    contained_game._stop_simulation()
    assert contained_game.screen_name == "analysis" and contained_game.end_reason == "MANUAL STOP"
    contained_game._render()
    assert contained_game.hit_targets["restart_scenario"]
    collapse_game = Deadgrid()
    collapse_game._begin_operation(CityMap("Collapse test", population=1000))
    collapse_game._start_simulation()
    collapse_game.casualties = collapse_game.represented_population
    collapse_game._update_simulation_status()
    assert collapse_game.screen_name == "analysis" and collapse_game.simulation_status == "Collapsed"
    game._draw_operation(False)
    game._click(game.hit_targets["origin"].center, 1)
    assert game.target_mode == "origin"
    game._click(game._map_point(0.25, 1 / 3), 1)
    assert game.infection_origin == (0.25, 1 / 3) and len(game.zombies["x"]) == round(game.population_agents * 0.05)
    game._draw_map_overlay(game.canvas)
    civilian_point = game._map_point(game.civilians["x"][0] / WORLD_W, game.civilians["y"][0] / WORLD_H)
    zombie_point = game._map_point(game.zombies["x"][0] / WORLD_W, game.zombies["y"][0] / WORLD_H)
    assert game.canvas.get_at(civilian_point)[:3] == (250, 250, 242)
    assert game.canvas.get_at(zombie_point)[:3] == LIME
    game._begin_operation(CityMap("Dense test area", population=10000))
    assert game.zombies["x"][0] and len(game.zombies["x"]) == round(game.population_agents * 0.05)

    density_city = CityMap(
        "Residential density test",
        population=10_000,
        buildings=[
            [(0.1, 0.1), (0.2, 0.1), (0.2, 0.2), (0.1, 0.2)],
            [(0.75, 0.75), (0.85, 0.75), (0.85, 0.85), (0.75, 0.85)],
        ],
        building_weights=[100.0, 0.01],
    )
    density_game = Deadgrid()
    density_game._begin_operation(density_city)
    residential = ((density_game.civilians["x"] / WORLD_W >= 0.1) & (density_game.civilians["x"] / WORLD_W <= 0.2) & (density_game.civilians["y"] / WORLD_H >= 0.1) & (density_game.civilians["y"] / WORLD_H <= 0.2))
    assert np.count_nonzero(residential) / len(residential) > 0.98

    spread_game = Deadgrid()
    spread_game._begin_operation(CityMap("Exposure test", population=20_000))
    sample_count = 32
    spread_game.civilians = {key: values[:sample_count].copy() for key, values in spread_game.civilians.items()}
    spread_game.civilians["x"].fill(500)
    spread_game.civilians["y"].fill(350)
    spread_game.civilians["exposure"].fill(0)
    spread_game.civilians["incubation"].fill(0)
    spread_game.civilians["status"].fill(0)
    spread_game.civilians["susceptibility"].fill(1)
    spread_game.civilians["wander"].fill(30)
    spread_game.civilians["dx"].fill(0)
    spread_game.civilians["dy"].fill(0)
    spread_game.civilians["evacuating"].fill(False)
    spread_game.civilians["route_node"] = spread_game._snap_agents_to_roads(spread_game.civilians["x"], spread_game.civilians["y"])
    spread_game.civilians["route_narrow"].fill(False)
    spread_game.zombies = {"x": np.full(16, 500, dtype=np.float32), "y": np.full(16, 350, dtype=np.float32), "hp": np.full(16, 40, dtype=np.float32)}
    initial_casualties = spread_game.casualties
    for _ in range(480):
        spread_game._update_crowd(0.05)
    assert len(spread_game.zombies["x"]) > 1
    assert spread_game.casualties > initial_casualties

    panic_game = Deadgrid()
    panic_game._begin_operation(CityMap("Panic response test", population=2000))
    panic_game.civilians = {key: values[:1].copy() for key, values in panic_game.civilians.items()}
    panic_game.civilians["x"][0], panic_game.civilians["y"][0] = 520, 308
    panic_game.civilians["status"].fill(0)
    panic_game.civilians["susceptibility"].fill(1)
    panic_game.civilians["wander"].fill(30)
    panic_game.civilians["dx"].fill(0)
    panic_game.civilians["dy"].fill(0)
    panic_game.civilians["evacuating"].fill(False)
    panic_game.civilians["route_node"] = panic_game._snap_agents_to_roads(panic_game.civilians["x"], panic_game.civilians["y"])
    panic_game.zombies = {"x": np.array([500], dtype=np.float32), "y": np.array([308], dtype=np.float32), "hp": np.array([40], dtype=np.float32)}
    panic_game._update_crowd(0.05)
    assert panic_game.civilians["x"][0] > 520
    game.selected_unit = "SENTRY"
    game._deploy((MAP_RECT.centerx, MAP_RECT.centery))
    assert len(game.units) == 1 and game.budget == 1200
    game._start_simulation()
    game._draw_operation(True)
    game._click(game.hit_targets["intel_horde"].center, 1)
    assert game.intel_modes["horde"]
    game._render()
    asset_point = game._map_point(game.units[0]["x"] / WORLD_W, game.units[0]["y"] / WORLD_H)
    game._click(asset_point, 1)
    assert game.target_mode == "move"
    game._click((MAP_RECT.x + 100, MAP_RECT.y + 100), 1)
    assert game.target_mode is None and game.units[0]["x"] < 200
    evacuation = Deadgrid()
    evacuation._begin_operation(city)
    evacuation.zombies = {"x": np.array([100000.0], dtype=np.float32), "y": np.array([100000.0], dtype=np.float32), "hp": np.array([40.0], dtype=np.float32)}
    evacuation.civilians = {key: values[:20].copy() for key, values in evacuation.civilians.items()}
    evacuation.civilians["x"].fill(520)
    evacuation.civilians["y"].fill(308)
    evacuation.civilians["route_node"] = evacuation._snap_agents_to_roads(evacuation.civilians["x"], evacuation.civilians["y"])
    evacuation.selected_unit = "EVAC STATION"
    evacuation.screen_name = "active"
    evacuation._deploy(evacuation._map_point(0.59, 0.44))
    evacuation.screen_name = "deployment"
    evacuation._start_simulation()
    for _ in range(4):
        evacuation._update(1 / 60)
    assert evacuation.rescued == 0
    for _ in range(90):
        evacuation._update(1 / 60)
    assert evacuation.rescued >= evacuation.people_per_agent

    shelter_game = Deadgrid()
    shelter_game._begin_operation(CityMap("Shelter test", population=3000))
    shelter_game.screen_name = "active"
    shelter_game.selected_unit = "SAFE ZONE"
    shelter_game._deploy(MAP_RECT.center)
    shelter_game._draw_operation(True)
    shelter_game._click(shelter_game.hit_targets["policy_SHELTER"].center, 1)
    assert shelter_game.emergency_policy == "SHELTER" and np.all(shelter_game.civilians["evacuating"])
    shelter = next(unit for unit in shelter_game.units if unit["kind"] == "SAFE ZONE")
    shelter_game.civilians["x"].fill(shelter["x"])
    shelter_game.civilians["y"].fill(shelter["y"])
    shelter_game.zombies = {"x": np.array([shelter["x"]], dtype=np.float32), "y": np.array([shelter["y"]], dtype=np.float32), "hp": np.array([40], dtype=np.float32)}
    shelter_game._update_crowd(0.05)
    assert shelter["safe_agents"] == len(shelter_game.civilians["x"])
    assert not np.any(shelter_game.civilians["status"])

    supply_game = Deadgrid()
    supply_game._begin_operation(CityMap("Supply test", population=500))
    supply_game.screen_name = "active"
    supply_game.selected_unit = "SUPPLY DROP"
    supply_game._deploy(MAP_RECT.center)
    supply_game.simulation_time = 8.0
    supply_game._update(0.05)
    assert supply_game.budget == 1800 and supply_game.supply_deliveries == 1

    barricade_game = Deadgrid()
    barricade_game._begin_operation(CityMap("Live barricade test", population=1000))
    barricade_game._start_simulation()
    barricade_game.selected_unit = "BARRICADE"
    barricade_game._deploy(MAP_RECT.center)
    assert any(unit["kind"] == "BARRICADE" for unit in barricade_game.units)
    for _ in range(240):
        game._update(1 / 60)
    assert game.simulation_time > 3.9
    assert "horde" in game.intel_layers
    restart_city_name = game.city.name
    game._stop_simulation()
    game._render()
    game._click(game.hit_targets["restart_scenario"].center, 1)
    assert game.screen_name == "deployment" and game.city.name == restart_city_name
    game.screen_name = "analysis"
    game._render()
    game._click(game.hit_targets["change_parameters"].center, 1)
    assert game.screen_name == "menu"
    pygame.quit()
    print("DEADGRID SELF-TEST OK · map, deployment, and live simulation")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        Deadgrid().run()