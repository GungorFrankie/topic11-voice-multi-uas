#!/usr/bin/env python3
"""
planner4d.py - Task 5, phase B: Mission JSON -> verified 4D trajectories.

    Mission JSON (WHAT) + world.json
        -> legs        every 'goto' becomes one planner agent with a start time
        -> 4DPlanning  writes .3dmap / .3dscen, runs preflight_plan, reads the paths
        -> verify      OUR OWN separation check over the whole timeline of every drone
        -> repair      conflict? delay the later leg by a few steps and plan again
        -> output      timed waypoints per drone (metres, seconds) or REFUSED (fail closed)

Why our own check: on 20 Nov 2026 we found that 4DPlanning can report "0 colliding pairs"
while its path file still puts two drones in the same cube at the same time
(two crossing drones, both start time 0). The planner is therefore used to generate paths,
and the decision "safe or not" is made here.

Run:
    python3 planner4d.py ../pipeline/examples/valid/v2_two_deliveries_then.json \
        --planner ~/4DPlanning/preflight_plan
    python3 planner4d.py MISSION.json --planner straight      # no 4DPlanning: straight-line legs
Options: --sep 0.3 (minimum distance in m), --dt, --res, --max-repairs, --out DIR, --plot
"""
import argparse
import itertools
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORLD = HERE.parent / "pipeline" / "world.json"

# ---------------------------------------------------------------- grid and time (proposal, see README)
HALL = dict(xmin=-2.0, xmax=2.0, ymin=-2.0, ymax=2.0, zmax=1.5)
RES = 0.1                      # [m] edge of one cube (voxel)
CRUISE_SPEED = 0.25            # [m/s] same as fly_mission.py
CRUISE_Z = 0.5                 # [m] cruise altitude of all legs (the planner separates in 3D itself)
TAKEOFF_S, SERVICE_S, LAND_S = 3.0, 3.0, 3.0
PLAN_RADIUS = 1                # [cubes] radius given to the planner (its units are not documented)


class Grid:
    def __init__(self, res):
        self.res = res
        self.nx = int(round((HALL["xmax"] - HALL["xmin"]) / res)) + 1
        self.ny = int(round((HALL["ymax"] - HALL["ymin"]) / res)) + 1
        self.nz = int(round(HALL["zmax"] / res)) + 1

    def cell(self, x, y, z):
        c = (int(round((x - HALL["xmin"]) / self.res)), int(round((y - HALL["ymin"]) / self.res)),
             int(round(z / self.res)))
        if not (0 <= c[0] < self.nx and 0 <= c[1] < self.ny and 0 <= c[2] < self.nz):
            raise ValueError(f"point ({x}, {y}, {z}) is outside the hall")
        return c

    def metres(self, c):
        return (round(HALL["xmin"] + c[0] * self.res, 3), round(HALL["ymin"] + c[1] * self.res, 3),
                round(c[2] * self.res, 3))


# ---------------------------------------------------------------- 1  mission -> per-drone task lists and legs
def build_program(mission, world, grid, steps):
    """Per drone: the ordered task list, with every goto turned into a leg (start cell, goal cell)."""
    place = {p["name"]: (p["x"], p["y"]) for p in world["places"]}
    pos = {u["id"]: (u["position"]["x"], u["position"]["y"]) for u in world["uas"]}
    program, legs = {}, []
    for t in mission["tasks"]:
        u = t["uas"]
        prog = program.setdefault(u, {"tasks": [], "here": pos[u]})
        item = {"id": t["id"], "action": t["action"], "after": t.get("after", [])}
        if t["action"] == "goto":
            a, b = prog["here"], place[t["target"]]
            item["leg"] = len(legs)
            legs.append({"uas": u, "task": t["id"], "target": t["target"],
                         "start": grid.cell(*a, CRUISE_Z), "goal": grid.cell(*b, CRUISE_Z)})
            prog["here"] = b
        elif t["action"] in ("takeoff",):
            item["hold"] = steps["takeoff"]
        elif t["action"] in ("pickup", "dropoff"):
            item["hold"] = steps["service"]
        elif t["action"] == "hold":
            item["hold"] = max(1, round(t["duration_s"] / steps["dt"]))
        elif t["action"] == "land":
            item["hold"] = steps["land"]
        prog["tasks"].append(item)
    for u, prog in program.items():
        prog["home"] = grid.cell(*pos[u], CRUISE_Z)
    return program, legs


