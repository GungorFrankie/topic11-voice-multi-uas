#!/usr/bin/env bash
# Build 4DPlanning (Sow et al., AAMAS 2026) for Task 5, including the two fixes we needed on Ubuntu 24.04.
# Usage: bash scripts/install_4dplanning.sh [TARGET_DIR]      (default: ~/4DPlanning)
set -euo pipefail
DIR="${1:-$HOME/4DPlanning}"

echo "== 1/4 build tools and libraries (asks for your password)"
sudo apt-get update -qq
sudo apt-get install -y build-essential cmake git libboost-all-dev libeigen3-dev

echo "== 2/4 source code -> $DIR"
if [ -d "$DIR/.git" ]; then
  echo "   already cloned, keeping it"
else
  git clone https://github.com/amathsow/4DPlanning.git "$DIR"
fi
cd "$DIR"

echo "== 3/4 remove build files and the prebuilt binary that came from the authors' computer"
# The repository contains a CMake cache and Makefile pointing to /home/amath/... and a preflight_plan built
# against Boost 1.74; both break the build on other machines.
rm -rf CMakeCache.txt CMakeFiles Makefile cmake_install.cmake preflight_plan simulate_paths

echo "== 4/4 compile (Release = optimised; a few minutes)"
cmake -DCMAKE_BUILD_TYPE=Release -Wno-dev . > /dev/null
make -j"$(nproc)"

./preflight_plan --help 2>&1 | grep -q "Allowed options" && echo "OK: $DIR/preflight_plan works"
