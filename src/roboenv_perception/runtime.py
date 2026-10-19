"""Production workflows run on Linux/NVIDIA; fixture tests never imply CUDA validation."""

import platform


def require_cuda():
    if platform.system() != "Linux":
        raise RuntimeError(
            "Production workflows require a Linux/NVIDIA CUDA server; no CPU/MPS fallback"
        )
    try:
        import torch
    except ImportError:
        raise RuntimeError(
            "Install CUDA-enabled PyTorch in the remote server environment"
        ) from None
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; no production CPU/MPS fallback")
    return torch


def cuda_info():
    torch = require_cuda()
    return {
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "compute_capability": list(torch.cuda.get_device_capability(0)),
        "gpu_count": torch.cuda.device_count(),
    }