# ---------------------------------------------------------------- 2  schedule: start time of every leg
def absolute(path, t0):
    """Path with index = time step. 4DPlanning writes paths in absolute time: an agent with start time
    t0 > 0 gets t0-1 extra copies of its start cell in front (found 20 Nov 2026 in Webots: reading them as
    relative made every later leg wait twice). A relative path (straight fallback) is padded the same way."""
    lead = next((i for i, c in enumerate(path) if c != path[0]), len(path))
    if t0 >= 2 and lead >= t0 - 1:
        return [path[0]] + path                    # planner file: index i is time i+1
    return [path[0]] * t0 + path



def schedule(program, legs, paths, delay):
    """Walk the task lists in time. A leg starts when the previous task of the same drone is done,
    all its 'after' tasks are done, and its extra delay (from repairs) has passed.
    Returns task end times and the start step of every leg. Fixed-point loop for 'after'."""
    end, begin = {}, {}
    for _ in range(50):
        changed = False
        for u, prog in program.items():
            t = 0
            for it in prog["tasks"]:
                ready = max([t] + [end.get(a, math.inf) for a in it["after"]])
                if math.isinf(ready):
                    break                                   # waits for a task not yet timed
                if "leg" in it:
                    k = it["leg"]
                    start = ready + delay.get(k, 0)
                    legs[k]["t0"] = start
                    t = max(start, len(absolute(paths[k], start)) - 1)
                else:
                    begin[it["id"]] = ready
                    t = ready + it.get("hold", 0)
                if end.get(it["id"]) != t:
                    end[it["id"]], changed = t, True
        if not changed:
            end["_begin"] = begin
            return end
    raise RuntimeError("schedule does not settle (circular 'after'?)")


# ---------------------------------------------------------------- 3  path generation
def straight_path(a, b):
    """Fallback without 4DPlanning: axis steps that follow the straight line (one cube per step)."""
    path, cur = [a], list(a)
    while tuple(cur) != b:
        d = [b[i] - cur[i] for i in range(3)]
        i = max(range(3), key=lambda j: abs(d[j]))
        cur[i] += 1 if d[i] > 0 else -1
        path.append(tuple(cur))
    return path


def run_4dplanning(exe, legs, grid, workdir, cutoff):
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "hall.3dmap").write_text(f"{grid.nx} {grid.ny} {grid.nz}\n0\n0\n")
    lines = [f"{grid.nx} {grid.ny} {grid.nz}", str(len(legs))]
    for g in legs:
        lines.append(" ".join(map(str, (*g["start"], *g["goal"], g["t0"], PLAN_RADIUS, 1, 0))))
    (workdir / "legs.3dscen").write_text("\n".join(lines) + "\n")
    cmd = [str(exe), "-m", str(workdir / "hall.3dmap"), "-a", str(workdir / "legs.3dscen"),
           "-k", str(len(legs)), "-t", str(cutoff), "--roundTrip", "0", "--screen", "0",
           "-o", str(workdir / "result"), "--outputPaths", str(workdir / "paths")]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=cutoff + 30)
    if r.returncode != 0 or not (workdir / "paths").exists():
        raise RuntimeError(f"4DPlanning failed (exit {r.returncode}): {r.stderr[-300:]}")
    paths = {}
    for line in (workdir / "paths").read_text().splitlines():
        m = re.match(r"Agent (\d+) \[[^\]]*\][^:]*:(.*)", line.strip())
        if m:
            paths[int(m.group(1))] = [tuple(map(int, c)) for c in
                                      re.findall(r"\((-?\d+),(-?\d+),(-?\d+)\)", m.group(2))]
    if len(paths) != len(legs):
        raise RuntimeError(f"4DPlanning returned {len(paths)} paths for {len(legs)} legs")
    return [paths[i] for i in range(len(legs))]


# ---------------------------------------------------------------- 4  independent verification
def timelines(program, legs, paths, end):
    """Cell of every drone at every time step, from takeoff until landing (None = on the ground)."""
    tl = {}
    for u, prog in program.items():
        cells, here, t = {}, prog["home"], 0
        for it in prog["tasks"]:
            if "leg" in it:
                g = legs[it["leg"]]
                ap = absolute(paths[it["leg"]], g["t0"])   # index = time; before t0 it is the start cell
                for s in range(t, len(ap)):
                    cells[s] = ap[s]
                here = ap[-1]
            else:
                # before takeoff the drone is on the ground (not in the timeline);
                # it then hovers through every waiting time until it lands
                first = end["_begin"][it["id"]] if it["action"] == "takeoff" else t
                for s in range(first, end[it["id"]] + 1):
                    cells[s] = here
            t = end[it["id"]]
        tl[u] = cells
    return tl


