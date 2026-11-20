#!/usr/bin/env python3
"""
generate_mission.py - Task 3: a local LLM turns a sentence into a Mission JSON.

  operator sentence + world model  ->  prompt  ->  local LLM (Ollama)  ->  Mission JSON  ->  checks

The prompt has five parts (handbook B4.2):
  1 role and task   2 rules of the mission language   3 current world state
  4 allocation rules   5 one worked example

Usage (Ollama must be running, the model downloaded):
    python3 generate_mission.py "Bring the blood sample to the hospital"
    python3 generate_mission.py "..." --model qwen3.5:4b --no-format    # free text instead of schema-forced output
    python3 generate_mission.py "..." --fly                              # also fly it in the simulator
    python3 generate_mission.py --batch commands.txt                     # run a list of commands, print a table
    python3 generate_mission.py "..." --show-prompt                      # print the prompt and stop
    python3 generate_mission.py --batch commands.txt --prompt v1         # the first prompt, for comparison
    python3 generate_mission.py --batch commands.txt --correct 3 --tag v2-c3   # Task 4: validators + correction loop

Every call is logged to logs/llm_runs.csv (prompt and answer in logs/llm/).
The real validators with automatic correction are Task 4; here we only report what is wrong.
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b"


# ----------------------------------------------------------------------------- prompt
ROLE = """You plan delivery missions for a small fleet of indoor drones (UAS).
You receive the current state of the fleet and one request from an operator.
Answer with exactly ONE JSON object in the mission language below. No text before or after it."""

RULES = """MISSION LANGUAGE
A mission is {"mission_id": "...", "reasoning": "...", "tasks": [ ... ]}.
Each task is {"id": "t1", "uas": "<drone id>", "action": "<action>", ...}. Task ids are t1, t2, t3, ... and unique in the mission.
The six actions and their fields:
- takeoff   optional "altitude_m"
- goto      required "target": the NAME of a place from the list below (never coordinates)
- pickup    required "parcel": parcel id. The drone must first goto the place where the parcel is.
- dropoff   required "parcel": parcel id. The drone must first goto the destination.
- hold      required "duration_s": seconds to hover
- land      no extra field
Order: the tasks of one drone are executed in the order they are listed.
Every drone that is used starts with takeoff and ends with land.
To make a task wait until a task of ANOTHER drone is finished, add "after": ["<task id>"].
Use "after" only when the operator asks for an order between drones ("then", "after that", "once ... has arrived").
If the operator asks for things "at the same time", do not use "after".
If the request is impossible, answer {"mission_id": "...", "reasoning": "<why>", "tasks": []}.
Do not add any field that is not listed here."""

ALLOCATION = """HOW TO CHOOSE DRONES
- Use only drones whose status is "idle" and whose battery is at least {min_battery}%.
- A drone carries one parcel at a time; the parcel mass must not exceed the drone's payload limit.
- Prefer the suitable drone that is closest to the parcel.
- Give each parcel to a different drone unless the operator says otherwise.
- Use only places and parcels that appear in the state. If a place, parcel or drone the operator names does not exist,
  or no drone can carry a parcel, refuse and explain why in "reasoning".
- Write a short "reasoning": which drone you chose for which parcel and why."""


def world_state_text(world):
    lim = world["limits"]
    place_xy = {p["name"]: (p["x"], p["y"]) for p in world["places"]}

    def near(x, y):
        name = min(place_xy, key=lambda n: (place_xy[n][0] - x) ** 2 + (place_xy[n][1] - y) ** 2)
        d = ((place_xy[name][0] - x) ** 2 + (place_xy[name][1] - y) ** 2) ** 0.5
        return f"{d:.1f} m from {name}"
    lines = ["CURRENT STATE", "Drones:"]
    for u in world["uas"]:
        p = u["position"]
        lines.append(f"- {u['id']}: status {u['status']}, battery {u['battery_pct']}%, payload limit {u['payload_limit_g']} g, "
                     f"at x={p['x']}, y={p['y']} ({near(p['x'], p['y'])})")
    lines.append("Places: " + ", ".join(f"{p['name']} (x={p['x']}, y={p['y']})" for p in world["places"]))
    lines.append("Parcels:")
    for p in world["parcels"]:
        lines.append(f"- {p['id']}: {p['label']}, {p['mass_g']} g, at {p['at']}")
    lines.append(f"Battery limit: {lim['min_battery_pct']}%")
    return "\n".join(lines)


# ---- prompt version 2 (15 Nov 2026): changes after the first run, see study notes ----
ALLOCATION_V2 = ALLOCATION + """
- If the operator names a drone that is not idle, has less than {min_battery}% battery or cannot carry the parcel,
  do NOT use it: refuse and say why.
