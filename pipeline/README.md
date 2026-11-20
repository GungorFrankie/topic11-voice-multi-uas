# Pipeline · Tasks 2–4

| File | Purpose | Docs |
|---|---|---|
| `mission_schema.json` | Mission language (JSON Schema, draft 2020-12) | [02](../docs/02_mission_language.md) |
| `world.json` | Drones, places, parcels, limits (test values) | [02](../docs/02_mission_language.md) |
| `examples/` | valid missions, form errors, meaning errors | [02](../docs/02_mission_language.md) |
| `check_missions.py` | checks all examples against the schema | [02](../docs/02_mission_language.md) |
| `fly_mission.py` | executes a mission in the simulator (altitude layers) | [02](../docs/02_mission_language.md) |
| `generate_mission.py` | request → local LLM → mission; correction loop; logging | [03](../docs/03_llm_generation.md), [04](../docs/04_validators.md) |
| `validators.py` | schema and semantic rules with plain-language errors | [04](../docs/04_validators.md) |
| `score.py` | scores generated missions against expected outcomes | [03](../docs/03_llm_generation.md) |
| `commands.txt`, `commands_3to6.txt` | test requests with expected outcomes | [03](../docs/03_llm_generation.md) |

Run all commands from this folder. Outputs go to `logs/` and to `flight_*.csv` / `events_*.csv` / `*.png`
(not committed).
