#!/usr/bin/env bash
# Execute on Linux/NVIDIA, from the project root. Do not run on macOS.
set -euo pipefail
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Run this script on the Linux CUDA server, not on macOS." >&2
  exit 1
fi
if [[ $# -ne 1 || ! -d "$1/sam3" ]]; then
  echo "Usage: bash scripts/bootstrap_cuda.sh /absolute/path/to/official-sam3-checkout" >&2
  exit 1
fi
command -v nvidia-smi >/dev/null
nvidia-smi
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install torch==2.10.0 torchvision --index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -m pip install -e "$1"
.venv/bin/python -m pip install -e '.[server,dev]'
.venv/bin/scene-perception doctor --require-cuda
