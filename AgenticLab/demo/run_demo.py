"""Start the Streamlit demo with the agentic venv's Python (agentic and agentic_lab installed).

  python agentic_lab/demo/run_demo.py          # real models, settings from .env
  python agentic_lab/demo/run_demo.py --fake   # no keys: fake models + throwaway essays in agentic_lab/local/demo
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]


def prepare_fake_data(src_dir: Path, dest_dir: Path, version: str) -> None:
    """Copy one classroom's labels (and assignment file) and generate placeholder essays for it."""
    from agentic import dataset
    from agentic.config import Settings
    from agentic_lab.labels import load_labels
    from agentic_lab.synth.generate_essays import generate
    from agentic_lab.wiring import build_chat

    dest_dir.mkdir(parents=True, exist_ok=True)
    for path in (dataset.labels_path(src_dir, version), dataset.assignment_path(src_dir, version)):
        if path.exists():
            shutil.copyfile(path, dest_dir / path.name)
    chat = build_chat(Settings(fake_llm=True))
    generate(chat, load_labels(dest_dir, version), dataset.essays_path(dest_dir, version), version,
             assignment=dataset.load_assignment(dest_dir, version))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fake", action="store_true", help="fake models and throwaway essays, no API keys")
    p.add_argument("--classroom", default="1.00", help="classroom version for --fake (default 1.00)")
    p.add_argument("--port", default="8501")
    args = p.parse_args()
    env = dict(os.environ)
    if args.fake:
        demo = LAB / "local" / "demo"
        prepare_fake_data(LAB / "data" / "synthetic", demo, args.classroom)
        env.update(FAKE_LLM="1", DATA_DIR=str(demo), CLASSROOM=args.classroom)
    cmd = [sys.executable, "-m", "streamlit", "run", str(LAB / "demo" / "app.py"),
           "--server.headless", "true", "--server.port", args.port]
    return subprocess.call(cmd, cwd=LAB, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
