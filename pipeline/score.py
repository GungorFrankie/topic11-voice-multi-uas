#!/usr/bin/env python3
"""
score.py - is each generated mission CORRECT for its request? (Task 3 now, Task 7 later)

For every request in commands.txt it takes the most recent mission from logs/llm_runs.csv
and compares it with the expected outcome written after "||". It checks, without flying:
  - refusal expected  -> the task list must be empty
  - otherwise         -> every parcel ends at its destination, using only drones that are idle,
                         charged (battery >= limit) and strong enough, flying only to known places,
                         picking up only where the parcel is, one parcel at a time;
                         "then" needs a waiting relation, "&" must not have one; +holdN needs the hold.
  - allocation ratio  -> flight distance of the mission / shortest possible distance (1.00 = optimal)

Run:  python3 score.py               (latest mission per request)
      python3 score.py --tag v2      (only missions made with --tag v2)
"""
import argparse
import csv
import itertools
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_expected(path):
    out = []
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.startswith("#") and "||" in line:
            req, exp = (s.strip() for s in line.split("||", 1))
            out.append((req, exp))
    return out


def parse_expected(exp):
    if exp == "refuse":
        return {"refuse": True}
    hold = re.search(r"\+hold(\d+)", exp)
    body = re.sub(r"\+hold\d+", "", exp).strip()
    rel = "then" if " then " in body else "&" if " & " in body else None
    parts = re.split(r"\s+then\s+|\s*&\s*", body) if rel else [body]
    deliveries = [tuple(p.strip().split(">")) for p in parts]
    return {"refuse": False, "deliveries": deliveries, "relation": rel, "hold": int(hold.group(1)) if hold else None}


def precedes(mission, a, b):
    """True if task a must finish before task b starts (same-drone order + 'after')."""
    tasks = mission["tasks"]
    before = {t["id"]: set(t.get("after", [])) for t in tasks}
    last = {}
    for t in tasks:
        if t["uas"] in last:
            before[t["id"]].add(last[t["uas"]])
        last[t["uas"]] = t["id"]
    seen, stack = set(), [b]
    while stack:
        x = stack.pop()
        for p in before.get(x, ()):
            if p == a:
                return True
            if p not in seen:
                seen.add(p); stack.append(p)
    return False


def score_any(mission, exp_text, world):
    """The expected outcome may list alternatives separated by ' or '; the mission is correct if it matches one."""
    results = [score(mission, parse_expected(alt.strip()), world) for alt in exp_text.split(" or ")]
    good = [r for r in results if r[0]]
    return good[0] if good else results[0]


