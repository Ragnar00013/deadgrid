"""DEADGRID: a small, data-driven city containment strategy game."""

from __future__ import annotations

import json
import heapq
import math
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
WORLD_W, WORLD_H = 1000.0, 700.0
MAP_RECT = pygame.Rect(276, 118, 796, 636)
SCENARIO_PATH = Path(__file__).with_name("deadgrid_scenarios.json")

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
    radius_km: float = 0.8
    population: int = 0
    base_population: int = 0
    population_source: str = "UNAVAILABLE"
    roads: list[list[tuple[float, float]]] = field(default_factory=list)
    road_types: list[str] = field(default_factory=list)
    buildings: list[list[tuple[float, float]]] = field(default_factory=list)
    building_weights: list[float] = field(default_factory=list)
    parks: list[list[tuple[float, float]]] = field(default_factory=list)
    source: str = "SCHEMATIC"
    note: str = ""


def _normalized(lon: float, lat: float, bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    west, south, east, north = bounds
    return ((lon - west) / (east - west), (north - lat) / (north - south))


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


def load_city(place: str, use_overture: bool = True, radius_km: float = 1.5) -> CityMap:
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
        radius_lat = radius_km / 110.57
        radius_lon = min(0.05, radius_lat / max(0.22, math.cos(math.radians(lat))))
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
        ");out geom qt;"
    )
    city = CityMap(name=display_name, center=(lat, lon), place_bounds=place_bounds, radius_km=radius_km)
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
        geometry = element.get("geometry") or []
        points = [_normalized(float(node["lon"]), float(node["lat"]), bounds) for node in geometry]
        if len(points) < 2:
            continue
        tags = element.get("tags", {})
        if "highway" in tags:
            city.roads.append(points)
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
    if not city.buildings:
        city.buildings = fallback.buildings
        city.building_weights = fallback.building_weights.copy()
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

    def request(self, place: str, use_overture: bool, radius_km: float) -> None:
        thread = threading.Thread(target=self._work, args=(place, use_overture, radius_km), daemon=True)
        thread.start()

    def _work(self, place: str, use_overture: bool, radius_km: float) -> None:
        self.results.put(load_city(place, use_overture, radius_km))


