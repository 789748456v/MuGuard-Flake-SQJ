"""
FT-1 v3.0 Sequential Wave-2 Docker Pipeline Orchestrator.

Runs all remaining remote-Docker v3.0 experiments in strict sequential order so the
8-vCPU / 14-GB remote server never exceeds 2-3 concurrent Docker containers, checkpointing
every phase cleanly to `data/v3_*.json`:
  1. Full K=5 Cross-Vendor `B6_LLM_Judge_Gate` on confirmatory `qwen3.7-plus` (judged by Zhipu `glm-5`) + held-out M_ext
  2. Full K=5 Dynamic `B5_Coverage_Gate` on confirmatory `qwen3.7-plus` (JaCoCo + coverage.py) + held-out M_ext
  3. Fair Python Indentation Normalizer Ablation on `qwen3.7-plus` (all 6 strategies) + held-out M_ext
  4. Real Historical Production Bug-Revert Audit (single-SSH remote batch across 55 eroded tests, testing ALL strategies including B5 & B6!)
  5. Online Top-10 (`qwen3.7-plus`, 203 tests) + held-out M_ext
"""
import subprocess
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent


def run_step(name: str, cmd: list) -> None:
    t0 = time.time()
    print(f"\n{'='*72}\n[WAVE2 START] {name}\n  CMD: {' '.join(cmd)}\n{'='*72}", flush=True)
    p = subprocess.run(cmd, cwd=str(SRC_DIR.parent.parent.parent))
    dt = time.time() - t0
    print(f"[WAVE2 {'OK' if p.returncode == 0 else 'WARN rc=' + str(p.returncode)}] {name} finished in {dt:.1f}s", flush=True)


def main() -> None:
    py = sys.executable
    steps = [
        ("2. Full K=5 B5_Coverage_Gate (qwen3.7-plus, JaCoCo+coverage.py)", [py, str(SRC_DIR / "v3_run_coverage_gate.py"), "--mode", "full_k5", "--model", "qwen3.7-plus", "--workers", "2"]),
        ("3. Fair Python Indentation Normalizer Ablation (qwen3.7-plus)", [py, str(SRC_DIR / "v3_python_indent_ablation.py"), "--models", "qwen3.7-plus", "--workers", "2"]),
        ("4. Real Historical Production Bug-Revert Audit (Batch)", [py, str(SRC_DIR / "v3_replay_real_bugs.py"), "--workers", "2", "--eroded-only", "--max-commits-per-test", "4"]),
        ("5. Online Top-10 Live Loop (qwen3.7-plus)", [py, str(SRC_DIR / "v3_rq4_online_topk_and_cached_latency.py"), "--model", "qwen3.7-plus", "--k", "10", "--workers", "2"]),
    ]
    for name, cmd in steps:
        run_step(name, cmd)
    print("\n=== ALL WAVE-2 DOCKER EXPERIMENTS COMPLETE ===", flush=True)


if __name__ == "__main__":
    main()
