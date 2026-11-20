#!/usr/bin/env python3
"""
fly_mission.py - fly one Mission JSON in the simulator.

This is the last step of Task 2 ("a hand-written mission flies") and the first,
simple version of the mission compiler (phase A of Task 5):

  Mission JSON (WHAT)  ->  compile  ->  HTTP calls with heights and travel times (HOW)

How it compiles
  - every drone gets its own altitude layer (0.5 m, 0.8 m, 1.1 m, ...), so paths never meet;
  - goto  -> one timed 'navigate' call to the coordinates of the named place;
  - pickup / dropoff -> hover for a few seconds while the program books the parcel;
  - hold  -> hover for duration_s;   takeoff / land -> the matching calls;
  - 'after' -> the drone waits until the named tasks of other drones are finished.

It only makes the checks an executor needs (names exist, order can be executed).
The full validators with error feedback are Task 4.

Run (simulator must show READY):
    python3 fly_mission.py examples/valid/v2_two_deliveries_then.json
    python3 fly_mission.py my_mission.json --dry-run      # compile and print only, no flight
"""
import argparse
import csv
import json
import math
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
HOST, FIRST_PORT, FIRST_ID = "http://127.0.0.1", 5000, 231
BASE_LAYER, LAYER_STEP = 0.5, 0.3     # [m] altitude of the first drone, spacing between drones
CRUISE_SPEED, MIN_LEG_S = 0.25, 4.0   # [m/s] planned speed, [s] shortest leg duration
TAKEOFF_S, PARCEL_S = 3.0, 3.0        # [simulated s] takeoff duration, time to load / unload
AT_PLACE_TOL = 0.20                   # [m] horizontal distance that counts as "at the place"
STALL_LIMIT, STEP_TIMEOUT = 15.0, 120.0
AIRBORNE_Z = 0.25
print_lock = threading.Lock()


def say(who, text):
    with print_lock:
        print(f"[{who}] {text}", flush=True)


# ----------------------------------------------------------------------------- checks
def preflight(mission, world):
    """Checks the executor needs before it can fly. Returns a list of problems (empty = ok)."""
    problems = []
    try:
        sys.path.insert(0, str(HERE))
        from check_missions import schema_errors
        problems += [f"schema: {e}" for e in schema_errors(mission)]
    except ImportError:
        say("check", "python3-jsonschema not installed - schema check skipped")
    if problems:
        return problems
    uas = {u["id"] for u in world["uas"]}
    places = {p["name"] for p in world["places"]}
    parcels = {p["id"] for p in world["parcels"]}
    ids = [t["id"] for t in mission["tasks"]]
    for t in mission["tasks"]:
        if t["uas"] not in uas:
            problems.append(f"task {t['id']}: UAS {t['uas']} is not in the world model")
        if "target" in t and t["target"] not in places:
            problems.append(f"task {t['id']}: unknown place '{t['target']}'")
        if "parcel" in t and t["parcel"] not in parcels:
            problems.append(f"task {t['id']}: unknown parcel '{t['parcel']}'")
        for a in t.get("after", []):
            if a not in ids:
                problems.append(f"task {t['id']}: waits for {a}, which does not exist")
    if len(set(ids)) != len(ids):
        problems.append("task ids are not unique")
    per = {}
    for t in mission["tasks"]:
        per.setdefault(t["uas"], []).append(t)
    for u, ts in per.items():
        if ts[0]["action"] != "takeoff" or ts[-1]["action"] != "land":
            problems.append(f"{u}: tasks must start with takeoff and end with land")
    # the order must be executable: no cycle in "previous task of the same drone" + "after"
    need = {t["id"]: set(t.get("after", [])) for t in mission["tasks"]}
    for ts in per.values():
        for a, b in zip(ts, ts[1:]):
            need[b["id"]].add(a["id"])
    done = set()
    while True:
        ready = [i for i, n in need.items() if i not in done and n <= done]
        if not ready:
            break
        done.update(ready)
    if len(done) != len(need) and not problems:
        problems.append("tasks wait for each other in a circle (deadlock): " + ", ".join(sorted(set(need) - done)))
    return problems