class Deadgrid:
    def __init__(self) -> None:
        pygame.init()
        pygame.display.set_caption("DEADGRID // City Containment")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
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
        self.emergency_policy = "EVACUATE"
        self.supply_deliveries = 0
        self.infection_casualties = 0
        self.strike_casualties = 0
        self.route_failure_agents = 0
        self.route_failure_history: list[str] = []
        self.route_failure_locations: list[tuple[float, float, int]] = []
        self.route_failure_signature: tuple[tuple[int, int], ...] = ()
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
        self.road_adjacency: list[list[tuple[int, float, bool, bool]]] = []
        self.road_edges: list[tuple[int, int, float, float, float, bool, bool]] = []
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
        self.infected_count = 0
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
        self.worker.request(place, self.use_overture, self.radius_km)

    def _begin_operation(self, city: CityMap) -> None:
        self.city = city
        self.intel_grids = {key: np.zeros((GRID_H, GRID_W), dtype=np.float32) for key in self.intel_modes}
        self.intel_layers = {}
        self.intel_refresh = 0.0
        self._prepare_road_network(city)
        self.screen_name = "deployment"
        self.selected_unit = "SNIPER"
        self.target_mode = None
        self.units = []
        self.effects = []
        self.budget = self.starting_resources
        self.rescued = self.casualties = self.infected_count = 0
        self.simulation_status = "Deployment"
        self.end_reason = ""
        self._eliminated = 0
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
        self.people_per_agent = max(1, math.ceil(population / self.population_agents))
        self.represented_population = self.population_agents * self.people_per_agent
        initial_zombie_count = max(1, round(self.population_agents * self.initial_infection_percent / 100))
        initial_zombie_count = min(self.population_agents - 1, initial_zombie_count)
        self.initial_infected_agents = initial_zombie_count
        self.initial_infected_population = initial_zombie_count * self.people_per_agent
        self.casualties = self.initial_infected_population
        self.infection_casualties = self.initial_infected_population
        self.strike_casualties = 0
        self.supply_deliveries = 0
        self.route_failure_agents = 0
        self.route_failure_history = []
        self.route_failure_locations = []
        self.route_failure_signature = ()
        self.collapse_cause = ""
        civilian_count = self.population_agents - initial_zombie_count
        agent_rng = np.random.default_rng(rng.randrange(2**32))
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
        }
        self.zombies["route_node"] = self._snap_agents_to_roads(self.zombies["x"], self.zombies["y"])
        self.zombies["route_narrow"] = np.zeros(initial_zombie_count, dtype=bool)
        self.screen_name = "deployment"
        note = city.note or f"{city.source} DATA READY"
        self._notify(f"{city.name.upper()} · {note}", 5.0)

    def _map_point(self, nx: float, ny: float) -> tuple[int, int]:
        return int(MAP_RECT.x + nx * MAP_RECT.w), int(MAP_RECT.y + ny * MAP_RECT.h)

    def _world_point(self, point: tuple[int, int]) -> tuple[float, float]:
        return ((point[0] - MAP_RECT.x) / MAP_RECT.w * WORLD_W, (point[1] - MAP_RECT.y) / MAP_RECT.h * WORLD_H)

    def _prepare_road_network(self, city: CityMap) -> None:
        self.road_nodes, self.road_adjacency, self.road_edges = _build_road_graph(city)
        if not len(self.road_nodes):
            fallback = schematic_map(city.name)
            self.road_nodes, self.road_adjacency, self.road_edges = _build_road_graph(fallback)
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

    def _rebuild_road_routes(self, occupancy: np.ndarray) -> None:
        if not self.road_adjacency:
            return
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

        barricades = [(unit["x"], unit["y"]) for unit in self.units if unit["kind"] == "BARRICADE"]
        penalized_edges: set[tuple[int, int]] = set()
        if barricades:
            for first, second, _, mid_x, mid_y, _, _ in self.road_edges:
                for barricade_x, barricade_y in barricades:
                    if (mid_x - barricade_x) ** 2 + (mid_y - barricade_y) ** 2 < 48 ** 2:
                        penalized_edges.add((min(first, second), max(first, second)))
                        break

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
                if edge_key in penalized_edges:
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
        barricades = [(unit["x"], unit["y"]) for unit in self.units if unit["kind"] == "BARRICADE"]
        penalized_edges: set[tuple[int, int]] = set()
        for first, second, _, mid_x, mid_y, _, _ in self.road_edges:
            if any((mid_x - bx) ** 2 + (mid_y - by) ** 2 < 48 ** 2 for bx, by in barricades):
                penalized_edges.add((min(first, second), max(first, second)))

        distance = np.full(len(self.road_nodes), np.inf, dtype=np.float64)
        next_node = np.full(len(self.road_nodes), -1, dtype=np.int32)
        next_narrow = np.zeros(len(self.road_nodes), dtype=bool)
        pending: list[tuple[float, int]] = []
        for node in goals:
            distance[node] = 0.0
            heapq.heappush(pending, (0.0, node))
        while pending:
            current_cost, node = heapq.heappop(pending)
            if current_cost != distance[node]:
                continue
            for neighbor, edge_cost, _, is_narrow in self.road_adjacency[node]:
                edge_key = (min(node, neighbor), max(node, neighbor))
                if edge_key in penalized_edges:
                    edge_cost *= 22.0
                midpoint_x = (self.road_nodes[node, 0] + self.road_nodes[neighbor, 0]) / 2
                midpoint_y = (self.road_nodes[node, 1] + self.road_nodes[neighbor, 1]) / 2
                grid_x = int(np.clip(midpoint_x * (GRID_W / WORLD_W), 0, GRID_W - 1))
                grid_y = int(np.clip(midpoint_y * (GRID_H / WORLD_H), 0, GRID_H - 1))
                edge_cost *= 1.0 + min(16.0, float(horde_pressure[grid_y, grid_x]) * 0.7)
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
        self.route_failure_locations = []
        if not len(failed_indices):
            self.route_failure_signature = ()
            return
        cell_x = np.clip((self.civilians["x"][failed_indices] * (GRID_W / WORLD_W)).astype(np.int32), 0, GRID_W - 1)
        cell_y = np.clip((self.civilians["y"][failed_indices] * (GRID_H / WORLD_H)).astype(np.int32), 0, GRID_H - 1)
        counts = np.bincount(cell_y * GRID_W + cell_x, minlength=GRID_W * GRID_H)
        hot_cells = np.argsort(counts)[::-1]
        for cell in hot_cells:
            if counts[cell] == 0 or len(self.route_failure_locations) == 3:
                break
            grid_y, grid_x = divmod(int(cell), GRID_W)
            location = ((grid_x + 0.5) / GRID_W, (grid_y + 0.5) / GRID_H, int(counts[cell]))
            self.route_failure_locations.append(location)
        signature = tuple((round(x * 100), round(y * 100)) for x, y, _ in self.route_failure_locations)
        if signature != self.route_failure_signature:
            self.route_failure_history.append(f"{self.route_failure_agents:,} residents have no route; inspect the highlighted blocks, one-way links, or barricades.")
            self.route_failure_history = self.route_failure_history[-5:]
            self.route_failure_signature = signature

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
                self.rescued += len(indices) * self.people_per_agent
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
            self.intel_layers[mode] = pygame.transform.scale(layer, MAP_RECT.size)

    def _draw_agents(self, surface: pygame.Surface, x: np.ndarray, y: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> None:
        if not np.any(mask):
            return
        pixels_x = np.clip((x[mask] * (MAP_RECT.w / WORLD_W)).astype(np.int32), 0, MAP_RECT.w - 1) + MAP_RECT.x
        pixels_y = np.clip((y[mask] * (MAP_RECT.h / WORLD_H)).astype(np.int32), 0, MAP_RECT.h - 1) + MAP_RECT.y
        pixels = pygame.surfarray.pixels3d(surface)
        pixels[pixels_x, pixels_y] = color
        pixels[np.minimum(pixels_x + 1, MAP_RECT.right - 1), pixels_y] = color
        del pixels

    def _draw_city(self, surface: pygame.Surface, rect: pygame.Rect, city: CityMap) -> None:
        pygame.draw.rect(surface, MAP_BASE, rect)
        surface.set_clip(rect)
        def screen_points(points: list[tuple[float, float]]) -> list[tuple[int, int]]:
            return [(int(rect.x + x * rect.w), int(rect.y + y * rect.h)) for x, y in points]
        for park in city.parks:
            points = screen_points(park)
            if len(points) >= 3:
                pygame.draw.polygon(surface, (162, 190, 147), points)
        for road in city.roads:
            points = screen_points(road)
            if len(points) >= 2:
                pygame.draw.lines(surface, (172, 184, 163), False, points, 7)
                pygame.draw.lines(surface, MAP_ROAD, False, points, 4)
        for polygon in city.buildings:
            points = screen_points(polygon)
            if len(points) >= 3:
                pygame.draw.polygon(surface, MAP_BUILDING, points)
                pygame.draw.polygon(surface, (121, 139, 126), points, 1)
        surface.set_clip(None)
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

    def _header(self, surface: pygame.Surface, mode: str) -> None:
        pygame.draw.rect(surface, PANEL, (0, 0, WIDTH, 78))
        pygame.draw.line(surface, LINE, (0, 77), (WIDTH, 77), 1)
        pygame.draw.rect(surface, LIME, (20, 19, 4, 34), border_radius=2)
        self._text(surface, "DEADGRID", 36, 16, TEXT, self.bold)
        self._text(surface, "CITY CONTAINMENT  /  FIELD COMMAND", 36, 38, MUTED, self.small)
        self._text(surface, mode, 600, 27, LIME, self.small)
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
        self._text(surface, "OUTBREAK SIMULATION  /  CONTAINMENT PLANNER", 34, 115, CYAN, self.small)
        self._text(surface, "HOLD THE LINE.", 34, 153, TEXT, self.large)
        self._text(surface, "SAVE WHO YOU CAN.", 34, 196, LIME, self.large)
        self._text(surface, "The city is the map. The crowd is the clock.", 36, 252, MUTED)
        self._text(surface, "Choose a real place, read the streets, and set your", 36, 279, MUTED)
        self._text(surface, "perimeter before the first infected move.", 36, 299, MUTED)
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
            self._button(surface, "build_plan", pygame.Rect(36, 676, 615, 57), "BUILD RESPONSE PLAN  →", accent=LIME, subtitle="REAL PLACE DATA  /  DEPLOYMENT PHASE")
            self._button(surface, "config", pygame.Rect(359, 676, 292, 57), "WORLD SETTINGS  /  SCENARIOS", accent=CYAN, subtitle="DENSITY · BEHAVIOR · STARTING FUNDS")
        self._text(surface, self.message if self.message_timer > 0 else f"SAVED SCENARIOS  {len(self.saved_scenarios)}  ·  STARTING INFECTION  {self.initial_infection_percent:g}%", 36, 779, MUTED, self.small)

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
        self._draw_intelligence(surface)
        infection = self._map_point(*self.infection_origin)
        pygame.draw.circle(surface, (214, 77, 70), infection, 47, 1)
        pygame.draw.circle(surface, (214, 77, 70), infection, 24, 1)
        pygame.draw.circle(surface, RED, infection, 4)
        self._text(surface, "PATIENT ZERO", infection[0] + 10, infection[1] - 20, RED, self.small)
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
                radius = max(18, int(UNITS[kind]["range"] * MAP_RECT.w / WORLD_W))
                pygame.draw.circle(surface, data["color"], center, radius, 1)
            elif kind == "SUPPLY DROP":
                pygame.draw.rect(surface, data["color"], pygame.Rect(center[0] - 7, center[1] - 7, 14, 14), border_radius=2)
                pygame.draw.line(surface, INK, (center[0] - 4, center[1]), (center[0] + 4, center[1]), 2)
            else:
                pygame.draw.circle(surface, (14, 25, 25), center, 11)
                pygame.draw.circle(surface, data["color"], center, 8, 2)
                pygame.draw.circle(surface, data["color"], center, 3)
                if self.screen_name == "deployment" and kind in ("SNIPER", "MORTAR"):
                    pygame.draw.circle(surface, (*data["color"], 35), center, max(8, int(data["range"] * MAP_RECT.w / WORLD_W)), 1)
        self._draw_agents(surface, self.civilians["x"], self.civilians["y"], self.civilians["status"] == 0, (250, 250, 242))
        self._draw_agents(surface, self.civilians["x"], self.civilians["y"], self.civilians["status"] == 1, LIME)
        self._draw_agents(surface, self.zombies["x"], self.zombies["y"], np.ones(len(self.zombies["x"]), dtype=bool), LIME)
        for effect in self.effects:
            progress = effect["age"] / effect["duration"]
            radius = int(effect["radius"] * (0.45 + progress * 0.7) * MAP_RECT.w / WORLD_W)
            color = AMBER if effect["kind"] == "strike" else CYAN
            pygame.draw.circle(surface, color, self._map_point(effect["x"] / WORLD_W, effect["y"] / WORLD_H), radius, 2)
        if self.target_mode:
            cursor = self._map_point(*self._target_preview())
            color = RED if self.target_mode == "origin" else (AMBER if self.target_mode == "strike" else CYAN)
            pygame.draw.circle(surface, color, cursor, 14, 1)
            pygame.draw.line(surface, color, (cursor[0] - 20, cursor[1]), (cursor[0] + 20, cursor[1]), 1)
            pygame.draw.line(surface, color, (cursor[0], cursor[1] - 20), (cursor[0], cursor[1] + 20), 1)

    def _draw_intelligence(self, surface: pygame.Surface) -> None:
        labels = {"risk": "INFECTION RISK", "horde": "HORDE DENSITY", "exposed": "EXPOSED CIVILIANS", "survivors": "EST. SURVIVORS"}
        shown = []
        for mode, enabled in self.intel_modes.items():
            if enabled and mode in self.intel_layers:
                surface.blit(self.intel_layers[mode], MAP_RECT.topleft)
                shown.append(labels[mode])
        if shown:
            title = "INTEL  /  " + "  ·  ".join(shown)
            self._text(surface, title[:86], MAP_RECT.right - 390, MAP_RECT.y + 13, TEXT, self.small)

    def _target_preview(self) -> tuple[float, float]:
        x, y = self.mouse
        return (max(0.0, min(1.0, (x - MAP_RECT.x) / MAP_RECT.w)), max(0.0, min(1.0, (y - MAP_RECT.y) / MAP_RECT.h)))

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
        self._text(surface, f"{self.people_per_agent:,} PEOPLE PER AGENT", 36, 269, MUTED, self.small)
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
        if self.target_mode == "origin":
            self._text(surface, "SET OUTBREAK ORIGIN  ·  CLICK MAP", MAP_RECT.x + 15, MAP_RECT.y + 13, RED, self.small)
        elif self.target_mode == "move":
            self._text(surface, "SELECT NEW POSITION  ·  CLICK MAP TO CONFIRM", MAP_RECT.x + 15, MAP_RECT.y + 13, CYAN, self.small)
        elif self.target_mode:
            mode_name = "BOMBER STRIKE" if self.target_mode == "strike" else "GUNSHIP TASKING"
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
            active_tool = self.selected_unit == kind and not self.target_mode
            self._button(surface, f"unit_{kind}", pygame.Rect(1108, y, 216, 40), kind, active=active_tool, accent=item["color"], subtitle=f"${item['cost']}  /  {item['description']}")
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
        if self.screen_name == "deployment" and self.mouse and MAP_RECT.collidepoint(self.mouse):
            cursor = self.mouse
            color = UNITS[self.selected_unit]["color"]
            pygame.draw.circle(surface, color, cursor, 10, 1)
            pygame.draw.line(surface, color, (cursor[0] - 15, cursor[1]), (cursor[0] + 15, cursor[1]), 1)

    def _draw_analysis(self) -> None:
        self.canvas.fill(INK)
        self._header(self.canvas, "SIMULATION ANALYSIS")
        self._text(self.canvas, f"{self.end_reason or 'SIMULATION STOPPED'}  /  {self.simulation_status.upper()}", 72, 128, AMBER if self.simulation_status == "Collapsed" else CYAN, self.bold)
        self._text(self.canvas, self.city.name.upper(), 75, 212, CYAN, self.bold)
        self._text(self.canvas, f"PATIENT ZERO  /  X {self.patient_zero[0]:.0%}  ·  Y {self.patient_zero[1]:.0%}  ·  AREA RADIUS {self.radius_km:.2f} KM", 75, 238, MUTED, self.small)
        pygame.draw.line(self.canvas, LINE, (74, 272), (1285, 272), 1)
        stats = [
            ("ESTIMATED RESIDENTS", f"~{self.city.population:,}", TEXT),
            ("RESCUED POPULATION", f"{self.rescued:,}", LIME),
            ("CIVILIAN CASUALTIES", f"{self.casualties:,}", RED),
            ("INITIAL INFECTED", f"{self.initial_infected_agents:,} agents", AMBER),
            ("PEOPLE PER AGENT", f"{self.people_per_agent:,}", CYAN),
            ("RESIDENTS REMAINING", f"{max(0, self.represented_population - self.rescued - self.casualties):,}", LIME),
        ]
        for index, (label, value, color) in enumerate(stats):
            x = 75 + (index % 3) * 405
            y = 300 + (index // 3) * 112
            self._text(self.canvas, label, x, y, MUTED, self.small)
            self._text(self.canvas, value, x, y + 25, color, self.large)
        self._text(self.canvas, f"RUN TIME  {int(self.simulation_time // 60):02}:{int(self.simulation_time % 60):02}  ·  ZOMBIES ELIMINATED  {self._eliminated:,}", 75, 548, TEXT, self.bold)
        cause = self.collapse_cause or f"INFECTION LOSSES {self.infection_casualties:,}  ·  STRIKE LOSSES {self.strike_casualties:,}  ·  SUPPLY DROPS {self.supply_deliveries}"
        self._text(self.canvas, f"OUTCOME  /  {cause[:112]}", 75, 579, AMBER, self.small)
        self._text(self.canvas, f"ROUTE FAILURES  /  {self.route_failure_agents:,} UNROUTED RESIDENTS", 75, 607, RED if self.route_failure_agents else MUTED, self.small)
        if self.route_failure_history:
            self._text(self.canvas, self.route_failure_history[-1][:112], 75, 627, MUTED, self.small)
        if self.route_failure_locations:
            hotspots = "  ·  ".join(f"{x:.0%}/{y:.0%} ({count:,})" for x, y, count in self.route_failure_locations)
            self._text(self.canvas, f"STRANDED MAP CELLS  /  {hotspots}"[:118], 75, 648, AMBER, self.small)
        self._text(self.canvas, f"FIELD RESOURCES REMAINING  ${self.budget:,}", 75, 670, LIME, self.small)
        self._button(self.canvas, "restart_scenario", pygame.Rect(75, 700, 345, 70), "RESTART SAME SCENARIO  ↻", accent=LIME, subtitle="KEEP CITY, SETTINGS, AND PATIENT ZERO")
        self._button(self.canvas, "change_parameters", pygame.Rect(440, 700, 300, 70), "CHANGE PARAMETERS", accent=CYAN, subtitle="RETURN TO WORLD SETUP")

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

    def _deploy(self, point: tuple[int, int]) -> None:
        if not MAP_RECT.collidepoint(point):
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
            self.casualties += int(np.count_nonzero(lost)) * self.people_per_agent
            self.strike_casualties += int(np.count_nonzero(lost)) * self.people_per_agent
            self.civilians = {key: values[~lost] for key, values in self.civilians.items()}
            self.effects.append({"x": x, "y": y, "radius": radius, "duration": 1.1, "age": 0, "kind": "strike"})
            self._notify(f"BOMBER STRIKE · {killed:,} HOSTILES · {int(np.count_nonzero(lost)) * self.people_per_agent:,} CIVILIAN CASUALTIES", 4.0)
        elif self.target_mode == "gunship":
            cost = 290
            if not self._pay(cost):
                return
            self.gunship = 10.0
            self.gunship_target = (x, y)
            self.effects.append({"x": x, "y": y, "radius": 92, "duration": 10, "age": 0, "kind": "gunship"})
            self._notify("GUNSHIP ON STATION · TEN-SECOND STRAFING RUN", 4.0)
        self.target_mode = None

    def _damage_zombies(self, x: float, y: float, radius: float, damage: float) -> int:
        within = (self.zombies["x"] - x) ** 2 + (self.zombies["y"] - y) ** 2 <= radius ** 2
        self.zombies["hp"][within] -= damage
        alive = self.zombies["hp"] > 0
        killed = int(len(alive) - np.count_nonzero(alive))
        self.zombies = {key: values[alive] for key, values in self.zombies.items()}
        self._eliminated = getattr(self, "_eliminated", 0) + killed
        return killed

    def _update_simulation_status(self) -> None:
        total = max(1, self.represented_population)
        lost_or_rescued = self.rescued + self.casualties
        remaining = max(0, total - lost_or_rescued)
        zombies = len(self.zombies["x"])
        susceptible = int(np.count_nonzero(self.civilians["status"] == 0))
        if remaining <= max(self.people_per_agent * 5, round(total * 0.02)):
            self.simulation_status = "Collapsed"
            if self.infection_casualties >= self.strike_casualties:
                self.collapse_cause = "Infection and exposure exhausted the remaining population."
            else:
                self.collapse_cause = "Civilian casualties from air support exceeded infection losses."
            if self.auto_collapse:
                self.end_reason = "POPULATION COLLAPSE"
                self.screen_name = "analysis"
            return
        if zombies == 0 and self.infected_count == 0:
            self.simulation_status = "Contained"
        elif lost_or_rescued / total >= 0.65 or zombies >= max(1, self.population_agents // 3):
            self.simulation_status = "Critical"
        else:
            self.simulation_status = "Spreading"

    def _stop_simulation(self) -> None:
        if self.screen_name != "active":
            return
        self.end_reason = "MANUAL STOP"
        self._update_simulation_status()
        if self.screen_name == "active":
            self.screen_name = "analysis"

    def _start_simulation(self) -> None:
        self.screen_name = "active"
        self.simulation_status = "Spreading"
        self.end_reason = ""
        self.phase_time = 0.0
        self._eliminated = 0
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
                self._begin_operation(city)
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
        alive = self.zombies["hp"] > 0
        self._eliminated = getattr(self, "_eliminated", 0) + int(len(alive) - np.count_nonzero(alive))
        self.zombies = {key: values[alive] for key, values in self.zombies.items()}
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
            speed = np.full(zombie_count, speed_by_behavior.get(self.zombie_behavior, 42.0), dtype=np.float32)
            speed[route_narrow] *= 0.58
            for barricade in (unit for unit in self.units if unit["kind"] == "BARRICADE"):
                near = (zombies["x"] - barricade["x"]) ** 2 + (zombies["y"] - barricade["y"]) ** 2 < 38 ** 2
                speed[near] *= 0.28
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
            self.casualties += converted_count * self.people_per_agent
            self.infection_casualties += converted_count * self.people_per_agent
            keep = ~converted
            self.civilians = {key: values[keep] for key, values in crowd.items()}
            zombies = self.zombies
            self.zombies = {
                "x": np.concatenate((zombies["x"], converted_x)),
                "y": np.concatenate((zombies["y"], converted_y)),
                "hp": np.concatenate((zombies["hp"], np.full(converted_count, 40, dtype=np.float32))),
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
            self.target_mode = None
            return
        if button != 1:
            return
        if self.screen_name == "menu":
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
            if self.target_mode and MAP_RECT.collidepoint(point):
                if self.target_mode == "origin" and self.screen_name == "deployment":
                    origin_x, origin_y = self._world_point(point)
                    self.infection_origin = (origin_x / WORLD_W, origin_y / WORLD_H)
                    self.patient_zero = self.infection_origin
                    self.zombies = {"x": np.empty(0, dtype=np.float32), "y": np.empty(0, dtype=np.float32), "hp": np.empty(0, dtype=np.float32)}
                    normalized_x, normalized_y = self.infection_origin
                    initial_count = max(1, round(self.population_agents * self.initial_infection_percent / 100))
                    angles = np.arange(initial_count, dtype=np.float32) * (math.tau / initial_count)
                    self.zombies = {
                        "x": (normalized_x * WORLD_W + np.cos(angles) * 12).astype(np.float32),
                        "y": (normalized_y * WORLD_H + np.sin(angles) * 12).astype(np.float32),
                        "hp": np.full(initial_count, 40, dtype=np.float32),
                    }
                    self.zombies["route_node"] = self._snap_agents_to_roads(self.zombies["x"], self.zombies["y"])
                    self.zombies["route_narrow"] = np.zeros(initial_count, dtype=bool)
                    self.target_mode = None
                    self._notify(f"PATIENT ZERO POSITIONED · {len(self.zombies['x']):,} INITIAL INFECTED")
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
                        selected = True
                        break
                if not selected and MAP_RECT.collidepoint(point):
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
                        self._deploy(point)
                elif self.target_mode:
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
        elif event.type == pygame.MOUSEBUTTONDOWN:
            point = self._screen_position(event)
            self.mouse = point
            self._click(point, event.button)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging_radius = False
        elif event.type == pygame.KEYDOWN:
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
            elif self.screen_name in ("deployment", "active"):
                if event.key == pygame.K_ESCAPE:
                    self.target_mode = None
                elif event.key == pygame.K_SPACE and self.screen_name == "active":
                    self.phase_time = -1 if self.phase_time >= 0 else 0
                elif pygame.K_1 <= event.key <= pygame.K_6 and self.screen_name == "deployment":
                    self.selected_unit = list(UNITS)[event.key - pygame.K_1]
                elif pygame.K_F1 <= event.key <= pygame.K_F4:
                    mode = ("risk", "horde", "exposed", "survivors")[event.key - pygame.K_F1]
                    self.intel_modes[mode] = not self.intel_modes[mode]
                    self._refresh_intelligence(self.intel_grids)

    def _set_radius_from_x(self, x: int) -> None:
        slider = self.hit_targets.get("radius_slider")
        if not slider:
            return
        fraction = max(0.0, min(1.0, (x - slider.x) / slider.w))
        self.radius_km = 0.35 + fraction * (2.0 - 0.35)

    def _render(self) -> None:
        self.hit_targets = {}
        if self.screen_name == "menu":
            self._draw_menu()
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
        frame = pygame.transform.smoothscale(self.canvas, (target_w, target_h))
        self.screen.blit(frame, ((width - target_w) // 2, (height - target_h) // 2))
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
    from unittest.mock import patch

    pygame.init()
    city = schematic_map("Test City Blocks", 7)
    city.population = 30_000
    assert len(city.roads) >= 10 and len(city.buildings) >= 20
    assert len(city.buildings) == len(city.building_weights)
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
    assert route_failure_game.route_failure_agents == 8 and route_failure_game.route_failure_locations
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

    contained_game = Deadgrid()
    contained_game._begin_operation(CityMap("Containment test", population=10_000))
    contained_game.zombies = {"x": np.empty(0, dtype=np.float32), "y": np.empty(0, dtype=np.float32), "hp": np.empty(0, dtype=np.float32), "route_node": np.empty(0, dtype=np.int32), "route_narrow": np.empty(0, dtype=bool)}
    contained_game._start_simulation()
    contained_game._update(0.05)
    assert contained_game.screen_name == "active" and contained_game.simulation_status == "Contained"
    contained_game._stop_simulation()
    assert contained_game.screen_name == "analysis" and contained_game.end_reason == "MANUAL STOP"
    collapse_game = Deadgrid()
    collapse_game._begin_operation(CityMap("Collapse test", population=1000))
    collapse_game._start_simulation()
    collapse_game.casualties = collapse_game.represented_population
    collapse_game._update_simulation_status()
    assert collapse_game.screen_name == "analysis" and collapse_game.simulation_status == "Collapsed"
    game._draw_operation(False)
    game._click(game.hit_targets["origin"].center, 1)
    assert game.target_mode == "origin"
    game._click((MAP_RECT.x + MAP_RECT.w // 4, MAP_RECT.y + MAP_RECT.h // 3), 1)
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