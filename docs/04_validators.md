# 04 · Validators and correction loop (Task 4)

[`pipeline/validators.py`](../pipeline/validators.py) checks a mission in two layers and explains every error in
one plain sentence. The validators are ordinary, deterministic Python code, not a second language model: the
same mission always gives the same result.

## Rules

| Rule | Layer | Checks | Example message |
|---|---|---|---|
| S1/S2 | schema | form of the mission and of each task (from `mission_schema.json`) | missing or unknown fields |
| S3 | schema | task ids unique; `after` refers to existing tasks | "the id t2 is used more than once" |
| A1 | allocation | drone exists | "UAS cf239 does not exist. Available: …" |
| A2 | allocation | drone idle and battery ≥ limit | "UAS cf232 battery 22% is below the 30% limit" |
| L1 | locations | place and parcel exist | "there is no place called 'warehouse'. Do not replace it with another place." |
| P1 | payload | parcel mass ≤ drone payload limit | "parcel p3 (30 g) exceeds the payload limit of cf231 (15 g)" |
| Q1 | sequence | each drone starts with takeoff and ends with land | "must start with takeoff, but starts with goto" |
| Q2 | sequence | pickup at the parcel's place, dropoff only what is carried, nothing carried at landing | "picks up p1 at its start position, but p1 is at depot_a" |
| Q3 | sequence | one parcel at a time; a parcel picked up by only one drone | "already carries p1" |
| Q4 | sequence | no circular waiting through `after` | "tasks … wait for each other in a circle" |

```bash
cd pipeline
python3 validators.py examples/semantic_errors/s2_low_battery.json
python3 validators.py --examples
```

## Correction loop

```
LLM ──► mission ──► validators ──► valid? ──yes──► execute
 ▲                                   │ no
 └──── error messages (all, or only the first) ◄── rounds left? ──no──► refuse (fail closed)
```

```bash
python3 generate_mission.py --batch commands.txt --correct 3 --tag v2-c3             # all errors fed back
python3 generate_mission.py --batch commands.txt --correct 3 --feedback first --tag v2-c3-fb1   # first error only (as ISR-LLM)
python3 score.py --tag v2-c3
```

If the mission is still invalid after the last round, the system saves a refusal (`tasks: []`,
reasoning "Refused by the system …"), so nothing unsafe is flown.

## Lesson from the first runs

A feedback message that listed the valid places made the model **substitute** a known place for an unknown one
(e.g. "warehouse" → "depot_a") instead of refusing. Message L1 v2 therefore suggests a correction only for a likely
typo (string similarity) and otherwise says explicitly: *do not replace it with another place*.