# ----------------------------------------------------------------------------- compile
def compile_mission(mission, world):
    """Turn the mission (what) into steps per drone with coordinates, heights and durations (how)."""
    place = {p["name"]: (p["x"], p["y"]) for p in world["places"]}
    start = {u["id"]: (u["position"]["x"], u["position"]["y"]) for u in world["uas"]}
    plan, order = {}, []
    for t in mission["tasks"]:
        if t["uas"] not in plan:
            plan[t["uas"]] = []
            order.append(t["uas"])
    layer = {u: round(BASE_LAYER + i * LAYER_STEP, 2) for i, u in enumerate(order)}
    here = dict(start)
    for t in mission["tasks"]:
        u, a = t["uas"], t["action"]
        step = {"task": t["id"], "action": a, "after": t.get("after", [])}
        if a == "takeoff":
            layer[u] = t.get("altitude_m", layer[u])
            step.update(z=layer[u], duration=TAKEOFF_S)
        elif a == "goto":
            x, y = place[t["target"]]
            dist = math.dist(here[u], (x, y))
            step.update(x=x, y=y, z=layer[u], place=t["target"], duration=round(max(MIN_LEG_S, dist / CRUISE_SPEED), 1))
            here[u] = (x, y)
        elif a in ("pickup", "dropoff"):
            step.update(parcel=t["parcel"], duration=PARCEL_S)
        elif a == "hold":
            step.update(duration=t["duration_s"])
        plan[u].append(step)
    zs = sorted(layer.values())
    if any(b - a < 0.25 for a, b in zip(zs, zs[1:])):
        raise SystemExit(f"Altitude layers too close: {layer}. Remove altitude_m or keep at least 0.3 m between drones.")
    return plan, layer


def show_plan(plan, layer):
    print("\nCompiled plan (what -> how)")
    for u, steps in plan.items():
        print(f"  {u}  altitude layer {layer[u]} m")
        for s in steps:
            how = {"takeoff": f"begin_takeoff to {s.get('z')} m in {s.get('duration')} s",
                   "goto": f"navigate to {s.get('place')} ({s.get('x')}, {s.get('y')}, {s.get('z')}) in {s.get('duration')} s",
                   "pickup": f"hover {s.get('duration')} s, load {s.get('parcel')}",
                   "dropoff": f"hover {s.get('duration')} s, unload {s.get('parcel')}",
                   "hold": f"hover {s.get('duration')} s", "land": "begin_landing"}[s["action"]]
            wait = f"   [waits for {', '.join(s['after'])}]" if s["after"] else ""
            print(f"    {s['task']:>4} {s['action']:8s} -> {how}{wait}")


# ----------------------------------------------------------------------------- drone
class Drone:
    def __init__(self, name):
        self.name = name
        self.url = f"{HOST}:{FIRST_PORT + int(name[2:]) - FIRST_ID}"

    def status(self):
        return requests.get(f"{self.url}/status", timeout=5).json()

    def sim_time(self):
        return float(self.status().get("sim_time", time.time()))

    def position(self):
        return self.status()["telemetry"]["position"]

    def post(self, path, body=None):
        r = requests.post(f"{self.url}/{path}", json=body or {}, timeout=10)
        if r.status_code != 200:
            raise RuntimeError(f"{self.name}: POST /{path} refused ({r.status_code}): {r.text[:200]}")
        return r.json()

    def wait(self, condition, what, timeout=STEP_TIMEOUT):
        start = self.sim_time()
        last_sim, last_change = start, time.time()
        while True:
            s = self.status()
            if s.get("error") or s.get("state") == "error":
                raise RuntimeError(f"{self.name}: drone error while waiting for {what}: {s.get('error')!r}")
            if condition(s):
                return s
            now = float(s.get("sim_time", time.time()))
            if now != last_sim:
                last_sim, last_change = now, time.time()
            elif time.time() - last_change > STALL_LIMIT:
                raise RuntimeError(f"{self.name}: simulation clock stopped - is Webots paused or closed?")
            if now - start > timeout:
                raise TimeoutError(f"{self.name}: timed out waiting for {what} (phase {s.get('phase')})")
            time.sleep(0.1)

    def command(self, path, body, what, timeout=STEP_TIMEOUT):
        """Send a motion command and wait until the control service reports its task as completed."""
        reply = self.post(path, body)
        task_id = reply.get("task_id")
        if not task_id:                       # older service: fall back to the phase
            return self.wait(lambda s: s.get("phase") in ("hovering", "landed"), what, timeout)

        def finished(_):
            t = requests.get(f"{self.url}/tasks/{task_id}", timeout=5).json()
            if t.get("status") in ("failed", "cancelled"):
                raise RuntimeError(f"{self.name}: {what} {t.get('status')}: {t.get('error')}")
            return t.get("status") == "completed"
        return self.wait(finished, what, timeout)

    def hover(self, seconds):
        t0 = self.sim_time()
        self.wait(lambda s: float(s.get("sim_time", 0)) - t0 >= seconds, "hover time", seconds + STEP_TIMEOUT)