BEFORE YOU WRITE THE TASKS, CHECK: does every place exist? Does every parcel weigh at most the payload limit?
Does every drone you use have at least {min_battery}% battery? If any answer is no, "tasks" MUST be [].
Your "tasks" must follow your own "reasoning": if the reasoning says a drone cannot be used, do not use it.
Keep "reasoning" to one or two short sentences."""

EXAMPLES_V2 = [
    ("Take the blood sample to the lab.", None),        # filled with EXAMPLE_ANSWER below
    ("Bring the medicine to the lab. Once it has arrived, bring the blood sample to the hospital.",
     {"mission_id": "example2",
      "reasoning": "p2 at depot_b: cf231 (86%, nearest). p1 at depot_a: cf233 (74%, nearest); it waits for the drop-off t5.",
      "tasks": [
          {"id": "t1", "uas": "cf231", "action": "takeoff"},
          {"id": "t2", "uas": "cf231", "action": "goto", "target": "depot_b"},
          {"id": "t3", "uas": "cf231", "action": "pickup", "parcel": "p2"},
          {"id": "t4", "uas": "cf231", "action": "goto", "target": "lab"},
          {"id": "t5", "uas": "cf231", "action": "dropoff", "parcel": "p2"},
          {"id": "t6", "uas": "cf231", "action": "land"},
          {"id": "t7", "uas": "cf233", "action": "takeoff", "after": ["t5"]},
          {"id": "t8", "uas": "cf233", "action": "goto", "target": "depot_a"},
          {"id": "t9", "uas": "cf233", "action": "pickup", "parcel": "p1"},
          {"id": "t10", "uas": "cf233", "action": "goto", "target": "hospital"},
          {"id": "t11", "uas": "cf233", "action": "dropoff", "parcel": "p1"},
          {"id": "t12", "uas": "cf233", "action": "land"}]}),
    ("Send cf239 to the hospital.",
     {"mission_id": "example3", "reasoning": "There is no drone cf239 in the fleet, so the request cannot be done.", "tasks": []}),
]

EXAMPLE_REQUEST = "Take the blood sample to the lab."
EXAMPLE_ANSWER = {
    "mission_id": "example",
    "reasoning": "p1 (blood sample, 10 g) is at depot_a. cf233 is idle, has enough battery and is closest to depot_a.",
    "tasks": [
        {"id": "t1", "uas": "cf233", "action": "takeoff"},
        {"id": "t2", "uas": "cf233", "action": "goto", "target": "depot_a"},
        {"id": "t3", "uas": "cf233", "action": "pickup", "parcel": "p1"},
        {"id": "t4", "uas": "cf233", "action": "goto", "target": "lab"},
        {"id": "t5", "uas": "cf233", "action": "dropoff", "parcel": "p1"},
        {"id": "t6", "uas": "cf233", "action": "land"}]}


def build_messages(request, world, version="v2"):
    lim = world["limits"]["min_battery_pct"]
    if version == "v1":
        system = "\n\n".join([ROLE, RULES, ALLOCATION.format(min_battery=lim)])
        examples = "EXAMPLE\nRequest: " + EXAMPLE_REQUEST + "\nAnswer: " + json.dumps(EXAMPLE_ANSWER)
        examples += "\n(The example only shows the format; plan the new request from the current state.)"
    else:
        system = "\n\n".join([ROLE, RULES, ALLOCATION_V2.format(min_battery=lim)])
        parts = []
        for i, (req, ans) in enumerate(EXAMPLES_V2, 1):
            parts.append(f"EXAMPLE {i}\nRequest: {req}\nAnswer: {json.dumps(ans or EXAMPLE_ANSWER)}")
        examples = "\n\n".join(parts) + "\n(The examples only show the format; plan the new request from the current state.)"
    user = world_state_text(world) + "\n\n" + examples + "\n\nREQUEST\n" + request
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ----------------------------------------------------------------------------- LLM call
def ask_llm(messages, model, schema, use_format, think, temperature, seed, ctx):
    body = {"model": model, "messages": messages, "stream": False, "keep_alive": "30m",
            "options": {"temperature": temperature, "seed": seed, "num_ctx": ctx}}
    if os.environ.get("TOPIC11_NUM_GPU"):        # force N model layers onto the GPU (99 = all)
        body["options"]["num_gpu"] = int(os.environ["TOPIC11_NUM_GPU"])
    if use_format:
        body["format"] = schema
    if think is not None:
        body["think"] = think
    t0 = time.time()
    r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=600)
    wall = time.time() - t0
    if r.status_code != 200:
        raise RuntimeError(f"Ollama answered {r.status_code}: {r.text[:300]}")
    out = r.json()
    ns = lambda k: out.get(k, 0) / 1e9
    timing = {"wall_s": round(wall, 2), "load_s": round(ns("load_duration"), 2),
              "prompt_tokens": out.get("prompt_eval_count", 0), "answer_tokens": out.get("eval_count", 0),
              "generate_s": round(ns("eval_duration"), 2),
              "tokens_per_s": round(out.get("eval_count", 0) / max(ns("eval_duration"), 1e-9), 1)}
    return out["message"].get("content", ""), out["message"].get("thinking", ""), timing


def extract_json(text):
    """Find the JSON object in the answer (needed when the format is not forced)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start:end + 1])


