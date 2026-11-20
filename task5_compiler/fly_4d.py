#!/usr/bin/env python3
"""
fly_4d.py - fly the verified 4D trajectories of planner4d.py in the Webots simulator.

    trajectories.json (timed waypoints per drone)
        -> one common start time t0 on the SIMULATION clock
        -> per drone: takeoff at its first waypoint, then one timed 'navigate' per segment,
           hover where the plan waits, land at its last waypoint
        -> log: real positions, smallest real distance between flying drones,
           and the tracking error (real position vs. planned position)

Run (simulator must show READY):
    python3 fly_4d.py plan4d_out/trajectories.json
    python3 fly_4d.py plan4d_out/trajectories.json --dry-run     # print the command schedule only
"""
import argparse
import bisect
import csv
import json
import math
import os
import sys
import threading
import time
from datetime import datetime

import requests

HOST, FIRST_PORT, FIRST_ID = "http://127.0.0.1", 5000, 231
LEAD_S = 3.0                 # [simulated s] time between "go" and t0, so every drone is ready
AIRBORNE_Z = 0.25
print_lock = threading.Lock()


def say(who, text):
    with print_lock:
        print(f"[{who}] {text}", flush=True)


class Drone:
    def __init__(self, name):
        self.name = name
        self.url = f"{HOST}:{FIRST_PORT + int(name[2:]) - FIRST_ID}"

    def status(self):
        return requests.get(f"{self.url}/status", timeout=5).json()

    def sim_time(self):
        return float(self.status()["sim_time"])

    def post(self, path, body=None):
        r = requests.post(f"{self.url}/{path}", json=body or {}, timeout=10)
        if r.status_code != 200:
            raise RuntimeError(f"{self.name}: POST /{path} refused ({r.status_code}): {r.text[:200]}")
        return r.json()

    def finish_task(self, task_id, abort, grace=10.0):
        """Wait until the previous motion task is completed; returns the extra waiting time."""
        if not task_id:
            return 0.0
        start = self.sim_time()
        while True:
            if abort.is_set():
                raise RuntimeError("mission aborted")
            t = requests.get(f"{self.url}/tasks/{task_id}", timeout=5).json()
            if t.get("status") in ("failed", "cancelled"):
                raise RuntimeError(f"{self.name}: motion task {t.get('status')}: {t.get('error')}")
            if t.get("status") == "completed":
                return self.sim_time() - start
            if self.sim_time() - start > grace:
                raise TimeoutError(f"{self.name}: previous motion not finished after {grace} s")
            time.sleep(0.03)

    def wait_until(self, t_sim, abort):
        """Block until the simulation clock reaches t_sim (works at any real-time factor)."""
        last, last_change = None, time.time()
        while True:
            if abort.is_set():
                raise RuntimeError("mission aborted")
            s = self.status()
            if s.get("error") or s.get("state") == "error":
                raise RuntimeError(f"{self.name}: drone error {s.get('error')!r}")
            now = float(s["sim_time"])
            if now >= t_sim:
                return now
            if now != last:
                last, last_change = now, time.time()
            elif time.time() - last_change > 15:
                raise RuntimeError("simulation clock stopped - is Webots paused?")
            time.sleep(0.05)


def segments(wps):
    """Commands per drone: (time, command, args). A segment whose end differs from its start
    is one navigate; equal points mean hovering (no command needed)."""
    cmds = [(wps[0]["t"], "takeoff", {"height": wps[0]["z"], "duration": max(1.0, wps[1]["t"] - wps[0]["t"])})]
    for a, b in zip(wps[1:], wps[2:]):
        if (a["x"], a["y"], a["z"]) != (b["x"], b["y"], b["z"]):
            cmds.append((a["t"], "navigate", {"x": b["x"], "y": b["y"], "z": b["z"],
                                              "duration": round(b["t"] - a["t"], 2)}))
    cmds.append((wps[-1]["t"], "land", {}))
    return cmds


def planned_position(wps, t):
    """Linear interpolation of the plan at time t (for the tracking error)."""
    ts = [w["t"] for w in wps]
    if t <= ts[0] or t >= ts[-1]:
        return None
    i = bisect.bisect_right(ts, t)
    a, b = wps[i - 1], wps[i]
    f = (t - a["t"]) / (b["t"] - a["t"]) if b["t"] > a["t"] else 0
    return tuple(a[k] + f * (b[k] - a[k]) for k in "xyz")


