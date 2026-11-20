# Task 5 · Mission compiler with 4D trajectories (design phase)

Goal (topic sheet): parse the Mission JSON and translate each UAS's tasks into **collision-free 4D trajectories**
using [4DPlanning](https://github.com/amathsow/4DPlanning) (Sow et al., AAMAS 2026), then verify them in Webots.

Phase A is done: [`pipeline/fly_mission.py`](../pipeline/fly_mission.py) separates drones by altitude layers.
Phase B (this folder) replaces layers by planned 4D paths.

## 4DPlanning in short

- C++17; needs Boost (`program_options`) and Eigen. Build: `cmake -DCMAKE_BUILD_TYPE=Release . && make`.
- **Input:** a voxel map (`.3dmap`: size, obstacles, time-limited no-fly zones) and a scenario (`.3dscen`:
  per agent start cell, goal cell, start time, radius, speed).
- **Output:** per agent one cell per time step from its start time; a repeated cell means waiting.
- **Method:** prioritised planning, then large neighbourhood search to repair conflicts.

```bash
./preflight_plan -m map.3dmap -a scen.3dscen -k N -t T -o out --outputPaths paths.txt \
                 --roundTrip false --initLNS true --enablePruning true --screen 1
```

## Planned converter (`planner4d.py`)

1. Write a square voxel map of the hall (proposal: 0.1 m voxels, about 40 × 40 × 15 cells for x, y in [−2, 2] m, z in [0, 1.5] m).
2. Turn each `goto` leg into a planner agent with a start time from the mission schedule.
3. Run `preflight_plan`.
4. Convert cell paths back to metres and seconds (t = (start + i) · Δt, proposal Δt ≈ 0.5 s).
5. Compress to the waypoints where direction changes or waiting starts.
6. Check pairwise separation independently of the planner.
7. Execute with a common start time t0 and timed `navigate` commands.

## Open questions (to agree with the supervisor)

- **Several legs per drone:** the planner plans one start and one goal per agent and keeps an agent at its goal
  after arrival; a delivery has several legs (start → depot → destination). Options: one agent per leg with
  separate pickup cells, leg-by-leg planning, or extending the planner to goal chains.
- Realistic voxel size and time step for the hall.
- Downwash (air pushed down by a drone above) is not modelled; the planner treats drones as spheres.

## Status 20 Nov 2026: phase B implemented

| File | What it does |
|---|---|
| `planner4d.py` | Mission JSON → legs (one planner agent per `goto`) → 4DPlanning → **own separation check** → delay repair → timed waypoints, or REFUSED |
| `fly_4d.py` | Flies `trajectories.json` in Webots with a common start time; logs real minimum distance and tracking error |
| `check_paths.py` | Stand-alone separation check for 4DPlanning path files |
| `cross_test_mission.json` | Two drones whose legs cross at the same time |

**Finding:** 4DPlanning can report "0 colliding pairs" while its path file still puts two drones in the
same cube at the same time (minimal example: two crossing agents, start time 0; confirmed by the
authors' own `simulate_paths`). The compiler therefore treats the planner as a path generator and decides
safety itself (fail closed).

First results (cross_test_mission, real 4DPlanning): conflict at 0.0 m → 1 repair (1.2 s delay) →
closest 0.32 m for 0.3 m required, planning time 0.17 s. With 0.5 m required: 2 repairs, 2.4 s delay, closest 0.6 m.

Build notes: `sudo apt install libboost-all-dev libeigen3-dev cmake`; delete the author's `CMakeCache.txt`,
`CMakeFiles`, `Makefile` and the prebuilt `preflight_plan` before `cmake -DCMAKE_BUILD_TYPE=Release . && make`.

**First flight in Webots (20 Nov 2026, cross_test_mission, --sep 0.4):** flown as planned, real minimum
distance 0.71 m (planned 0.60 m), tracking error mean 0.09 m / max 0.41 m, no delay behind the plan;
51 s of simulated flight took 767 s real time (real-time factor ≈ 0.07, software rendering).

**Second finding:** 4DPlanning writes paths in absolute time (an agent with start time t0 gets t0-1 copies of
its start cell in front). Read as relative paths, every later leg waited twice (cf233 waited 10.8 s instead of
3.2 s at the depot). Safety was not affected, because the flight followed exactly the timeline we had
verified; only time was lost. Fixed in `absolute()`.
