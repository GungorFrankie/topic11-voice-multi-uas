# 02 · Mission language (Task 2)

The mission language is a small domain-specific language in JSON, defined by
[`pipeline/mission_schema.json`](../pipeline/mission_schema.json) (JSON Schema, draft 2020-12, version 0.1).
It is the contract between the LLM, the validators and the compiler.

## Structure

```json
{
  "mission_id": "m1",
  "reasoning": "optional short explanation; required when refusing",
  "tasks": [
    {"id": "t1", "uas": "cf233", "action": "takeoff"},
    {"id": "t2", "uas": "cf233", "action": "goto",    "target": "depot_a"},
    {"id": "t3", "uas": "cf233", "action": "pickup",  "parcel": "p1"},
    {"id": "t4", "uas": "cf233", "action": "goto",    "target": "hospital"},
    {"id": "t5", "uas": "cf233", "action": "dropoff", "parcel": "p1"},
    {"id": "t6", "uas": "cf233", "action": "land"}
  ]
}
```

## The six actions

| Action | Required field | Optional | Meaning |
|---|---|---|---|
| `takeoff` | – | `altitude_m` (≤ 1.5) | climb to cruise height |
| `goto` | `target` (place name) | – | fly to a named place from `world.json` |
| `pickup` | `parcel` | – | take a parcel at the current place |
| `dropoff` | `parcel` | – | deliver the parcel at the current place |
| `hold` | `duration_s` (0–120) | – | hover for the given time |
| `land` | – | – | land at the current position |

## Order and refusal

- Tasks of one drone run in list order.
- `"after": ["t5"]` makes a task wait until tasks of **other** drones are finished ("then" in the request).
- An impossible request is answered with `"tasks": []` and a `reasoning` text (refusal).

## World model

[`pipeline/world.json`](../pipeline/world.json) holds the eight drones (start positions from the simulator),
four places (`depot_a`, `depot_b`, `hospital`, `lab`), three parcels (`p1` 10 g, `p2` 12 g, `p3` 30 g),
battery levels and limits (payload 15 g, minimum battery 30 %). Places, parcels and battery levels are
**test values chosen by the team**; the simulator has none of them.

## Examples

| Folder | Expectation | Count |
|---|---|---|
| `examples/valid/` | pass schema and validators | 4 |
| `examples/schema_errors/` | fail the schema (wrong form) | 9 |
| `examples/semantic_errors/` | pass the schema, fail the validators (wrong meaning) | 8 |

```bash
cd pipeline
python3 check_missions.py                 # all examples
python3 check_missions.py my_mission.json # one mission
```

## Executing a mission (simple compiler, phase A of Task 5)

[`pipeline/fly_mission.py`](../pipeline/fly_mission.py) executes a mission in the simulator:

- each drone gets its own altitude layer (0.5 m, 0.8 m, 1.1 m, …), so paths cannot meet;
- `goto` becomes one timed `navigate` call (cruise speed 0.25 m/s, at least 4 s per leg);
- `pickup` / `dropoff` hover for 3 s while the parcel is booked; `hold` hovers for `duration_s`;
- `after` makes a drone wait for the named tasks; positions and events are logged to CSV and plotted.

```bash
python3 fly_mission.py examples/valid/v2_two_deliveries_then.json
python3 fly_mission.py my_mission.json --dry-run    # compile and print only
```

Altitude layers are safe but do not scale to eight drones in a 1.5 m hall; Task 5 replaces them with 4D trajectories.
