#!/usr/bin/env bash
# Execute on Linux/NVIDIA, from the project root. Do not run on macOS.
set -euo pipefail
# ROS shell setup can inject Python 3.10 packages into this Python 3.12 venv.
unset PYTHONPATH PYTHONHOME
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
.venv/bin/python -m pip install --no-deps torch==2.10.0+cu128 torchvision==0.25.0+cu128 --index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -m pip install --index-url https://mirrors.aliyun.com/pypi/simple/ -r requirements/cuda-dev.lock.txt -e "$1" -e '.[server,dev]'
.venv/bin/scene-perception doctor --require-cuda
.venv/bin/python -c 'from sam3.model.sam3_image_processor import Sam3Processor; from sam3.model.sam3_video_predictor import Sam3VideoPredictor; print("SAM 3 image/video interfaces imported; no checkpoint loaded.")'
