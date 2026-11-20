#!/usr/bin/env python3
"""
run_pipeline.py - the whole chain in one command (mid-term demo):

    request (typed)  ->  local LLM (Task 3)  ->  validators + correction loop (Task 4)
                     ->  4D compiler with own separation check (Task 5)  ->  flight in Webots (Task 1)

Every stage can stop the chain (fail closed): an invalid mission is refused by the validators,
an unsafe plan is refused by the compiler, a drone in the wrong place is refused by the flight script.
Stage times are logged to logs/pipeline_runs.csv (end-to-end latency, RQ4).

Run (Ollama running; for --fly the simulator must be READY):
    python3 run_pipeline.py "Bring the blood sample to the hospital and the medicine to the lab at the same time."
    python3 run_pipeline.py "..." --fly
Options: --correct 3 (correction rounds), --sep 0.4 (m), --planner PATH or 'straight', --compiler DIR
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import generate_mission as gm                      # noqa: E402  (Tasks 3 + 4)


def find_compiler(arg):
    for c in ([Path(arg).expanduser()] if arg else []) + [HERE.parent / "task5_compiler", Path.home() / "task5_compiler"]:
        if (c / "planner4d.py").exists():
            return c
    sys.exit("planner4d.py not found: give --compiler DIR")


def main():
    ap = argparse.ArgumentParser(description="Request -> LLM -> validators -> 4D compiler -> flight.")
    ap.add_argument("request")
    ap.add_argument("--world", default=str(HERE / "world.json"))
    ap.add_argument("--model", default=gm.DEFAULT_MODEL)
    ap.add_argument("--correct", type=int, default=3, help="max. correction rounds (Task 4)")
    ap.add_argument("--sep", type=float, default=0.4, help="planned minimum distance between drones [m]")
    ap.add_argument("--planner", default=str(Path.home() / "4DPlanning" / "preflight_plan"),
                    help="path to preflight_plan, or 'straight'")
    ap.add_argument("--compiler", help="folder with planner4d.py and fly_4d.py")
    ap.add_argument("--fly", action="store_true", help="fly in Webots if every stage accepts")
    args = ap.parse_args()

    comp = find_compiler(args.compiler)
    sys.path.insert(0, str(comp))
    import planner4d                                 # noqa: E402  (Task 5)
    world = json.loads(Path(args.world).read_text())
    schema = json.loads((HERE / "mission_schema.json").read_text())
    try:
        requests.get(f"{gm.OLLAMA}/api/version", timeout=3)
    except requests.RequestException:
        sys.exit("Ollama is not running. Start it with:  ollama serve   (in another terminal)")
    row = {"time": datetime.now().strftime("%Y%m%d_%H%M%S"), "request": args.request, "sep_m": args.sep}
    t_all = time.time()

    # ---- stage 1+2: LLM writes the mission, validators check it, errors go back (fail closed)
    print(f"\n[1-2] LLM + validators (max. {args.correct} correction rounds)")
    llm_args = argparse.Namespace(prompt="v2", model=args.model, no_format=False, think=False, temperature=0.0,
                                  seed=42, ctx=4096, correct=args.correct, feedback="all", tag="pipeline")
    t = time.time()
    result, mission = gm.run_one(args.request, llm_args, world, schema)
    gm.log_row(result)
    row.update(llm_s=round(time.time() - t, 2), rounds=result["rounds_used"], outcome=result["outcome"],
               mission_file=result["file"])
    gm.show(mission)
    print(f"  -> {result['outcome'].upper()} after {row['llm_s']} s ({result['rounds_used']} correction rounds)")
    if not mission.get("tasks"):
        row["result"] = "refused by validators" if "system" in result["outcome"] else "refused by model"
        return finish(row, t_all, 1, mission.get("reasoning", ""))

    # ---- stage 3: 4D compiler with own separation check (fail closed)
    print(f"\n[3] 4D compiler (4DPlanning: {args.planner}, required distance {args.sep} m)")
    t = time.time()
    out = HERE / "plan4d_out" / row["time"]
    out.mkdir(parents=True, exist_ok=True)
    try:
        plan = planner4d.compile4d(mission, world, args.planner, args.sep, workdir=out / "work")
    except Exception as e:
        plan = {"status": "REFUSED", "reason": f"compiler error: {e}", "history": []}
    (out / "trajectories.json").write_text(json.dumps(plan, indent=2))
    row["compiler_s"] = round(time.time() - t, 2)
    for h in plan.get("history", []):
        print(f"  iteration {h['iteration']}: {h['conflict_steps']} conflict steps, closest {h['min_distance_m']} m")
    if plan["status"] != "OK":
        row["result"] = "refused by compiler"
        return finish(row, t_all, 1, plan.get("reason", ""))
    row.update(repairs=plan["repairs"], planned_min_m=plan["min_distance_m"],
               mission_end_s=max(w[-1]["t"] for w in plan["trajectories"].values()))
    print(f"  -> OK: closest {plan['min_distance_m']} m, {plan['repairs']} repairs, "
          f"mission ends at {row['mission_end_s']} s (simulation time)")
    row["latency_to_flight_s"] = round(time.time() - t_all, 2)
    print(f"\nEnd-to-end latency (request -> flyable plan): {row['latency_to_flight_s']} s  "
          f"[LLM+validators {row['llm_s']} s, compiler {row['compiler_s']} s]")

    # ---- stage 4: flight in Webots
    if not args.fly:
        row["result"] = "planned (not flown)"
        print(f"\nNot flown (add --fly). Plan: {out / 'trajectories.json'}")
        return finish(row, t_all, 0)
    print("\n[4] Flight in Webots")
    r = subprocess.run([sys.executable, str(comp / "fly_4d.py"), str(out / "trajectories.json"),
                        "--logs", str(HERE / "logs")])
    row["result"] = "flown" if r.returncode == 0 else "flight failed"
    return finish(row, t_all, r.returncode)


def finish(row, t_all, code, why=""):
    row["total_s"] = round(time.time() - t_all, 2)
    if why:
        print(f"\nSTOPPED - {row['result']} (fail closed): {why[:300]}")
    path = HERE / "logs" / "pipeline_runs.csv"
    path.parent.mkdir(exist_ok=True)
    fields = ["time", "request", "outcome", "rounds", "llm_s", "compiler_s", "repairs", "sep_m", "planned_min_m",
              "mission_end_s", "latency_to_flight_s", "total_s", "result", "mission_file"]
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)
    print(f"Result: {row['result'].upper()}  ·  logged to {path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
