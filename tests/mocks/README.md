# Test stand-ins

Small local servers that imitate the simulator's control services and the Ollama API, so the scripts can be
tested without Webots or a GPU. They are deliberately simple and are **not** a model of the real drones.

| File | Imitates | Used to test |
|---|---|---|
| `mock_sim_task1.py` | control services of cf231–cf233 (ports 5000–5002) | `task1_simulation/my_mission.py` |
| `mock_sim_fly.py` | all eight control services incl. phases, tasks and timed navigate (4× speed) | `pipeline/fly_mission.py` |
| `mock_ollama.py` | Ollama `/api/chat` with canned answers that react to feedback | `pipeline/generate_mission.py --correct` |

Start a mock in one terminal (`python3 tests/mocks/mock_sim_fly.py`), then run the script in another.
Do not run a mock while the real simulator or Ollama uses the same ports.