# ----------------------------------------------------------------------------- one request (+ Task 4 correction loop)
def run_one(request, args, world, schema):
    from validators import validate, feedback_text
    messages = build_messages(request, world, args.prompt)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    llm_dir = HERE / "logs" / "llm"
    llm_dir.mkdir(parents=True, exist_ok=True)
    (llm_dir / f"{stamp}_prompt.txt").write_text(messages[0]["content"] + "\n\n=====\n\n" + messages[1]["content"])

    total = {"wall_s": 0.0, "load_s": 0.0, "answer_tokens": 0, "generate_s": 0.0}
    rounds_log, mission, errors, first_errors = [], None, None, None
    for rnd in range(args.correct + 1):                       # round 0 = first answer, then corrections
        answer, thinking, timing = ask_llm(messages, args.model, schema, not args.no_format, args.think,
                                           args.temperature, args.seed, args.ctx)
        for k in total:
            total[k] += timing.get(k, 0)
        (llm_dir / f"{stamp}_answer{rnd}.txt").write_text(answer + ("\n\n----- thinking -----\n" + thinking if thinking else ""))
        try:
            mission = extract_json(answer)
            mission["mission_id"] = mission.get("mission_id") or stamp
            errors = validate(mission, world)
        except (ValueError, json.JSONDecodeError) as e:
            mission, errors = None, [{"rule": "S0", "task": "", "message": f"the answer is not a JSON object ({e})."}]
        if first_errors is None:
            first_errors = errors
        rounds_log.append(",".join(sorted({e["rule"] for e in errors})) or "ok")
        if not errors:
            break
        if rnd < args.correct:                                # send the errors back and ask again
            fb = errors[:1] if args.feedback == "first" else errors
            messages = messages + [{"role": "assistant", "content": answer},
                                   {"role": "user", "content": feedback_text(fb)}]
            print(f"  round {rnd}: {len(errors)} error(s) -> feedback: " + "; ".join(e["message"][:70] for e in fb[:3]))

    refused_by_model = mission is not None and not errors and not mission.get("tasks")
    if errors:                                                # fail closed: the SYSTEM refuses
        outcome = "system refused" if args.correct else "invalid"
        mission = {"mission_id": stamp, "tasks": [],
                   "reasoning": "Refused by the system: no valid mission after "
                                f"{args.correct} correction round(s). Remaining errors: " + " | ".join(e["message"] for e in errors)}
    elif refused_by_model:
        outcome = "model refused"
    else:
        outcome = "valid first time" if len(rounds_log) == 1 else f"valid after {len(rounds_log) - 1} round(s)"
    out_dir = HERE / "missions"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{stamp}.json").write_text(json.dumps(mission, indent=2))
    total = {k: round(v, 2) for k, v in total.items()}
    result = {"time": stamp, "model": args.model, "prompt": args.prompt, "tag": args.tag or args.prompt,
              "forced_format": not args.no_format, "ctx": args.ctx, "request": request, **total,
              "tokens_per_s": round(total["answer_tokens"] / max(total["generate_s"], 1e-9), 1),
              "correct_rounds_max": args.correct, "rounds_used": len(rounds_log) - 1,
              "errors_per_round": " > ".join(rounds_log), "first_valid": not first_errors,
              "outcome": outcome, "parsed": mission is not None, "schema_ok": True,
              "executable": bool(mission.get("tasks")), "n_tasks": len(mission.get("tasks", [])),
              "problems": " | ".join(e["message"] for e in (errors or [])), "file": f"missions/{stamp}.json"}
    return result, mission


