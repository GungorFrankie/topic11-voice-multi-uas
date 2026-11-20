#!/usr/bin/env python3
"""
validators.py - Task 4: check a Mission JSON against the language AND the world, and explain every error.

Two layers (handbook B4.6):
  schema layer   (S rules)  the FORM of each task              - from mission_schema.json
  semantic layer (A, L, P, Q rules)  the MEANING of the mission  - against world.json

Each error is one plain sentence that names the task, the rule and, where obvious, the fix.
These sentences are what the LLM gets back in the correction loop.

Run on a file:   python3 validators.py examples/semantic_errors/s2_low_battery.json
Run all examples: python3 validators.py --examples
"""
import difflib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def validate(mission, world):
    """Return a list of errors: dicts with rule, task, message. Empty list = valid."""
    from check_missions import schema_errors
    errs = [{"rule": "S1/S2", "task": "", "message": e} for e in schema_errors(mission)]
    if errs:
        return errs                     # meaning cannot be checked if the form is broken
    tasks = mission["tasks"]
    if not tasks:
        return []                       # a refusal is always valid
    uas = {u["id"]: u for u in world["uas"]}
    place = {p["name"]: (p["x"], p["y"]) for p in world["places"]}
    parcels = {p["id"]: p for p in world["parcels"]}
    limit = world["limits"]["min_battery_pct"]
    usable = sorted(u for u, d in uas.items() if d["status"] == "idle" and d["battery_pct"] >= limit)

    def err(rule, tid, msg):
        errs.append({"rule": rule, "task": tid, "message": (f"Task {tid}: " if tid else "") + msg})

    # S3 unique ids, references exist
    ids = [t["id"] for t in tasks]
    for i in sorted({i for i in ids if ids.count(i) > 1}):
        err("S3", i, f"the id {i} is used more than once. Give every task its own id.")
    for t in tasks:
        for a in t.get("after", []):
            if a not in ids:
                err("S3", t["id"], f"waits for {a}, which does not exist.")

    seen_uas = []
    for t in tasks:
        u = t["uas"]
        if u not in seen_uas:
            seen_uas.append(u)
            # A1 exists, A2 available
            if u not in uas:
                err("A1", t["id"], f"UAS {u} does not exist. Available: {', '.join(usable)}.")
            elif uas[u]["status"] != "idle":
                err("A2", t["id"], f"UAS {u} is {uas[u]['status']}, not idle. Use another UAS, e.g. {usable[0] if usable else 'none'}.")
            elif uas[u]["battery_pct"] < limit:
                err("A2", t["id"], f"UAS {u} battery {uas[u]['battery_pct']}% is below the {limit}% limit. "
                                   f"Use another UAS: {', '.join(usable)}; if none fits, refuse with tasks [].")
        # L1 places and parcels exist
        if t["action"] == "goto" and t["target"] not in place:
            # Version 2 (15 Nov 2026): do NOT offer other places as substitutes - in the first run the model
            # "fixed" 'warehouse' by flying to the lab instead. Suggest a name only if it looks like a typo.
            close = difflib.get_close_matches(t["target"], list(place), n=1, cutoff=0.8)
            if close:
                err("L1", t["id"], f"unknown place '{t['target']}'. Did you mean '{close[0]}'? "
                                   f"Use it only if that is the place the operator asked for.")
            else:
                err("L1", t["id"], f"there is no place called '{t['target']}'. Do not replace it with another place. "
                                   f"If the operator asked for '{t['target']}', the request cannot be done: "
                                   f"answer with \"tasks\": [] and explain why in \"reasoning\".")
        if t["action"] in ("pickup", "dropoff") and t["parcel"] not in parcels:
            err("L1", t["id"], f"unknown parcel '{t['parcel']}'. Known parcels: {', '.join(parcels)}.")

    # Q1, Q2, Q3, P1: follow each drone's tasks in order
    per = {}
    for t in tasks:
        per.setdefault(t["uas"], []).append(t)
    picked_by = {}
    for u, ts in per.items():
        if ts[0]["action"] != "takeoff":
            err("Q1", ts[0]["id"], f"UAS {u} must start with takeoff, but starts with {ts[0]['action']}.")
        if ts[-1]["action"] != "land":
            err("Q1", ts[-1]["id"], f"UAS {u} must end with land, but ends with {ts[-1]['action']}.")
        for extra in ("takeoff", "land"):
            n = sum(1 for t in ts if t["action"] == extra)
            if n > 1:
                err("Q1", "", f"UAS {u} has {n} {extra} tasks; it needs exactly one.")
        here, carrying = None, None
        if u in uas:
            here_xy = (uas[u]["position"]["x"], uas[u]["position"]["y"])
            here = next((n for n, xy in place.items() if xy == here_xy), None)
        for t in ts:
            a = t["action"]
            if a == "goto" and t["target"] in place:
                here = t["target"]
            elif a == "pickup" and t["parcel"] in parcels:
                p = parcels[t["parcel"]]
                if here != p["at"]:
                    err("Q2", t["id"], f"{u} picks up {p['id']} at {here or 'its start position'}, but {p['id']} is at "
                                       f"{p['at']}. Add a goto {p['at']} before this pickup.")
                if u in uas and p["mass_g"] > uas[u]["payload_limit_g"]:
                    err("P1", t["id"], f"parcel {p['id']} ({p['mass_g']} g) exceeds the payload limit of {u} "
                                       f"({uas[u]['payload_limit_g']} g). No UAS can carry more than "
                                       f"{max(d['payload_limit_g'] for d in uas.values())} g: if no UAS can carry it, refuse with tasks [].")
                if carrying:
                    err("Q3", t["id"], f"{u} already carries {carrying}; a UAS carries one parcel at a time.")
                if p["id"] in picked_by and picked_by[p["id"]] != u:
                    err("Q3", t["id"], f"parcel {p['id']} is picked up by both {picked_by[p['id']]} and {u}.")
                picked_by[p["id"]] = u
                carrying = p["id"]
            elif a == "dropoff":
                if carrying != t["parcel"]:
                    err("Q2", t["id"], f"{u} drops off {t['parcel']} but does not carry it.")
                carrying = None
        if carrying:
            err("Q2", ts[-1]["id"], f"{u} lands while still carrying {carrying}. Add a dropoff.")

    # Q4 no cycle in the waiting relations
    need = {t["id"]: set(t.get("after", [])) for t in tasks}
    for ts in per.values():
        for a, b in zip(ts, ts[1:]):
            need[b["id"]].add(a["id"])
    done = set()
    while True:
        ready = [i for i, n in need.items() if i not in done and n <= done]
        if not ready:
            break
        done.update(ready)
    if len(done) < len(need) and len(set(ids)) == len(ids):
        stuck = sorted(set(need) - done, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)
        err("Q4", "", f"tasks {', '.join(stuck)} wait for each other in a circle and can never start. Check the 'after' fields.")
    return errs