def score(mission, exp, world):
    tasks = mission.get("tasks", [])
    by_system = str(mission.get("reasoning", "")).startswith("Refused by the system")
    if exp["refuse"]:
        if not tasks:
            return True, "correct refusal" + (" (by the SYSTEM, after correction failed)" if by_system else " (by the model)"), None
        return False, "should have refused, but planned a mission", None
    if not tasks:
        return False, f"refused, but the request was possible ({str(mission.get('reasoning', ''))[:100]})", None
    # structure of each drone's task list: takeoff first, land last, nothing in between on the ground
    per = {}
    for t in tasks:
        per.setdefault(t.get("uas"), []).append(t.get("action"))
    structure = []
    for u, acts in per.items():
        if acts[0] != "takeoff":
            structure.append(f"{u} does not start with takeoff")
        if acts[-1] != "land":
            structure.append(f"{u} does not end with land")
        if acts.count("takeoff") != 1 or acts.count("land") != 1:
            structure.append(f"{u} has {acts.count('takeoff')} takeoff(s) and {acts.count('land')} landing(s)")
    if structure:
        return False, "; ".join(structure), None
    uas = {u["id"]: u for u in world["uas"]}
    place = {p["name"]: (p["x"], p["y"]) for p in world["places"]}
    parcel = {p["id"]: dict(p) for p in world["parcels"]}
    limit = world["limits"]["min_battery_pct"]
    problems, pos, carry, dist, dropoff_task, first_task, held = [], {}, {}, 0.0, {}, {}, {}
    for t in tasks:
        u, a = t.get("uas"), t.get("action")
        if u not in uas:
            problems.append(f"{t['id']}: drone {u} does not exist"); continue
        if u not in pos:
            d = uas[u]
            pos[u] = (d["position"]["x"], d["position"]["y"]); first_task[u] = t["id"]
            if d["status"] != "idle":
                problems.append(f"{u} is not idle")
            if d["battery_pct"] < limit:
                problems.append(f"{u} battery {d['battery_pct']}% < {limit}%")
        if a == "goto":
            if t["target"] not in place:
                problems.append(f"{t['id']}: unknown place {t['target']}"); continue
            dist += math.dist(pos[u], place[t["target"]]); pos[u] = place[t["target"]]
        elif a == "pickup":
            p = parcel.get(t["parcel"])
            if not p:
                problems.append(f"{t['id']}: unknown parcel"); continue
            if place.get(p["at"]) != pos[u]:
                problems.append(f"{t['id']}: {u} is not at {p['at']} where {t['parcel']} is")
            if p["mass_g"] > uas[u]["payload_limit_g"]:
                problems.append(f"{t['parcel']} ({p['mass_g']} g) too heavy for {u}")
            if carry.get(u):
                problems.append(f"{u} already carries {carry[u]}")
            carry[u] = t["parcel"]; p["at"] = u
        elif a == "dropoff":
            if carry.get(u) != t["parcel"]:
                problems.append(f"{t['id']}: {u} does not carry {t['parcel']}")
            here = next((n for n, xy in place.items() if xy == pos[u]), None)
            if t["parcel"] in parcel:
                parcel[t["parcel"]]["at"] = here
            carry[u] = None; dropoff_task[t["parcel"]] = t["id"]
        elif a == "hold":
            held[u] = (pos[u], t.get("duration_s"))
    for pid, dest in exp["deliveries"]:
        if parcel.get(pid, {}).get("at") != dest:
            problems.append(f"{pid} ends at {parcel.get(pid, {}).get('at')}, expected {dest}")
    if exp["relation"] and len(exp["deliveries"]) == 2 and not problems:
        (p_a, _), (p_b, _) = exp["deliveries"]
        drone_b = next(t["uas"] for t in tasks if t.get("parcel") == p_b)
        waits = precedes(mission, dropoff_task[p_a], first_task[drone_b]) if drone_b in first_task else False
        if exp["relation"] == "then" and not waits:
            problems.append(f"order: {p_b} does not wait for the drop-off of {p_a} (missing 'after')")
        if exp["relation"] == "&" and waits:
            problems.append(f"order: {p_b} waits for {p_a}, but the operator asked for 'at the same time'")
    if exp["hold"] and not any(h[1] == exp["hold"] for h in held.values()):
        problems.append(f"no hold of {exp['hold']} s")
    ratio = None
    if not problems:
        best = best_distance(exp["deliveries"], world)
        ratio = round(dist / best, 2) if best else None
    return (not problems), ("; ".join(problems) if problems else "correct"), ratio


def best_distance(deliveries, world):
    """Shortest total flight distance: try every assignment of suitable drones to the parcels."""
    place = {p["name"]: (p["x"], p["y"]) for p in world["places"]}
    parcel = {p["id"]: p for p in world["parcels"]}
    ok = [u for u in world["uas"] if u["status"] == "idle" and u["battery_pct"] >= world["limits"]["min_battery_pct"]]
    best = None
    for combo in itertools.permutations(ok, len(deliveries)):
        total = 0.0
        for u, (pid, dest) in zip(combo, deliveries):
            if parcel[pid]["mass_g"] > u["payload_limit_g"]:
                total = None; break
            start = (u["position"]["x"], u["position"]["y"])
            total += math.dist(start, place[parcel[pid]["at"]]) + math.dist(place[parcel[pid]["at"]], place[dest])
        if total is not None and (best is None or total < best):
            best = total
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commands", default=str(HERE / "commands.txt"))
    ap.add_argument("--tag", help="only missions generated with this --tag")
    args = ap.parse_args()
    world = json.loads((HERE / "world.json").read_text())
    rows = list(csv.DictReader(open(HERE / "logs" / "llm_runs.csv")))
    if args.tag:
        rows = [r for r in rows if r.get("tag") == args.tag]
    latest = {}
    for r in rows:
        latest[r["request"]] = r
    n = ok = 0
    ratios = []
    print(f"{'#':>2}  {'correct':7}  {'alloc':5}  request / reason")
    for i, (req, exp) in enumerate(load_expected(args.commands), 1):
        r = latest.get(req)
        if not r or not r.get("file"):
            print(f"{i:>2}  {'-':7}  {'':5}  {req[:60]}  (no mission found)"); continue
        mission = json.loads((HERE / r["file"]).read_text())
        good, why, ratio = score_any(mission, exp, world)
        n += 1; ok += good
        if ratio:
            ratios.append(ratio)
        print(f"{i:>2}  {'YES' if good else 'no':7}  {str(ratio or ''):5}  {req[:60]}\n{'':17}{why}")
    if n:
        print(f"\nIntent-correct: {ok}/{n} ({100 * ok / n:.0f}%)"
              + (f"; allocation ratio of correct missions: mean {sum(ratios) / len(ratios):.2f} (1.00 = shortest possible)" if ratios else ""))


if __name__ == "__main__":
    main()