# ----------------------------------------------------------------------------- execute
def fly(plan, world, log_dir):
    drones = {u: Drone(u) for u in plan}
    place = {p["name"]: (p["x"], p["y"]) for p in world["places"]}
    parcel_at = {p["id"]: p["at"] for p in world["parcels"]}
    done = {s["task"]: threading.Event() for steps in plan.values() for s in steps}
    abort, events, results, book = threading.Event(), [], {}, threading.Lock()

    print("\nHealth check")
    for u, d in drones.items():
        s = d.status()
        pos = s["telemetry"]["position"]
        print(f"  {u}: connected={s.get('connected')} phase={s.get('phase')} position={[round(v, 2) for v in pos]}")
        if not s.get("connected") or s.get("error") or s.get("state") == "error":
            raise SystemExit(f"{u} is not ready. Restart the simulator and try again.")
        wu = next(x for x in world["uas"] if x["id"] == u)["position"]
        if math.dist(pos[:2], (wu["x"], wu["y"])) > 0.2:
            print(f"  note: {u} is not where world.json says ({wu['x']}, {wu['y']}); leg durations are planned from world.json")
    any_d = next(iter(drones.values()))
    t1 = any_d.sim_time(); time.sleep(2.0); t2 = any_d.sim_time()
    if t2 <= t1:
        raise SystemExit("The simulation clock is not advancing. Press play in Webots or restart the simulator.")
    print(f"  simulation clock is running (real-time factor {(t2 - t1) / 2:.2f})\n")

    def mark(u, task, text):
        events.append((drones[u].sim_time(), u, task, text))
        say(u, f"{task}: {text}")

    def nearest_place(u):
        x, y, _ = drones[u].position()
        name = min(place, key=lambda n: math.dist(place[n], (x, y)))
        return name if math.dist(place[name], (x, y)) <= AT_PLACE_TOL else None

    def run(u):
        d = drones[u]
        try:
            for s in plan[u]:
                for a in s["after"]:
                    if not done[a].is_set():
                        say(u, f"{s['task']}: waiting for {a}")
                    while not done[a].wait(0.2):
                        if abort.is_set():
                            raise RuntimeError("mission aborted")
                if abort.is_set():
                    raise RuntimeError("mission aborted")
                a = s["action"]
                if a == "takeoff":
                    if d.status().get("phase") not in ("idle", "landed"):
                        d.post("activate_idle")
                    mark(u, s["task"], f"takeoff to {s['z']} m")
                    d.command("begin_takeoff", {"height": s["z"], "duration": s["duration"]}, "takeoff")
                elif a == "goto":
                    mark(u, s["task"], f"goto {s['place']} in {s['duration']} s")
                    d.command("navigate", {"x": s["x"], "y": s["y"], "z": s["z"], "duration": s["duration"]},
                              f"arrival at {s['place']}", s["duration"] * 2 + STEP_TIMEOUT)
                elif a == "pickup":
                    where = nearest_place(u)
                    with book:
                        if parcel_at.get(s["parcel"]) != where:
                            raise RuntimeError(f"{s['task']}: parcel {s['parcel']} is at {parcel_at.get(s['parcel'])}, drone is at {where}")
                        parcel_at[s["parcel"]] = u
                    mark(u, s["task"], f"pickup {s['parcel']} at {where}")
                    d.hover(s["duration"])
                elif a == "dropoff":
                    where = nearest_place(u)
                    with book:
                        if parcel_at.get(s["parcel"]) != u:
                            raise RuntimeError(f"{s['task']}: {u} does not carry {s['parcel']}")
                        if where is None:
                            raise RuntimeError(f"{s['task']}: drone is not at a known place")
                    d.hover(s["duration"])
                    with book:
                        parcel_at[s["parcel"]] = where
                    mark(u, s["task"], f"dropoff {s['parcel']} at {where}")
                elif a == "hold":
                    mark(u, s["task"], f"hold {s['duration']} s")
                    d.hover(s["duration"])
                elif a == "land":
                    mark(u, s["task"], "land")
                    d.command("begin_landing", {}, "landing")
                done[s["task"]].set()
            results[u] = "OK"
        except Exception as e:
            results[u] = f"FAILED: {e}"
            say(u, results[u])
            abort.set()                               # fail safe: the whole mission stops
            try:
                if d.position()[2] > AIRBORNE_Z:
                    say(u, "landing after the failure")
                    d.post("stop_controller"); time.sleep(0.5); d.post("begin_landing")
            except Exception as e2:
                say(u, f"could not land: {e2}")

    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = f"{log_dir}/flight_{stamp}.csv"
    stop, least = threading.Event(), {"d": float("inf"), "pair": None}

    def logger():
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["wall_time", "sim_time", "drone", "phase", "x", "y", "z"])
            while not stop.is_set():
                sample = {}
                for u, d in drones.items():
                    try:
                        s = d.status()
                    except requests.RequestException:
                        continue
                    x, y, z = s["telemetry"]["position"]
                    sample[u] = (x, y, z)
                    w.writerow([f"{time.time():.3f}", s.get("sim_time"), u, s.get("phase"), f"{x:.4f}", f"{y:.4f}", f"{z:.4f}"])
                up = [u for u, p in sample.items() if p[2] > AIRBORNE_Z]
                for i in range(len(up)):
                    for j in range(i + 1, len(up)):
                        dd = math.dist(sample[up[i]], sample[up[j]])
                        if dd < least["d"]:
                            least.update(d=dd, pair=(up[i], up[j]))
                f.flush()
                time.sleep(0.2)

    lt = threading.Thread(target=logger); lt.start()
    wall0, sim0 = time.time(), any_d.sim_time()
    threads = [threading.Thread(target=run, args=(u,)) for u in plan]
    for t in threads: t.start()
    for t in threads: t.join()
    wall1, sim1 = time.time(), any_d.sim_time()
    stop.set(); lt.join()

    ok = all(r == "OK" for r in results.values())
    print("\nResult:", "MISSION COMPLETED" if ok else "MISSION FAILED")
    for u, r in results.items():
        print(f"  {u}: {r}")
    for p in world["parcels"]:
        if parcel_at[p["id"]] != p["at"]:
            print(f"  parcel {p['id']} ({p['label']}): {p['at']} -> {parcel_at[p['id']]}")
    print(f"  duration: {sim1 - sim0:.1f} s simulated, {wall1 - wall0:.1f} s real")
    if least["pair"]:
        print(f"  smallest distance between two flying drones: {least['d']:.2f} m {least['pair']}")
    ev_path = f"{log_dir}/events_{stamp}.csv"
    with open(ev_path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["sim_time", "drone", "task", "event"]); w.writerows(sorted(events))
    print(f"  logs: {csv_path}, {ev_path}")
    plot(csv_path, f"{log_dir}/flight_{stamp}.png", world)
    return ok


