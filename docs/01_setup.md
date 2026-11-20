# 01 · Set-up: simulator and local LLM (Task 1)

> For a complete step-by-step installation see [SETUP.md](../SETUP.md). This page documents the
> simulator's interfaces and what we learned about it.

## 1 Simulator

The simulator is the Crazyflie digital twin in the chair's repository
[FEDT-Bigrid-UAS](https://github.com/ShaneCan/FEDT-Bigrid-UAS) (tested at commit `0b01ccd`,
"update interfaces for new crazyswarm2", 10 Nov 2026). It runs in Docker: Ubuntu 22.04, ROS 2 Humble,
Webots R2025a, eight simulated Crazyflie 2.1 drones (`cf231` … `cf238`).

Tested host: Windows 11, WSL2 with Ubuntu 24.04, Docker Engine with Compose v2, WSLg for the window.

```bash
git clone https://github.com/ShaneCan/FEDT-Bigrid-UAS.git
cd FEDT-Bigrid-UAS/cps.asset.crazyflie
bash controller/cf.PyControl/bin/start_dssim_docker.sh webots --action build   # first time only, takes a while
bash controller/cf.PyControl/bin/start_dssim_docker.sh webots                  # start; wait for READY
# without a window:  ... start_dssim_docker.sh webots --headless
```

Ctrl+C stops and removes the container.

### Interfaces used by this project

| Interface | Address |
|---|---|
| HTTP control service per drone | `http://127.0.0.1:5000` (cf231) … `:5007` (cf238) |
| Status | `GET /status` → `state`, `phase`, `position`, `battery`, `sim_time` |
| Commands | `/activate_idle`, `/begin_takeoff` (height, duration), `/navigate` (x, y, z, duration), `/begin_landing` |
| Task completion | `GET /tasks/<task_id>` (HTTP 200 only means "accepted") |
| State diagrams | `http://127.0.0.1:8080/webview.html` … `:8087` |

### Facts learned in Task 1

- **Use the phase, not the state, to detect landing:** after landing, `state` stays `landing`; `phase` becomes `landed`.
- **Use simulation time:** the simulation can run slower than real time, so all waiting in our scripts uses
  `sim_time` from `/status`, and each script checks that the clock is advancing (a frozen clock means: restart the simulator).
- **Battery is a dummy value** (constant 4.2 V); battery levels for planning come from `pipeline/world.json`.
- **Motion is simplified** Webots motion, not the real flight firmware.
- The floor is 15 m × 6 m centred at (0.5, 0); the drones start in a ring at ±0.5 m / ±1.0 m.

### First flight

```bash
python3 task1_simulation/my_mission.py
```

Flies cf231–cf233 on separate altitude layers (0.5 / 0.8 / 1.1 m), logs positions to CSV and saves a plot
(see `figures/task1_three_drones.png`).

## 2 Local language model

```bash
sudo apt install -y zstd                      # needed by the Ollama installer
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &                                # "address already in use" = it is already running (fine)
ollama pull qwen3:4b
```

Tested: Ollama 0.40.16, `qwen3:4b`, NVIDIA GTX 1650 Ti (4 GB). With a context window of 4096 tokens the model
runs about 82 % on the GPU at roughly 10 tokens per second.

## 3 Python packages on the host

```bash
pip install -r requirements.txt
# or with apt: sudo apt install -y python3-requests python3-jsonschema python3-matplotlib
```

## Known pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Drones never reach "landed" | waiting for `state` | wait for `phase == "landed"` |
| Script waits forever, positions frozen | simulation clock stopped | restart the simulator |
| `apt` 404 errors | outdated package lists | `sudo apt update` first |
| Ollama installer fails | `zstd` missing | `sudo apt install zstd` |
| CSV column mismatch in `logs/llm_runs.csv` | log written by an older script version | move the old log away before a new run |
