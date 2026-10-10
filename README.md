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

Choose a suggested city or search for any place by name. Open **World Settings** before loading the map to change density, zombie behavior, starting resources, and automatic collapse detection. Save multiple named scenarios and select one later. Map requests need internet access. Overture data is fetched on demand and may take longer than the OSM street query. Map attribution is shown in-game. OSM data is available under the Open Database License; Overture Maps data is available under the Overture Maps Foundation's terms.

## Population model

The selected-area radius defaults to 1.5 km and is adjustable from 0.35 to 2 km around the geocoded place center. DEADGRID requests the place's OSM population tag and boundary, then estimates the selected area's population using the circle's share of the named place's bounding-box area. This is an area-share estimate, not a census for the exact circle; it assumes even population density within the named place. If a place has no population tag, its Overture/OSM building count multiplied by an 18-residents-per-footprint assumption provides a clearly labeled rough fallback. The briefing reports the data source and estimated area population.

The simulation creates up to 50,000 population agents, one per estimated resident until that cap is reached. A density multiplier scales the selected area's population estimate. NumPy-backed arrays and spatial grids update and render the crowd in batches. Residents are sampled from building footprints weighted by type, floor count, and area: apartments/dormitories/residential buildings attract the most residents; houses less; offices, retail, and industrial footprints substantially less. Overture properties are used when present; otherwise OSM building tags and floor counts supply the weights.

Choose a 0.5%, 1%, 2%, or 5% initial infected share, then place Patient Zero during setup. Zombies follow one-way-aware street routes toward populated blocks. Nearby exposure accumulates over time; exposed civilians incubate before joining the horde. Uninfected civilians are white; infected civilians and zombies are green. Live status is **Spreading**, **Critical**, **Contained**, or **Collapsed**. Eliminating every zombie changes the status to Contained but does not end the simulation.

Zombies route through connected street waypoints and intersections toward populated blocks. Road classes and one-way tags influence the route graph; bridge crossings remain chokepoints, narrow streets slow the horde, and barricades make nearby edges expensive so it seeks alternate routes. The map intelligence controls toggle infection risk, horde density, exposed civilians, and estimated survivors; `F1`–`F4` toggle the same layers.

## Field controls

- During deployment, click an asset on the right and click the map to place it. Keys `1`–`6` select the original ground assets.
- In the briefing, search for a place to open its area-selection map. Click to position the operation, set its radius, use the mouse wheel or `+`/`−` to zoom, right-drag to pan, then choose **Use This Area**. Only the selected area is converted into the deployed map.
- Press `F10` to toggle a 1080p window and `F11` to toggle fullscreen. **World Settings** configures density, behavior, resources, auto-collapse, and named scenario saves.
- During deployment, use **Set Outbreak Origin** to place Patient Zero.
- Start the simulation; it continues until you stop it or optional collapse detection triggers when at most 2% of residents remain.
- Click **Stop Simulation** to open the analysis view. Restart the same scenario or return to parameters; a contained outbreak remains live until you choose.
- During a run, deploy or reposition combat units, place new extraction stations, establish capacity-limited safe zones, or request supply drops. A supply drop costs $100 and delivers $400 after eight simulation seconds.
- The emergency policy switches among EVACUATE, SHELTER, and FLEE. Civilians route to stations or safe zones, flee nearby horde pressure when no destination is reachable, and are rescued only after reaching an extraction station.
- Air strikes damage civilians in their blast radius. Barricades slow and divert horde and civilian routes. Use risk, horde, exposed, and survivor overlays to track the evolving situation.
- Right-click or `Escape` cancels an air-support targeting order. `Space` pauses or resumes. The stop control in the header ends the run for analysis.
- Analysis reports outbreak origin, population outcomes, route failures, supply deliveries, and likely collapse cause.

Map lookup uses OpenStreetMap's Nominatim and Overpass services. Please use the search responsibly and follow their usage policies. Install `overturemaps` through `requirements.txt` to enable the Overture building-data supplement. Named scenarios are stored locally in `deadgrid_scenarios.json`.

## Offline check

```bash
SDL_VIDEODRIVER=dummy python deadgrid.py --self-test
```