def plot(csv_path, png_path, world):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (no plot: sudo apt install -y python3-matplotlib)")
        return
    tracks = {}
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            tracks.setdefault(r["drone"], []).append((float(r["sim_time"]), float(r["x"]), float(r["y"]), float(r["z"])))
    if not tracks:
        return
    t0 = min(t[0][0] for t in tracks.values())
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4.5))
    for p in world["places"]:
        a.plot(p["x"], p["y"], "s", color="0.6", markersize=9)
        a.annotate(p["name"], (p["x"], p["y"]), textcoords="offset points", xytext=(6, 6), fontsize=8, color="0.3")
    for name, t in tracks.items():
        line, = a.plot([p[1] for p in t], [p[2] for p in t], label=name)
        a.plot(t[0][1], t[0][2], "o", color=line.get_color())
        b.plot([p[0] - t0 for p in t], [p[3] for p in t], label=name)
    a.set_title("Top view (squares: places, dots: start)"); a.set_xlabel("x [m]"); a.set_ylabel("y [m]"); a.axis("equal"); a.grid(True); a.legend()
    b.set_title("Altitude over time"); b.set_xlabel("simulation time [s]"); b.set_ylabel("z [m]"); b.grid(True); b.legend()
    fig.tight_layout(); fig.savefig(png_path, dpi=150)
    print(f"  plot: {png_path}")


def main():
    ap = argparse.ArgumentParser(description="Fly one Mission JSON in the simulator.")
    ap.add_argument("mission")
    ap.add_argument("--world", default=str(HERE / "world.json"))
    ap.add_argument("--dry-run", action="store_true", help="check and compile only, do not fly")
    args = ap.parse_args()
    mission = json.loads(Path(args.mission).read_text())
    world = json.loads(Path(args.world).read_text())
    print(f"Mission {mission.get('mission_id')}: {len(mission.get('tasks', []))} tasks")
    if not mission.get("tasks"):
        print(f"Nothing to fly. The planner declined: {mission.get('reasoning', '(no reason given)')}")
        return 0
    problems = preflight(mission, world)
    if problems:
        print("NOT FLOWN - the mission cannot be executed:")
        for p in problems:
            print(f"  - {p}")
        return 1
    plan, layer = compile_mission(mission, world)
    show_plan(plan, layer)
    if args.dry_run:
        return 0
    return 0 if fly(plan, world, "logs") else 1


if __name__ == "__main__":
    sys.exit(main())