def verify(tl, min_cubes):
    """All pairs of drones, all time steps: Euclidean distance between cube centres >= min_cubes."""
    worst, conflicts = (math.inf, None, None), []
    for a, b in itertools.combinations(sorted(tl), 2):
        for t in sorted(set(tl[a]) & set(tl[b])):
            d = math.dist(tl[a][t], tl[b][t])
            if d < worst[0]:
                worst = (d, t, (a, b))
            if d < min_cubes:
                conflicts.append((t, a, b, d))
    return conflicts, worst


def leg_at(program, legs, u, t):
    """The leg of drone u that is active at (or the last one before) time t, for the repair."""
    best = None
    for it in program[u]["tasks"]:
        if "leg" in it and legs[it["leg"]]["t0"] <= t:
            best = it["leg"]
    if best is None:
        best = next(it["leg"] for it in program[u]["tasks"] if "leg" in it)
    return best


# ---------------------------------------------------------------- 5  output
def compress(tl_u, grid, dt, tol=0.75):
    """Timed waypoints in metres. The planner moves one cube per step, so diagonal flight becomes a
    staircase of many tiny moves. We simplify in space-time (x, y, z, t*speed) with Ramer-Douglas-Peucker:
    a point is dropped when the straight, constant-speed segment passes within tol cubes of it.
    Waiting stays exact, because time is one of the four coordinates."""
    ts = sorted(tl_u)
    pts = [(*tl_u[t], float(t)) for t in ts]           # 4D: cubes and steps (1 step = 1 cube at cruise speed)

    def rdp(lo, hi):
        if hi - lo < 2:
            return [lo, hi]
        a, b = pts[lo], pts[hi]
        seg = [q - p for p, q in zip(a, b)]
        L2 = sum(v * v for v in seg) or 1e-9
        best, idx = -1.0, None
        for i in range(lo + 1, hi):
            w = [q - p for p, q in zip(a, pts[i])]
            f = max(0.0, min(1.0, sum(x * y for x, y in zip(w, seg)) / L2))
            d = math.dist(pts[i], [p + f * v for p, v in zip(a, seg)])
            if d > best:
                best, idx = d, i
        if best <= tol:
            return [lo, hi]
        return rdp(lo, idx)[:-1] + rdp(idx, hi)

    keep = rdp(0, len(pts) - 1)
    return [{"t": round(ts[i] * dt, 2), **dict(zip("xyz", grid.metres(tl_u[ts[i]])))} for i in keep]


def plot(result, out_png, world):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (no plot: sudo apt install -y python3-matplotlib)")
        return
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4.5))
    for p in world["places"]:
        a.plot(p["x"], p["y"], "s", color="0.6", markersize=9)
        a.annotate(p["name"], (p["x"], p["y"]), textcoords="offset points", xytext=(6, 6), fontsize=8)
    for u, wps in result["trajectories"].items():
        line, = a.plot([w["x"] for w in wps], [w["y"] for w in wps], ".-", label=u)
        b.plot([w["t"] for w in wps], [w["x"] for w in wps], "-", color=line.get_color(), label=f"{u} x")
    a.set_title("Planned paths, top view"); a.axis("equal"); a.grid(True); a.legend(fontsize=8)
    a.set_xlabel("x [m]"); a.set_ylabel("y [m]")
    b.set_title("x position over time"); b.set_xlabel("t [s]"); b.set_ylabel("x [m]"); b.grid(True)
    fig.tight_layout(); fig.savefig(out_png, dpi=150)
    print(f"  plot: {out_png}")