def feedback_text(errors):
    lines = ["Your mission is not valid. Errors found by the checker:"]
    lines += [f"- [{e['rule']}] {e['message']}" for e in errors]
    lines.append("Return the complete corrected mission as one JSON object. Change only what is needed to fix these errors. "
                 "If the request cannot be done with the available UAS, places and parcels, return \"tasks\": [] "
                 "and explain why in \"reasoning\".")
    return "\n".join(lines)


def main():
    world = json.loads((HERE / "world.json").read_text())
    if len(sys.argv) > 1 and sys.argv[1] != "--examples":
        errs = validate(json.loads(Path(sys.argv[1]).read_text()), world)
        print("VALID" if not errs else feedback_text(errs))
        return
    wrong = 0
    for group, should_be_valid in [("valid", True), ("schema_errors", False), ("semantic_errors", False)]:
        print(f"\n{group}  (expected: {'valid' if should_be_valid else 'errors'})")
        for f in sorted((HERE / "examples" / group).glob("*.json")):
            m = json.loads(f.read_text())
            errs = validate(m, world)
            ok = (not errs) == should_be_valid
            wrong += not ok
            first = errs[0] if errs else None
            print(f"  [{'ok' if ok else 'WRONG'}] {f.name:36s} " + (f"[{first['rule']}] {first['message'][:95]}" if first else "valid")
                  + (f"   (+{len(errs) - 1} more)" if len(errs) > 1 else ""))
    print("\nAll examples behave as expected." if not wrong else f"\n{wrong} example(s) did NOT behave as expected.")


if __name__ == "__main__":
    main()
