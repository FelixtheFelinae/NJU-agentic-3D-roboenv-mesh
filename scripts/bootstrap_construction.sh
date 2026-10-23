#!/usr/bin/env bash
# User-local Ubuntu COLMAP and isolated 3DGS CUDA extensions; no weights/API calls.
set -euo pipefail
unset PYTHONPATH PYTHONHOME
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_root="${ROBOENV_RUNTIME_ROOT:-$HOME/.local/share/roboenv}"
gaussian_revision="54c035f7834b564019656c3e3fcc3646292f727d"
gaussian_repo="$runtime_root/gaussian-splatting"
gaussian_env="$runtime_root/gaussian-env"
[[ "$(uname -s)" == Linux ]] || { echo 'Linux required' >&2; exit 1; }
[[ -x "$project_root/.venv/bin/python" ]] || { echo 'Prepare project .venv first' >&2; exit 1; }
command -v uv >/dev/null
command -v nvcc >/dev/null
mkdir -p "$runtime_root"

"$project_root/.venv/bin/python" - "$runtime_root" <<'PY'
import json
import shlex
import subprocess
import sys
from pathlib import Path

base = Path(sys.argv[1]).resolve() / "colmap"
cache, root = base / "debs", base / "root"
cache.mkdir(parents=True, exist_ok=True)
root.mkdir(exist_ok=True)
if not (root / "usr/bin/colmap").is_file():
    plan = subprocess.check_output(["apt-get", "-s", "install", "colmap"], text=True)
    (base / "install-plan.txt").write_text(plan)
    # Installed dependencies can still be loaded from the host's standard paths.
    packages = [line.split()[1] for line in plan.splitlines() if line.startswith("Inst ")]
    if "colmap" not in packages:
        packages.append("colmap")
    subprocess.run(["apt-get", "download", *packages], cwd=cache, check=True, timeout=180)
    for package in sorted(cache.glob("*.deb")):
        subprocess.run(["dpkg-deb", "-x", str(package), str(root)], check=True)
wrapper = base / "bin/colmap"
wrapper.parent.mkdir(exist_ok=True)
wrapper.write_text(
    '#!/usr/bin/env bash\nset -euo pipefail\n'
    + 'colmap_install_root=' + shlex.quote(str(root)) + '\n'
    + 'export LD_LIBRARY_PATH="$colmap_install_root/usr/lib/x86_64-linux-gnu:$colmap_install_root/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
    + 'export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"\n'
    + 'exec "$colmap_install_root/usr/bin/colmap" "$@"\n'
)
wrapper.chmod(0o755)
subprocess.run([str(wrapper), "-h"], check=True, stdout=subprocess.DEVNULL)
print(json.dumps({"colmap": str(wrapper), "system_packages_modified": False}))
PY

if [[ ! -d "$gaussian_repo/.git" ]]; then
    [[ ! -e "$gaussian_repo" ]] || { echo 'Existing non-Git source directory; inspect manually' >&2; exit 1; }
    git init "$gaussian_repo"
    git -C "$gaussian_repo" remote add origin https://github.com/graphdeco-inria/gaussian-splatting.git
    timeout 180 git -C "$gaussian_repo" fetch --depth 1 origin "$gaussian_revision"
    git -C "$gaussian_repo" checkout --detach FETCH_HEAD
fi
[[ "$(git -C "$gaussian_repo" rev-parse HEAD)" == "$gaussian_revision" ]] || {
    echo 'Existing Gaussian checkout differs from the pinned revision; preserving it.' >&2; exit 1;
}
git -C "$gaussian_repo" diff --exit-code
timeout 240 git -C "$gaussian_repo" submodule update --init --recursive --depth 1 \
    submodules/diff-gaussian-rasterization submodules/simple-knn
if [[ ! -x "$gaussian_env/bin/python" ]]; then
    uv venv --python "$project_root/.venv/bin/python" "$gaussian_env"
fi
# Share only this project's pinned runtime packages; new extensions install into
# the dedicated worker environment. No other Conda/ROS environment is modified.
"$project_root/.venv/bin/python" - "$project_root" "$gaussian_env" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]) / ".venv/lib/python3.12/site-packages"
target = Path(sys.argv[2]) / "lib/python3.12/site-packages/project-pinned-dependencies.pth"
target.write_text(str(source.resolve()) + "\n")
PY
uv pip install --python "$gaussian_env/bin/python" --no-deps ninja==1.13.2 wheel==0.48.0
export PATH="$gaussian_env/bin:$PATH"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-12.4}"
export MAX_JOBS="${MAX_JOBS:-2}"
export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-8.9}"
uv pip install --python "$gaussian_env/bin/python" --no-build-isolation --no-deps \
    "$gaussian_repo/submodules/diff-gaussian-rasterization" "$gaussian_repo/submodules/simple-knn"
"$gaussian_env/bin/python" - <<'PY'
import torch
import diff_gaussian_rasterization
from simple_knn._C import distCUDA2

assert torch.cuda.is_available(), "Worker CUDA unavailable"
points = torch.tensor([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]], device="cuda")
assert torch.isfinite(distCUDA2(points)).all(), "KNN CUDA smoke failed"
print("3DGS extension imports and KNN CUDA smoke passed; no model weights downloaded")
PY
echo "Configure background.colmap=$runtime_root/colmap/bin/colmap"
echo "Configure background.gaussian_repo=$gaussian_repo"
echo "Configure background.gaussian_python=$gaussian_env/bin/python"
echo 'Ubuntu package/CUDA/architecture defaults target the documented RTX 4070 Ti SUPER host.'