# ---------------------------------------------------------------- main loop
def compile4d(mission, world, planner="straight", sep=0.3, res=RES, max_repairs=20, delay_steps=3,
              workdir=Path("plan4d_work"), cutoff=10):
    grid = Grid(res)
    dt = res / CRUISE_SPEED
    steps = {"dt": dt, "takeoff": round(TAKEOFF_S / dt), "service": round(SERVICE_S / dt),
             "land": round(LAND_S / dt)}
    program, legs = build_program(mission, world, grid, steps)
    if not legs:
        raise ValueError("mission has no goto legs")
    min_cubes = sep / res
    delay, history = {}, []
    paths = [straight_path(g["start"], g["goal"]) for g in legs]      # first estimate of durations
    t_start = time.time()
    for it in range(max_repairs + 1):
        # start times depend on path lengths and path lengths on start times: settle both
        for _ in range(5):
            end = schedule(program, legs, paths, delay)
            if planner == "straight":
                new = [straight_path(g["start"], g["goal"]) for g in legs]
            else:
                new = run_4dplanning(Path(planner).expanduser(), legs, grid, workdir, cutoff)
            if new == paths:
                break
            paths = new
        end = schedule(program, legs, paths, delay)
        tl = timelines(program, legs, paths, end)
        conflicts, worst = verify(tl, min_cubes)
        history.append({"iteration": it, "conflict_steps": len(conflicts),
                        "min_distance_m": round(worst[0] * res, 3)})
        if not conflicts:
            return {"status": "OK", "dt_s": dt, "res_m": res, "min_sep_m": sep,
                    "min_distance_m": round(worst[0] * res, 3), "closest_pair": worst[2],
                    "closest_at_s": round(worst[1] * dt, 2), "repairs": it,
                    "planning_time_s": round(time.time() - t_start, 3),
                    "delays_steps": {legs[k]["task"]: v for k, v in delay.items()},
                    "history": history,
                    "trajectories": {u: compress(tl[u], grid, dt) for u in tl}}
        # repair: first conflict in time -> delay the leg of the drone that started it later
        t, a, b, _ = min(conflicts)
        la, lb = leg_at(program, legs, a, t), leg_at(program, legs, b, t)
        k = la if (legs[la]["t0"], a) > (legs[lb]["t0"], b) else lb
        delay[k] = delay.get(k, 0) + delay_steps
    return {"status": "REFUSED", "reason": f"no conflict-free plan after {max_repairs} repairs",
            "min_sep_m": sep, "history": history,
            "planning_time_s": round(time.time() - t_start, 3)}


def main():
    ap = argparse.ArgumentParser(description="Mission JSON -> verified 4D trajectories (Task 5).")
    ap.add_argument("mission")
    ap.add_argument("--world", default=str(WORLD))
    ap.add_argument("--planner", default="straight", help="path to preflight_plan, or 'straight'")
    ap.add_argument("--sep", type=float, default=0.3, help="minimum distance between drones [m]")
    ap.add_argument("--res", type=float, default=RES, help="cube size [m]")
    ap.add_argument("--max-repairs", type=int, default=20)
    ap.add_argument("--out", default="plan4d_out")
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()
    mission = json.loads(Path(args.mission).read_text())
    world = json.loads(Path(args.world).read_text())
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    print(f"Mission {mission.get('mission_id')}: {len(mission.get('tasks', []))} tasks, planner = {args.planner}")
    res = compile4d(mission, world, args.planner, args.sep, args.res, args.max_repairs,
                    workdir=out / "work")
    for h in res["history"]:
        print(f"  iteration {h['iteration']}: {h['conflict_steps']} conflict steps, "
              f"closest {h['min_distance_m']} m")
    (out / "trajectories.json").write_text(json.dumps(res, indent=2))
    if res["status"] != "OK":
        print(f"REFUSED: {res['reason']} (fail closed, nothing is flown)")
        return 1
    print(f"OK after {res['repairs']} repairs: closest {res['min_distance_m']} m "
          f"({res['closest_pair'][0]}-{res['closest_pair'][1]} at {res['closest_at_s']} s), "
          f"required {res['min_sep_m']} m, planning time {res['planning_time_s']} s")
    if res["delays_steps"]:
        print(f"  delays added: { {k: f'{v * res['dt_s']:.1f} s' for k, v in res['delays_steps'].items()} }")
    for u, w in res["trajectories"].items():
        print(f"  {u}: {len(w)} waypoints, {w[0]['t']} s -> {w[-1]['t']} s")
    print(f"  written: {out / 'trajectories.json'}")
    if args.plot:
        plot(res, out / "trajectories.png", world)
    return 0


if __name__ == "__main__":
    sys.exit(main())