def log_row(result):
    path = HERE / "logs" / "llm_runs.csv"
    fields = ["time", "tag", "model", "prompt", "forced_format", "ctx", "request", "wall_s", "load_s", "answer_tokens",
              "generate_s", "tokens_per_s", "correct_rounds_max", "rounds_used", "errors_per_round", "first_valid", "outcome",
              "executable", "n_tasks", "problems", "file"]
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(result)


def show(mission):
    print(f"  reasoning: {mission.get('reasoning', '-')}")
    for t in mission.get("tasks", []):
        extra = {k: v for k, v in t.items() if k not in ("id", "uas", "action")}
        print(f"    {t.get('id', '?'):>4} {t.get('uas', '?'):6} {t.get('action', '?'):8} {json.dumps(extra) if extra else ''}")


def main():
    ap = argparse.ArgumentParser(description="Turn an operator sentence into a Mission JSON with a local LLM.")
    ap.add_argument("request", nargs="?")
    ap.add_argument("--batch", help="text file, one request per line (# = comment)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--world", default=str(HERE / "world.json"))
    ap.add_argument("--no-format", action="store_true", help="do not force the JSON Schema (free text)")
    ap.add_argument("--think", choices=["on", "off"], default="off", help="model thinking (default off, faster)")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--ctx", type=int, default=4096, help="context window in tokens; smaller uses less GPU memory (prompt is ~1,250)")
    ap.add_argument("--show-prompt", action="store_true")
    ap.add_argument("--prompt", choices=["v1", "v2"], default="v2", help="prompt version (v1 = first run on 15 Nov)")
    ap.add_argument("--tag", help="label for this run in the log, e.g. v2-qwen3-4b (default: the prompt version)")
    ap.add_argument("--fly", action="store_true", help="fly the mission if it is executable")
    ap.add_argument("--correct", type=int, default=0, help="Task 4: max. correction rounds with validator feedback (0 = off)")
    ap.add_argument("--feedback", choices=["all", "first"], default="all", help="send all errors, or only the first (as ISR-LLM)")
    args = ap.parse_args()
    args.think = {"on": True, "off": False}[args.think]
    world = json.loads(Path(args.world).read_text())
    schema = json.loads((HERE / "mission_schema.json").read_text())

    if args.show_prompt:
        for m in build_messages(args.request or "<request>", world, args.prompt):
            print(f"===== {m['role']} =====\n{m['content']}\n")
        return 0
    try:
        requests.get(f"{OLLAMA}/api/version", timeout=3)
    except requests.RequestException:
        print("Ollama is not running. Start it with:  ollama serve   (in another terminal)")
        return 1

    requests_list = []
    if args.batch:
        requests_list = [l.split("||")[0].strip() for l in Path(args.batch).read_text().splitlines()
                         if l.strip() and not l.startswith("#")]      # the part after || is for score.py only
    elif args.request:
        requests_list = [args.request]
    else:
        ap.error("give a request or --batch FILE")

    rows = []
    for i, req in enumerate(requests_list, 1):
        print(f"\n[{i}/{len(requests_list)}] {req}")
        try:
            result, mission = run_one(req, args, world, schema)
        except Exception as e:
            result, mission = {"request": req, "model": args.model, "problems": f"call failed: {e}"}, None
        log_row(result)
        rows.append(result)
        print(f"  {result.get('wall_s', '?')} s in total, {result.get('rounds_used', 0)} correction round(s), "
              f"{result.get('tokens_per_s', '?')} tokens/s")
        if mission:
            show(mission)
        print(f"  -> {result.get('outcome', 'failed').upper()}" + (f": {result['problems'][:200]}" if result.get("problems") else ""))
        if args.fly and result.get("executable") and len(requests_list) == 1:
            import subprocess
            subprocess.run([sys.executable, str(HERE / "fly_mission.py"), str(HERE / result["file"]), "--world", args.world])

    if len(rows) > 1:
        print("\nSummary")
        print(f"  {'#':>2}  {'time s':>6}  {'rounds':6}  {'outcome':22}  errors per round        request")
        for i, r in enumerate(rows, 1):
            print(f"  {i:>2}  {str(r.get('wall_s', '-')):>6}  {str(r.get('rounds_used', '-')):6}  {r.get('outcome', 'failed'):22}  "
                  f"{r.get('errors_per_round', ''):22}  {r['request'][:45]}")
        n = len(rows)
        print(f"  valid first time {sum(bool(r.get('first_valid')) for r in rows)}/{n}; flyable at the end "
              f"{sum(bool(r.get('executable')) for r in rows)}/{n}. Log: logs/llm_runs.csv")
        print("  Valid is not the same as correct. Score with:  python3 score.py --tag " + (args.tag or args.prompt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
