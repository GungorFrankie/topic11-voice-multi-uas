# Set-up guide: from a fresh computer to the first flight

This guide installs everything needed to run the project, step by step. Every step ends with a **check**, so
you know it worked before you go on. At any point you can run the automatic check:

```bash
bash scripts/check_setup.sh
```

It lists every part as `OK`, `MISSING` (with the command that fixes it) or `NOT NOW` (installed but not started).

| | |
|---|---|
| **Tested on** | Windows 11, WSL2 with Ubuntu 24.04, NVIDIA GTX 1650 Ti (4 GB) |
| **Also works on** | plain Ubuntu 22.04 / 24.04 (skip step 1) |
| **Time** | about 1–2 hours, mostly downloads (simulator image, language model) |
| **Disk space** | about 25 GB free |
| **You need** | an NVIDIA graphics card for the language model; everything else runs on the CPU |

> **Only want to look at the code?** Steps 7 and 8 alone are enough: the offline self-test and the test
> stand-ins in `tests/mocks/` run without simulator, GPU or language model.

---

## Step 1 · Ubuntu on Windows (WSL2)

Open **PowerShell as administrator** and run:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart Windows when asked, open **Ubuntu** from the Start menu and choose a user name and password.
All further commands are typed into this Ubuntu window.

**Check:** `lsb_release -d` shows `Ubuntu 24.04`.

## Step 2 · Graphics driver

Install the current **NVIDIA driver on Windows** (from nvidia.com or the GeForce app). Do **not** install a
driver inside Ubuntu: WSL uses the Windows driver. Then restart WSL in PowerShell: `wsl --shutdown`.

**Check:** `nvidia-smi` in Ubuntu shows your graphics card and its memory.

## Step 3 · Docker (runs the simulator)