def main():
    ap = argparse.ArgumentParser(description="Fly verified 4D trajectories in Webots.")
    ap.add_argument("trajectories")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--logs", default="logs")
    args = ap.parse_args()
    plan = json.load(open(args.trajectories))
    if plan.get("status") != "OK":
        print("NOT FLOWN: the compiler refused this mission (fail closed).")
        return 1
    traj = plan["trajectories"]
    cmds = {u: segments(w) for u, w in traj.items()}
    print(f"Plan: {len(traj)} drones, planned minimum distance {plan['min_distance_m']} m "
          f"(required {plan['min_sep_m']} m)")
    for u, cs in cmds.items():
        print(f"  {u}: " + ", ".join(f"{c[1]}@{c[0]:.1f}s" for c in cs[:3]) + f", ... land@{cs[-1][0]:.1f}s")
    if args.dry_run:
        return 0

    drones = {u: Drone(u) for u in traj}
    for u, d in drones.items():
        s = d.status()
        if not s.get("connected") or s.get("error"):
            raise SystemExit(f"{u} is not ready. Restart the simulator.")
        x, y, _ = s["telemetry"]["position"]
        w0 = traj[u][0]
        if math.dist((x, y), (w0["x"], w0["y"])) > 0.2:
            raise SystemExit(f"NOT FLOWN: {u} is at ({x:.2f}, {y:.2f}), the plan starts at "
                             f"({w0['x']}, {w0['y']}). Restart the simulator so the drones are at their start positions.")
        if s.get("phase") not in ("idle", "landed"):
            d.post("activate_idle")
    any_d = next(iter(drones.values()))
    t0 = any_d.sim_time() + LEAD_S
    print(f"\nCommon start t0 = {t0:.1f} s simulation time")
    abort, results = threading.Event(), {}

    def run(u):
        d = drones[u]
        task, lag = None, 0.0
        try:
            for t, cmd, body in cmds[u]:
                d.wait_until(t0 + t, abort)
                lag = max(lag, d.finish_task(task, abort))
                path = {"takeoff": "begin_takeoff", "navigate": "navigate", "land": "begin_landing"}[cmd]
                task = d.post(path, body).get("task_id")
                say(u, f"t={t:5.1f}s {cmd} {body if cmd == 'navigate' else ''}")
            d.finish_task(task, abort, grace=30.0)
            results[u] = f"OK (largest delay behind plan {lag:.2f} s)"
        except Exception as e:
            results[u] = f"FAILED: {e}"
            say(u, results[u])
            abort.set()
            try:
                d.post("begin_landing")
            except Exception:
                pass

    os.makedirs(args.logs, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = f"{args.logs}/flight4d_{stamp}.csv"
    stop = threading.Event()
    stats = {"min_d": math.inf, "pair": None, "at": None, "max_err": 0.0, "err_sum": 0.0, "n": 0}

    def logger():
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["sim_time", "t_plan", "drone", "x", "y", "z", "px", "py", "pz", "error_m"])
            while not stop.is_set():
                sample = {}
                for u, d in drones.items():
                    try:
                        s = d.status()
                    except requests.RequestException:
                        continue
                    x, y, z = s["telemetry"]["position"]
                    tp = float(s["sim_time"]) - t0
                    p = planned_position(traj[u], tp)
                    err = math.dist((x, y, z), p) if p else ""
                    if p:
                        stats["max_err"] = max(stats["max_err"], err)
                        stats["err_sum"] += err; stats["n"] += 1
                    sample[u] = (x, y, z)
                    planned = [f"{v:.3f}" for v in p] if p else ["", "", ""]
                    w.writerow([s["sim_time"], f"{tp:.2f}", u, f"{x:.3f}", f"{y:.3f}", f"{z:.3f}",
                                *planned, f"{err:.3f}" if p else ""])
                up = [u for u, q in sample.items() if q[2] > AIRBORNE_Z]
                for i in range(len(up)):
                    for j in range(i + 1, len(up)):
                        dd = math.dist(sample[up[i]], sample[up[j]])
                        if dd < stats["min_d"]:
                            stats.update(min_d=dd, pair=(up[i], up[j]), at=tp)
                f.flush()
                time.sleep(0.1)

    lt = threading.Thread(target=logger); lt.start()
    wall0 = time.time()
    threads = [threading.Thread(target=run, args=(u,)) for u in traj]
    for t in threads: t.start()
    for t in threads: t.join()
    end_t = max(w[-1]["t"] for w in traj.values())
    any_d.wait_until(t0 + end_t + 4.0, threading.Event())     # let the landings finish
    stop.set(); lt.join()

    ok = all(r.startswith("OK") for r in results.values())
    print("\nResult:", "MISSION FLOWN AS PLANNED" if ok else "MISSION FAILED")
    for u, r in results.items():
        print(f"  {u}: {r}")
    if stats["pair"]:
        print(f"  smallest REAL distance between flying drones: {stats['min_d']:.2f} m "
              f"{stats['pair']} at t={stats['at']:.1f} s (planned minimum {plan['min_distance_m']} m)")
    if stats["n"]:
        print(f"  tracking error real vs. plan: mean {stats['err_sum'] / stats['n']:.2f} m, "
              f"max {stats['max_err']:.2f} m")
    print(f"  real time used: {time.time() - wall0:.1f} s  ·  log: {csv_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
