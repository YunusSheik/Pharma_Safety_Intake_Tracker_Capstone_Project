"""One-time setup: download the two local RAG models into pharma_intake_tracker/models/.

This is the ONLY step in the project that uses the network, and it runs at
setup time, like `pip install`. At runtime the models are loaded from disk with
HF_HUB_OFFLINE=1 and local_files_only=True, and no request leaves the machine.

    python scripts/download_models.py            # both models
    python scripts/download_models.py --check    # report what is present
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pharma_intake import config  # noqa: E402

MODELS = (
    (config.BI_ENCODER_MODEL, config.BI_ENCODER_DIRNAME),
    (config.CROSS_ENCODER_MODEL, config.CROSS_ENCODER_DIRNAME),
)
# Only the PyTorch safetensors weights + tokenizer/config are needed.
IGNORE = [
    "onnx/*", "openvino/*", "*.onnx", "*.h5", "*.msgpack", "*.ot",
    "tf_model*", "flax_model*", "rust_model*", "pytorch_model.bin", "*.tflite",
]


def present(target: Path) -> bool:
    return (target / "config.json").exists() and any(target.glob("*.safetensors"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report which models are present")
    args = parser.parse_args()

    base = config.models_dir()
    missing = [(repo, name) for repo, name in MODELS if not present(base / name)]
    for repo, name in MODELS:
        print(f"{'present' if present(base / name) else 'MISSING'}: {repo} -> {base / name}")
    if args.check:
        return 0 if not missing else 1
    if not missing:
        print("All models already downloaded.")
        return 0

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub is not installed: pip install -r requirements-models.txt", file=sys.stderr)
        return 2

    for repo, name in missing:
        target = base / name
        print(f"Downloading {repo} ...")
        snapshot_download(repo_id=repo, local_dir=str(target), ignore_patterns=IGNORE)
        print(f"  saved to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