```bash
sudo apt update
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Close the Ubuntu window and open it again (so the group change takes effect).

**Check:** `docker run --rm hello-world` prints "Hello from Docker!".

## Step 4 · Simulator (Webots + ROS 2, eight Crazyflie drones)

The simulator is provided by the chair in a separate repository.

```bash
cd ~
git clone https://github.com/ShaneCan/FEDT-Bigrid-UAS.git
cd FEDT-Bigrid-UAS/cps.asset.crazyflie
bash controller/cf.PyControl/bin/start_dssim_docker.sh webots --action build   # first time only, 20-40 min
bash controller/cf.PyControl/bin/start_dssim_docker.sh webots                  # start
```

A Webots window opens and the terminal prints `READY ... drones=[231, ..., 238]`.
Leave this terminal open while you work; **Ctrl+C** stops the simulator.

**Check** (in a second Ubuntu window):

```bash
curl -s http://127.0.0.1:5000/status | head -c 120; echo
```

If this prints drone data with `"connected":true`, the simulator is ready, **even if the start script printed
"Startup failed"** (the script sometimes gives up waiting although the simulator is running).

## Step 5 · Local language model (Ollama + Qwen3 4B)

```bash
sudo apt install -y zstd
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:4b            # about 2.5 GB
```

Ollama usually starts by itself as a background service. If not, run `ollama serve` in its own terminal.

**Check:** `ollama run qwen3:4b "Say hello in one word."` answers within a minute or two (type `/bye` to leave).

## Step 6 · 4D path planner (4DPlanning, Task 5)

One script installs the libraries, downloads the planner and builds it (it also removes two leftover files from
the authors' computer that break the build on other machines):

```bash
cd ~
git clone https://github.com/GungorFrankie/topic11-voice-multi-uas.git     # this repository (needed in step 7 too)
bash ~/topic11-voice-multi-uas/scripts/install_4dplanning.sh               # builds into ~/4DPlanning
```

**Check:** the script ends with `OK: /home/<you>/4DPlanning/preflight_plan works`.

## Step 7 · This repository and its Python packages

```bash
cd ~/topic11-voice-multi-uas          # cloned in step 6
sudo apt install -y python3-requests python3-jsonschema python3-matplotlib
```

**Check:**

```bash
bash scripts/check_setup.sh
```

All lines should be `OK`. `NOT NOW` for Ollama or the simulator only means it is not started at the moment.

## Step 8 · First runs, from simple to complete

Run these from the `pipeline/` folder: `cd ~/topic11-voice-multi-uas/pipeline`

| # | What | Command | Needs |
|---|---|---|---|
| a | Mission examples against the schema | `python3 check_missions.py` | nothing |
| b | Validators on all examples | `python3 validators.py --examples` | nothing |
| c | 4D plan for two crossing drones | `python3 ../task5_compiler/planner4d.py ../task5_compiler/cross_test_mission.json --planner ~/4DPlanning/preflight_plan --plot` | step 6 |
| d | Fly a hand-written mission | `python3 fly_mission.py examples/valid/v2_two_deliveries_then.json` | simulator |
| e | LLM writes a mission (with correction loop) | `python3 generate_mission.py "Bring the blood sample to the hospital." --correct 3` | Ollama |
| f | **Whole chain**: request → LLM → validators → 4D compiler → flight | `python3 run_pipeline.py "Bring the blood sample to the lab and the medicine to the hospital at the same time." --fly` | everything |

Before every flight (d, f): **restart the simulator**, so all drones are back at their start positions.
The flight scripts refuse to fly otherwise.

### Without simulator and GPU: the test stand-ins

`tests/mocks/` contains small servers that imitate the simulator and Ollama, so the scripts can be tried on
any computer:

```bash
cd ~/topic11-voice-multi-uas
python3 tests/mocks/mock_ollama.py &          # imitates Ollama
python3 tests/mocks/mock_sim_fly.py 120 &     # imitates the eight drones (4x speed) for 120 s
cd pipeline
python3 run_pipeline.py "Bring the blood sample to the lab and the medicine to the hospital at the same time." --planner straight --fly
```

Stop the stand-ins afterwards (`pkill -f tests/mocks/`), and never run them while the real simulator or Ollama
is running: they use the same ports.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Startup failed: Simulator exited during startup` | (a) false alarm, or (b) the WSL display stopped working | (a) run the `curl` check of step 4; (b) in PowerShell `wsl --shutdown`, reopen Ubuntu, start again |
| `start_dssim_docker.sh: the following arguments are required: backend` | simulator name missing | add `webots` to the command |
| Flight script: `NOT FLOWN: cf233 is at (…)` | drones still where the last flight ended | restart the simulator |
| Script waits forever, positions frozen | simulation clock stopped (Webots paused or closed) | press play in Webots or restart the simulator |
| The language model needs minutes per request | part of the model runs on the CPU (`ollama ps` shows e.g. `18%/82% CPU/GPU`), slowed further by Webots' software rendering | close other programs that use the GPU; measure LLM times with the simulator closed |
| `Ollama is not running` | service not started | `ollama serve` in its own terminal |
| Ollama installer fails | `zstd` missing | `sudo apt install -y zstd` |
| 4DPlanning: `Could not find … boost_system` / `boost_filesystem` | Boost parts missing | `sudo apt install -y libboost-all-dev` (the install script does this) |
| 4DPlanning: `/home/amath/Documents/4DPlanning does not exist` | old build files from the authors' computer | delete `CMakeCache.txt CMakeFiles Makefile cmake_install.cmake` (the install script does this) |
| 4DPlanning: `libboost_program_options.so.1.74.0: cannot open` | you ran the authors' prebuilt binary | delete `preflight_plan` and build it again (the install script does this) |
| 4DPlanning seems stuck at `getInitialSolution() started` | the large demo map (400 × 400 × 25 cells) takes very long | use our hall-sized maps (`planner4d.py` writes them for you) |
| `No module named requests` / `jsonschema` / `matplotlib` | Python package missing | `sudo apt install -y python3-requests python3-jsonschema python3-matplotlib` |
| `apt` 404 errors | outdated package lists | `sudo apt update` first |
| Docker: `permission denied … docker.sock` | your user is not in the docker group yet | `sudo usermod -aG docker $USER`, close and reopen Ubuntu |

Still stuck? Open an issue in the repository with the command you ran and the last 20 lines of its output.
