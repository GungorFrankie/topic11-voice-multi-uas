# Voice-driven Dynamic Multi-UAS Mission Planning with Large Language Models

Research Task in Air Transport and Logistics · Topic 11 · TU Dresden, Chair of Air Transport and Logistics  
Repository: [github.com/GungorFrankie/topic11-voice-multi-uas](https://github.com/GungorFrankie/topic11-voice-multi-uas)
Team: Hüseyin Güngör, İlke Kök, Kshitij Virendra Mahajani · Supervisor: Tianxiong Zhang, M.Sc. · Winter term 2026/27

A framework that turns a spoken delivery request into checked, collision-free flights of several small
drones (unmanned aircraft systems, UAS). A **local** large language model (LLM) writes the mission;
**deterministic validators** check it and send errors back for correction; a **planner** turns it into
flight paths; the drones fly in a **Webots** simulation.

```
 spoken request ──► speech-to-text ──► local LLM ──► Mission JSON ──► validators ──► compiler ──► Webots
   (Task 6)                           (Task 3)       (Task 2)        (Task 4)       (Task 5)    (Task 1)
                                         ▲                               │
                                         └──── error feedback, max. n rounds, else refusal
                                                       Task 7: evaluation of the whole chain
```

## Research question

> Can a local LLM turn spoken requests into safe, executable missions for several UAS, and how much do
> external validators with automatic correction improve this?

| | Sub-question | Metric (topic sheet) |
|---|---|---|
| RQ1 | How often is the generated mission correct? | Mission generation accuracy |
| RQ2 | How much do validator feedback and correction raise success? | Execution success rate |
| RQ3 | How good is the choice of drone and task order compared with a baseline? | Task allocation quality |
| RQ4 | How do success and latency change with the number of UAS? | End-to-end latency |

## Status (20 November 2026)

| Task | Content | Status | Where |
|---|---|---|---|
| 1 | Simulation set-up, first multi-drone flight | Done | [`task1_simulation/`](task1_simulation/), [docs/01_setup.md](docs/01_setup.md) |
| 2 | Mission language (JSON + JSON Schema), example missions, executor | Done (v0.1) | [`pipeline/`](pipeline/), [docs/02_mission_language.md](docs/02_mission_language.md) |
| 3 | Local LLM (Qwen via Ollama) generates missions | Done, first evaluation | [docs/03_llm_generation.md](docs/03_llm_generation.md) |
| 4 | Validators and correction loop | Done, first evaluation | [docs/04_validators.md](docs/04_validators.md) |
| 5 | Mission compiler with 4D trajectories (4DPlanning), own separation check, flight in Webots | First version, flown 20 Nov | [`task5_compiler/`](task5_compiler/) |
| 6 | Speech-to-text | Planned (January 2027) | – |
| 7 | Evaluation across tasks and UAS counts | Planned (January 2027) | [docs/05_results.md](docs/05_results.md) (first numbers) |

### First results

**Tasks 3–4** (8-request test set, qwen3:4b, laptop GPU): correct missions rose from **3/8** (first prompt) to
**4/8** (improved prompt) to **6/8** with validators and up to three correction rounds; **no unsafe mission**
passed the validators. Details and limits: [docs/05_results.md](docs/05_results.md).

**Task 5** (20 Nov 2026, Webots, 4DPlanning):

| Run | Planned closest distance | Real closest distance | Behind schedule | Tracking error (mean / max) |
|---|---|---|---|---|
| Two drones crossing at the same time (0.4 m required) | 0.60 m | **0.71 m** | 0.00 s | 0.09 m / 0.41 m |
| Whole chain: request → LLM → validators → compiler → flight | 1.00 m | **1.00 m** | 0.00 s | 0.11 m / 0.42 m |

End-to-end latency (request → flyable plan): **112–143 s**, of which the 4D compiler takes **< 0.3 s**; the LLM
dominates (GTX 1650 Ti, 4 GB; the model ran 82 % on the GPU while Webots used the CPU).

**Two findings about 4DPlanning** (minimal examples in [task5_compiler/README.md](task5_compiler/README.md)):
1. The planner can report "0 colliding pairs" while its path file still puts two drones in the same cell at the
   same time. The compiler therefore checks separation itself and repairs conflicts by delaying a leg.
2. Paths are written in absolute time (an agent with start time t₀ is padded with copies of its start cell).
   Read as relative paths, later legs wait twice; corrected in the compiler.

## Repository structure

```
topic11-voice-multi-uas/
├── README.md                  this file
├── SETUP.md                   step-by-step installation guide
├── requirements.txt           Python packages for the host
├── task1_simulation/          Task 1: first multi-drone flight script
├── pipeline/                  Tasks 2–4: mission language, LLM generation, validators, executor, scoring
│   ├── mission_schema.json    the mission language (JSON Schema, draft 2020-12)
│   ├── world.json             world model: drones, places, parcels, limits (test values)
│   ├── examples/              valid missions, form errors, meaning errors
│   ├── check_missions.py      checks the examples against the schema
│   ├── validators.py          schema + semantic rules with plain-language error messages
│   ├── generate_mission.py    request → prompt → local LLM → mission (+ correction loop, logging)
│   ├── fly_mission.py         executes a mission in the simulator (simple compiler, altitude layers)
│   ├── score.py               scores generated missions against the expected outcome
│   ├── run_pipeline.py        the whole chain in one command (request → flight), with stage times
│   └── commands*.txt          test requests with expected outcomes
├── task5_compiler/            Task 5: 4D compiler (4DPlanning + own separation check), flight script, path checker
├── scripts/                   check_setup.sh (what is missing?), install_4dplanning.sh (builds the planner)
├── tests/mocks/               stand-ins for the simulator and Ollama, for testing without hardware
├── docs/                      documentation per task, results, references
├── figures/                   plots and screenshots from own runs
└── presentations/             slides (initial presentation 17 Nov 2026)
```

## Getting started

**New here? Follow [SETUP.md](SETUP.md)**: a step-by-step guide from a fresh Windows or Ubuntu computer to the
first flight, with a check after every step. `bash scripts/check_setup.sh` tells you at any time what is still missing.

## Quick start (once everything is installed)

Requirements: Windows 11 with WSL2 (Ubuntu 24.04) or Linux, Docker Engine with Compose v2, Python 3.10+,
an NVIDIA GPU for the LLM (4 GB is enough for qwen3:4b), and for Task 5 a local build of
[4DPlanning](https://github.com/amathsow/4DPlanning) (see [task5_compiler/README.md](task5_compiler/README.md)). Full instructions: [docs/01_setup.md](docs/01_setup.md).

```bash
# 1  Python packages
pip install -r requirements.txt        # or: sudo apt install python3-requests python3-jsonschema python3-matplotlib

# 2  Offline checks (no simulator, no LLM needed)
cd pipeline
python3 check_missions.py              # schema: all examples behave as expected
python3 validators.py --examples       # validators: all examples behave as expected

# 3  Fly a hand-written mission (simulator running and READY)
python3 fly_mission.py examples/valid/v2_two_deliveries_then.json

# 4  Let the local LLM write a mission, with validators and up to 3 correction rounds (Ollama running)
python3 generate_mission.py "Bring the blood sample to the hospital." --correct 3 --fly

# 5  Whole chain: request -> LLM -> validators -> 4D compiler -> flight (Ollama + simulator running)
python3 run_pipeline.py "Bring the blood sample to the lab and the medicine to the hospital at the same time." --fly

# 6  Run the test set and score it
python3 generate_mission.py --batch commands.txt --correct 3 --tag v2-c3
python3 score.py --tag v2-c3
```

## Design principles

1. **The LLM interprets, code decides.** The model only translates the request; deterministic code checks the
   mission and computes the paths.
2. **Fail closed.** A mission that is still invalid after the last correction round is refused, never flown.
3. **Local and reproducible.** The model runs on the team's own computer (no cloud costs, no internet
   connection, data stay on site); temperature 0 and a fixed seed make runs repeatable.
4. **Measure everything.** Every LLM call is logged (prompt, answer, tokens, time, errors per round).

## Documentation

| File | Content |
|---|---|
| [docs/01_setup.md](docs/01_setup.md) | Installing and starting the simulator and Ollama; known pitfalls |
| [docs/02_mission_language.md](docs/02_mission_language.md) | The six actions, ordering, refusal, examples |
| [docs/03_llm_generation.md](docs/03_llm_generation.md) | Prompt design, model settings, logging |
| [docs/04_validators.md](docs/04_validators.md) | Rules, error messages, correction loop |
| [docs/05_results.md](docs/05_results.md) | First results from 15 Nov 2026 and their limits |
| [task5_compiler/README.md](task5_compiler/README.md) | 4D compiler: design, 4DPlanning build notes, findings, first flights |
| [docs/references.md](docs/references.md) | Literature |

## Acknowledgements

The simulator is the Crazyflie digital twin of the FEDT-Bigrid-UAS repository provided by the chair
(commit `0b01ccd`). The 4D planner is [4DPlanning](https://github.com/amathsow/4DPlanning) by Sow et al. (2026).
Neither is included here; see the set-up notes and [docs/references.md](docs/references.md).

## License

To be agreed with the chair before the repository is made public.
