# DEADGRID

![DEADGRID logo](DeadGrid_%20Gritty%20Urban%20Target%20Logo.png)

DEADGRID is a top-down, continuous city outbreak simulator built with Python and pygame. Choose a real place, tune the population and outbreak, then intervene while the world keeps evolving. The map uses OpenStreetMap streets and building footprints, supplemented with public Overture Maps building data when available. If map services are unavailable, a generated city keeps the simulation playable.

## Run

Python 3.10 or newer is recommended.

```bash
python -m pip install -r requirements.txt
python deadgrid.py
```

## Standalone build

Install dependencies and build on the same operating system you want to run:

```bash
bash build_linux.sh
```

If your default Python was built without a shared library, the Linux build script
now auto-detects a compatible system Python such as `/usr/bin/python3.12` and
falls back to `PYTHON_BIN=/path/to/python3.12 bash build_linux.sh` when you want
an explicit override.

The Linux executable is written to `dist/linux/DEADGRID`. On Windows, run
`build_windows.bat`; it creates `dist\windows\DEADGRID.exe`. PyInstaller does
not cross-compile Windows executables from Linux, so the `.exe` build must run
on Windows. The batch file detects `python` or the `py` launcher and pauses with
the error visible if a dependency or build step fails. Launching either build
works offline with the generated city; live
place lookup still requires internet access. The standalone build includes the
core game dependencies; the optional Overture/geospatial supplement remains
available when running from source with `requirements.txt`. Saved scenarios go
in the user's platform-specific config directory when running a packaged build.

The opening screen is an interactive, dark world map. Search for a place or choose a suggestion to move the camera there; drag to pan, scroll or use the on-map controls to zoom, and click the map to set a custom operation center. The green radius marker follows the selected center. Choose the operation radius and starting infection share, then select **Start Operation** to load streets and buildings and proceed directly to deployment. **World Settings** configures population density, zombie behavior, starting resources, automatic collapse detection, and named scenario saves.

Map tiles are cached locally for offline reuse. The world map uses OpenStreetMap's standard tile service (no API key required), recolored to fit the dark interface; geocoding and operation map data use OpenStreetMap's Nominatim and Overpass services and require internet access when not cached. Tile requests are throttled and requests follow the OpenStreetMap tile usage policy. Overture building data is fetched on demand and may take longer than the OSM street query. OSM data is available under the Open Database License; Overture Maps data is available under the Overture Maps Foundation's terms.

City maps use a native dark palette: near-black streets, uniform building footprints, subdued parks, and deep blue water. Buildings and mapped water bodies block civilian and zombie movement; major waterways block movement too, while mapped streams, brooks, ditches, and drains remain passable. Road routes are filtered against those obstacles before use. Map panels preserve the selected area's geographic aspect ratio, and road/building detail scales with the ground area shown.

## Population model

The selected-area radius defaults to 1.5 km and is adjustable from 0.35 to 2 km around the chosen map center. DEADGRID requests the selected place's OSM population tag and boundary, then estimates the selected area's population using the circle's share of the named place's bounding-box area. This is an area-share estimate, not a census for the exact circle; it assumes even population density within the named place. If a place has no population tag, its Overture/OSM building count multiplied by an 18-residents-per-footprint assumption provides a clearly labeled rough fallback. The briefing reports the data source and estimated area population.

The simulation creates up to 50,000 population agents, one per estimated resident until that cap is reached. A density multiplier scales the selected area's population estimate. NumPy-backed arrays and spatial grids update and render the crowd in batches. Residents are sampled from building footprints weighted by type, floor count, and area: apartments/dormitories/residential buildings attract the most residents; houses less; offices, retail, and industrial footprints substantially less. Overture properties are used when present; otherwise OSM building tags and floor counts supply the weights.

Choose a 0.5%, 1%, 2%, or 5% initial infected share, then place Patient Zero during setup. Zombies follow one-way-aware street routes toward populated blocks. Nearby exposure accumulates over time; exposed civilians incubate before joining the horde. Uninfected civilians are white; infected civilians and zombies are green. Live status is **Spreading**, **Critical**, **Contained**, or **Collapsed**. Eliminating every zombie changes the status to Contained but does not end the simulation.

Zombies route through connected street waypoints and intersections toward populated blocks. Road classes and one-way tags influence the route graph; narrow streets slow the horde. Barricades are brown, destructible wall segments: zombies that reach a wall attack it, the color darkens and cracks appear as durability falls, and destroyed segments open a breach that triggers route recalculation. Horde attacks are distributed with variation but favor nearby weakened segments; zombies unable to reach a wall continue toward alternate reachable road routes and gaps. Buildings and major water features are collision obstacles for both wandering and routed agents. The map intelligence controls toggle infection risk, horde density, exposed civilians, and estimated survivors; `F1`–`F4` toggle the same layers.

## Field controls

- During deployment, click an asset on the right and click the map to place it. Keys `1`–`6` select the original ground assets.
- Zoom the deployment/live map with the mouse wheel over the map, the `+`/`−` keys, or the on-map controls. Zoom is centered on the pointer with the wheel; middle-drag to pan.
- On the world-map start screen, search or choose a suggestion to recenter the map. Drag to pan, scroll or use `+`/`−` to zoom, and click the map to move the operation center. Set the radius and initial horde, then choose **Start Operation** to load the selected area and open deployment.
- The game opens in a 1920×1080 window by default and renders the interface at the window's actual pixel resolution; text, controls, and map geometry are not enlarged from a smaller full-frame canvas. Press `F10` to switch to the compact window size or back to 1080p, and `F11` to toggle fullscreen. **World Settings** configures density, behavior, resources, auto-collapse, and named scenario saves.
- During deployment, use **Set Outbreak Origin** to place Patient Zero.
- Start the simulation; it continues until you stop it or optional collapse detection triggers when at most 2% of residents remain.
- Click **Stop Simulation** to open the analysis view. Restart the same scenario or return to parameters; a contained outbreak remains live until you choose.
- During a run, deploy or reposition combat units, place new extraction stations, establish capacity-limited safe zones, or request supply drops. A supply drop costs $100 and delivers $400 after eight simulation seconds.
- The emergency policy switches among EVACUATE, SHELTER, and FLEE. Civilians route to stations or safe zones, flee nearby horde pressure when no destination is reachable, and are rescued only after reaching an extraction station.
- Air strikes damage civilians in their blast radius. Drag to lay brown barricade walls; a click places a short wall. Walls stop zombies until their durability is exhausted; concentrated hordes can break weak sections and create breaches. Use risk, horde, exposed, and survivor overlays to track the evolving situation.
- Right-click or `Escape` cancels an air-support targeting order. `Space` pauses or resumes. The stop control in the header ends the run for analysis.
- Analysis reports outbreak origin, population outcomes, route failures, supply deliveries, and likely collapse cause.

Map lookup uses OpenStreetMap's Nominatim and Overpass services. Please use the search responsibly and follow their usage policies. Install `overturemaps` through `requirements.txt` to enable the Overture building-data supplement. Named scenarios are stored locally in `deadgrid_scenarios.json`.

## Offline check

```bash
SDL_VIDEODRIVER=dummy python deadgrid.py --self-test
```