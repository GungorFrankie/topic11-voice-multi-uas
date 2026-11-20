#!/usr/bin/env bash
# Checks every part of the set-up and says what to do if something is missing.
# Usage (from the repository folder):  bash scripts/check_setup.sh
# Optional: PLANNER=/path/to/preflight_plan bash scripts/check_setup.sh
cd "$(dirname "$0")/.." || exit 1
PLANNER="${PLANNER:-$HOME/4DPlanning/preflight_plan}"
ok=0; bad=0
pass() { printf "  \033[32mOK\033[0m       %s\n" "$1"; ok=$((ok+1)); }
fail() { printf "  \033[31mMISSING\033[0m  %s\n           -> %s\n" "$1" "$2"; bad=$((bad+1)); }
info() { printf "  \033[33mNOT NOW\033[0m  %s\n           -> %s\n" "$1" "$2"; }

echo "1. Basic tools"
command -v python3 >/dev/null && pass "python3 $(python3 -V 2>&1 | cut -d' ' -f2)" || fail "python3" "sudo apt install -y python3"
for m in requests jsonschema matplotlib; do
  python3 -c "import $m" 2>/dev/null && pass "python package: $m" \
    || fail "python package: $m" "sudo apt install -y python3-$m"
done

echo "2. GPU (for the language model)"
command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1 \
  && pass "NVIDIA GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)" \
  || fail "NVIDIA GPU in WSL" "install the current NVIDIA driver on WINDOWS (not inside Ubuntu), then: wsl --shutdown"

echo "3. Docker (for the simulator)"
if command -v docker >/dev/null; then
  docker ps >/dev/null 2>&1 && pass "docker runs" \
    || fail "docker runs" "sudo service docker start   (or start Docker Desktop); without sudo: sudo usermod -aG docker \$USER, then log in again"
else
  fail "docker" "see SETUP.md, step 3"
fi
[ -d "$HOME/FEDT-Bigrid-UAS" ] && pass "simulator repository ~/FEDT-Bigrid-UAS" \
  || fail "simulator repository" "git clone https://github.com/ShaneCan/FEDT-Bigrid-UAS.git ~/FEDT-Bigrid-UAS"

echo "4. Ollama and the model"
if command -v ollama >/dev/null; then
  pass "ollama installed"
  if curl -s --max-time 3 http://127.0.0.1:11434/api/version >/dev/null; then
    pass "ollama is running"
    ollama list 2>/dev/null | grep -q "qwen3:4b" && pass "model qwen3:4b" || fail "model qwen3:4b" "ollama pull qwen3:4b"
  else
    info "ollama is not running" "ollama serve   (in its own terminal)"
  fi
else
  fail "ollama" "sudo apt install -y zstd && curl -fsSL https://ollama.com/install.sh | sh"
fi

echo "5. 4DPlanning (Task 5)"
[ -x "$PLANNER" ] && "$PLANNER" --help 2>&1 | grep -q "Allowed options" && pass "preflight_plan: $PLANNER" \
  || fail "preflight_plan at $PLANNER" "bash scripts/install_4dplanning.sh"

echo "6. Simulator running?"
if curl -s --max-time 3 http://127.0.0.1:5000/status | grep -q '"connected": *true'; then
  pass "drones answer on ports 5000-5007"
else
  info "simulator not running" "cd ~/FEDT-Bigrid-UAS/cps.asset.crazyflie && bash controller/cf.PyControl/bin/start_dssim_docker.sh webots"
fi

echo "7. Offline self-test of this repository (no simulator, no LLM needed)"
if python3 -c "import jsonschema" 2>/dev/null; then
  (cd pipeline && python3 check_missions.py >/dev/null 2>&1) && pass "mission examples vs. schema" || fail "mission examples vs. schema" "run: cd pipeline && python3 check_missions.py"
  (cd pipeline && python3 validators.py --examples >/dev/null 2>&1) && pass "validators on all examples" || fail "validators on all examples" "run: cd pipeline && python3 validators.py --examples"
fi
tmp=$(mktemp -d)
python3 task5_compiler/planner4d.py task5_compiler/cross_test_mission.json --planner straight --out "$tmp" >/dev/null 2>&1 \
  && pass "4D compiler (built-in straight-line planner)" || fail "4D compiler" "run: python3 task5_compiler/planner4d.py task5_compiler/cross_test_mission.json --planner straight"
if [ -x "$PLANNER" ]; then
  python3 task5_compiler/planner4d.py task5_compiler/cross_test_mission.json --planner "$PLANNER" --out "$tmp" >/dev/null 2>&1 \
    && pass "4D compiler with 4DPlanning" || fail "4D compiler with 4DPlanning" "run it without >/dev/null to see the error"
fi
rm -rf "$tmp"

echo
[ $bad -eq 0 ] && echo "All required parts are in place ($ok checks OK)." \
  || echo "$bad part(s) missing - follow the arrows above, then run this check again."
