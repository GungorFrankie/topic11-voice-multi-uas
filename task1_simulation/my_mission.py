#!/usr/bin/env python3
"""
my_mission.py - our own multi-drone test mission for Task 1.

What it does
  1. Asks every drone in MISSION for its status (health check).
  2. Flies each drone on its own altitude layer: take off, climb to the layer,
     fly out to one point, fly back above the start, land.
  3. Logs position and state of all mission drones to a CSV file while they fly.
  4. Prints a summary (duration, smallest distance between two flying drones)
     and saves a trajectory plot if matplotlib is installed.

Run (simulator must show READY first):
    python3 my_mission.py

All waiting uses the SIMULATION clock from /status, so the script also works
when the simulator runs slower than real time.
"""
import csv
import math
import os
import threading
import time
from datetime import datetime

import requests

HOST = "http://127.0.0.1"
FIRST_PORT = 5000          # cf231 -> 5000, cf232 -> 5001, ...
FIRST_ID = 231

# ---- the mission: drone -> altitude layer [m] and the point it flies out to [x, y] ----
MISSION = {
    "cf231": {"layer": 0.5, "out": (0.0, 0.5)},
    "cf232": {"layer": 0.8, "out": (-0.5, 0.0)},
    "cf233": {"layer": 1.1, "out": (-1.0, -0.2)},
}

ARRIVE_TOL = 0.10          # [m] distance to target that counts as "arrived"
SIM_TIMEOUT = 90.0         # [simulated s] max time for one step
HOLD_AT_POINT = 2.0        # [simulated s] hover at each waypoint
LOG_PERIOD = 0.2           # [real s] between log samples
AIRBORNE_Z = 0.25          # [m] above this a drone counts as flying
STALL_LIMIT = 15.0         # [real s] without any progress of the simulation clock -> stop

print_lock = threading.Lock()


def say(name, text):
    with print_lock:
        print(f"[{name}] {text}", flush=True)


class Drone:
    def __init__(self, name):
        self.name = name
        self.url = f"{HOST}:{FIRST_PORT + int(name[2:]) - FIRST_ID}"

    def status(self):
        return requests.get(f"{self.url}/status", timeout=5).json()

    def state(self):
        return self.status().get("state")

    def position(self):
        return self.status()["telemetry"]["position"]

    def sim_time(self):
        return float(self.status().get("sim_time", time.time()))

    def post(self, path, body=None):
        r = requests.post(f"{self.url}/{path}", json=body, timeout=10)
        if r.status_code != 200:
            raise RuntimeError(f"{self.name}: POST /{path} -> {r.status_code} {r.text[:200]}")
        return r.json() if r.text else {}

    def wait(self, condition, what):
        """Poll /status until condition(status) is true; timeout in simulated seconds."""
        start = self.sim_time()
        last_sim, last_change = start, time.time()
        while True:
            s = self.status()
            if s.get("error") or s.get("state") == "error":
                raise RuntimeError(f"{self.name}: drone is in error while waiting for {what}: "
                                   f"error={s.get('error')!r} phase={s.get('phase')!r} transition={s.get('transition')!r}")
            if condition(s):
                return s
            now_sim = float(s.get("sim_time", time.time()))
            if now_sim != last_sim:
                last_sim, last_change = now_sim, time.time()
            elif time.time() - last_change > STALL_LIMIT:
                raise RuntimeError(f"{self.name}: simulation clock stopped at {now_sim} s - is Webots paused or closed?")
            if now_sim - start > SIM_TIMEOUT:
                raise TimeoutError(f"{self.name}: timed out waiting for {what} (state={s.get('state')})")
            time.sleep(0.1)

    def hold(self, sim_seconds):
        self.wait(lambda s, t0=self.sim_time(): float(s.get("sim_time", 0)) - t0 >= sim_seconds, "hold time")

    def goto(self, x, y, z):
        say(self.name, f"navigate to ({x:.2f}, {y:.2f}, {z:.2f})")
        # same two forms as the supervisor's script: JSON body if any value is negative, else URL
        if x < 0 or y < 0 or z < 0:
            self.post("navigate", {"x": x, "y": y, "z": z})
        else:
            self.post(f"navigate/{x}/{y}/{z}")

        def arrived(s):
            px, py, pz = s["telemetry"]["position"]
            return math.dist((px, py, pz), (x, y, z)) < ARRIVE_TOL and s.get("state") == "hovering"

        self.wait(arrived, f"arrival at ({x}, {y}, {z})")
        self.hold(HOLD_AT_POINT)

    def fly(self, layer, out):
        x0, y0, _ = self.position()
        st = self.status()
        say(self.name, f"start position ({x0:.2f}, {y0:.2f}), state '{st.get('state')}', phase '{st.get('phase')}'")

        if self.state() != "idle":
            self.post("activate_idle")
            self.wait(lambda s: s.get("state") == "idle", "state idle")
        say(self.name, "idle -> takeoff")
        self.post("begin_takeoff")
        self.wait(lambda s: s.get("state") == "hovering", "hovering after takeoff")

        self.goto(x0, y0, layer)            # climb to own altitude layer
        self.goto(out[0], out[1], layer)    # fly out
        self.goto(x0, y0, layer)            # fly back above the start

        say(self.name, "landing")
        self.post("begin_landing")
        # after touchdown the service keeps state 'landing' and sets phase 'landed' (seen on 15 Nov 2026)
        self.wait(lambda s: s.get("phase") == "landed"
                  or (s["telemetry"]["position"][2] < AIRBORNE_Z and s.get("state") != "landing"), "touchdown")
        st = self.status()
        say(self.name, f"on the ground (state '{st.get('state')}', phase '{st.get('phase')}')")


