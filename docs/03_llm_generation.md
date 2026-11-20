# 03 · Mission generation with a local LLM (Task 3)

[`pipeline/generate_mission.py`](../pipeline/generate_mission.py):

```
operator request + world model ──► prompt ──► local LLM (Ollama) ──► Mission JSON ──► checks ──► log
```

## Model and settings

| Setting | Value | Why |
|---|---|---|
| Model | `qwen3:4b` via Ollama | fits a 4 GB laptop GPU |
| Output format | forced to the JSON Schema (`format` = schema) | always parseable JSON |
| Thinking | off | shorter answers and faster responses |
| Temperature / seed | 0 / 42 | repeatable answers |
| Context window | 4096 tokens | prompt is about 1,250 tokens; keeps the model mostly on the GPU |

## Prompt (version v2)

Five parts:

1. role and task;
2. rules of the mission language;
3. current world state (drones with position, status, battery; places; parcels; limits);
4. allocation rules (only idle drones with enough battery and payload; prefer the suitable drone closest to the parcel);
5. one worked example.

Version v1 was the first prompt (15 Nov 2026); v2 is the revised prompt after the first evaluation. Compare them with `--prompt v1` / `--prompt v2`.

```bash
cd pipeline
python3 generate_mission.py "Bring the blood sample to the hospital."
python3 generate_mission.py "..." --show-prompt          # print the prompt only
python3 generate_mission.py "..." --fly                  # also fly it if executable
python3 generate_mission.py --batch commands.txt --prompt v1 --tag v1
python3 generate_mission.py --batch commands.txt --tag v2
```

Main options: `--model`, `--prompt v1|v2`, `--tag`, `--ctx`, `--think on|off`, `--no-format`,
`--correct N` (Task 4), `--feedback all|first`, `--batch FILE`, `--fly`.

## Test set

[`pipeline/commands.txt`](../pipeline/commands.txt) holds eight requests with the expected outcome after `||`,
from one delivery to requests that must be refused (unknown place, parcel too heavy). Example:

```
Bring the blood sample to the hospital, then the medicine to the lab. || p1>hospital then p2>lab
Deliver the heavy box to the hospital. || refuse
```

[`pipeline/score.py`](../pipeline/score.py) compares the latest mission per request with the expectation, without
flying, and reports an allocation ratio (flight distance of the mission ÷ shortest possible distance; 1.00 = optimal).

## Logging

Every call appends one row to `logs/llm_runs.csv` (time, tag, model, prompt version, request, wall time, load time,
answer tokens, tokens per second, correction rounds used, errors per round, outcome, executable, number of tasks,
problems, file); prompt and answer are stored in `logs/llm/`. Logs are not committed (see `.gitignore`).
