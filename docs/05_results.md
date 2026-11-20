# 05 · First results (15 November 2026)

Set-up: 8 test requests (`pipeline/commands.txt`), `qwen3:4b` via Ollama on a GTX 1650 Ti (4 GB), temperature 0,
seed 42, output forced to the JSON Schema, thinking off. Scored with `pipeline/score.py`.

| Run | Prompt | Validators | Correct missions | Unsafe missions accepted | Mean time per request |
|---|---|---|---|---|---|
| 1 | v1 | no | 3 / 8 | 3 | 66 s (54 s without the first, model-loading request) |
| 2 | v2 | no | 4 / 8 | 2 | 37 s |
| 3 | v2 | yes, up to 3 correction rounds | 6 / 8 | 0 | 93 s |

"Unsafe" = a mission that would have been executed although it breaks a rule (e.g. a drone below the battery
limit, an invented place).

## Observations

- A better prompt helps, but does not remove unsafe missions; the validators do (0 unsafe in run 3).
- The correction loop costs time: about 2.5× the mean time of run 2, because each round is another LLM call.
- Feedback wording matters: listing valid places led to place substitution (see `04_validators.md`).
- An LLM-generated, validated mission was flown in the simulator (cf233, then cf231 after the first drop-off):
  `figures/task4_llm_mission_flight.png`.

## Limits

- Eight requests are far too few for statistical statements; Task 7 will use a larger, labelled test set.
- One model, one GPU, one world; no speech input yet (Task 6).
- Requests 3–6 with the second version of the feedback messages (`commands_3to6.txt`, tag `v2-c3-fb2`) still have to be rerun.