def logger(drones, path, stop, summary):
    """Sample all mission drones, write CSV, track the smallest distance between flying drones."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["wall_time", "sim_time", "drone", "state", "x", "y", "z"])
        while not stop.is_set():
            sample = {}
            for d in drones:
                try:
                    s = d.status()
                except requests.RequestException:
                    continue
                x, y, z = s["telemetry"]["position"]
                sample[d.name] = (x, y, z)
                w.writerow([f"{time.time():.3f}", s.get("sim_time"), d.name, s.get("state"),
                            f"{x:.4f}", f"{y:.4f}", f"{z:.4f}"])
            names = [n for n, p in sample.items() if p[2] > AIRBORNE_Z]
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    dist = math.dist(sample[names[i]], sample[names[j]])
                    if dist < summary["min_dist"]:
                        summary["min_dist"] = dist
                        summary["min_pair"] = (names[i], names[j])
            f.flush()
            time.sleep(LOG_PERIOD)


def plot(csv_path, png_path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed - no plot. Install with: sudo apt install -y python3-matplotlib")
        return
    tracks = {}
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            tracks.setdefault(r["drone"], []).append((float(r["sim_time"]), float(r["x"]), float(r["y"]), float(r["z"])))
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4.5))
    for name, t in tracks.items():
        t0 = t[0][0]
        a.plot([p[1] for p in t], [p[2] for p in t], label=name)
        a.plot(t[0][1], t[0][2], "o", color=a.lines[-1].get_color())
        b.plot([p[0] - t0 for p in t], [p[3] for p in t], label=name)
    a.set_title("Top view"); a.set_xlabel("x [m]"); a.set_ylabel("y [m]"); a.axis("equal"); a.grid(True); a.legend()
    b.set_title("Altitude over time"); b.set_xlabel("simulation time [s]"); b.set_ylabel("z [m]"); b.grid(True); b.legend()
    fig.tight_layout(); fig.savefig(png_path, dpi=150)
    print(f"plot saved: {png_path}")


def main():
    drones = [Drone(n) for n in MISSION]
    print("Health check")
    for d in drones:
        s = d.status()
        print(f"  {d.name}: connected={s.get('connected')} state={s.get('state')} "
              f"battery={s['telemetry'].get('battery'):.2f} V position={[round(v, 2) for v in s['telemetry']['position']]}")
        if not s.get("connected") or s.get("error") or s.get("state") == "error":
            raise SystemExit(f"{d.name} is not ready (error={s.get('error')!r}, state={s.get('state')}). "
                             f"Restart the simulator, then run this script again.")
    t1 = drones[0].sim_time(); time.sleep(2.0); t2 = drones[0].sim_time()
    if t2 <= t1:
        raise SystemExit(f"The simulation clock is not advancing (stuck at {t1} s). "
                         f"Webots is paused, frozen or closed. Press the play button in Webots or restart the simulator.")
    print(f"  simulation clock is running: {t2 - t1:.2f} simulated s in 2 real s (real-time factor {(t2 - t1) / 2:.2f})")

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path, png_path = f"logs/mission_{stamp}.csv", f"logs/mission_{stamp}.png"
    stop, summary = threading.Event(), {"min_dist": float("inf"), "min_pair": None}
    log_thread = threading.Thread(target=logger, args=(drones, csv_path, stop, summary))
    log_thread.start()

    results = {}

    def run(d):
        try:
            d.fly(MISSION[d.name]["layer"], MISSION[d.name]["out"])
            results[d.name] = "OK"
        except Exception as e:                     # report, keep the other drones going
            results[d.name] = f"FAILED: {e}"
            say(d.name, results[d.name])
            try:                                   # fail safe: never leave a drone hovering
                if d.position()[2] > AIRBORNE_Z:
                    say(d.name, "trying to land after the failure")
                    d.post("begin_landing")
            except Exception as e2:
                say(d.name, f"could not land: {e2}")

    wall0, sim0 = time.time(), drones[0].sim_time()
    threads = [threading.Thread(target=run, args=(d,)) for d in drones]
    for t in threads: t.start()
    for t in threads: t.join()
    wall1, sim1 = time.time(), drones[0].sim_time()
    stop.set(); log_thread.join()

    print("\nSummary")
    for name, r in results.items():
        print(f"  {name}: {r}")
    print(f"  duration: {sim1 - sim0:.1f} s simulated, {wall1 - wall0:.1f} s real "
          f"(real-time factor {(sim1 - sim0) / max(wall1 - wall0, 1e-9):.2f})")
    if summary["min_pair"]:
        print(f"  smallest distance between two flying drones: {summary['min_dist']:.2f} m {summary['min_pair']}")
    print(f"  log saved: {csv_path}")
    plot(csv_path, png_path)


if __name__ == "__main__":
    main()
