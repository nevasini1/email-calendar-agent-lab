from __future__ import annotations

from pathlib import Path

from .evals import validate_eval_files

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    eval_dir = ROOT / "evals"
    counts = validate_eval_files(
        (
            eval_dir / "stable.jsonl",
            eval_dir / "generated.jsonl",
            eval_dir / "heldout.jsonl",
            eval_dir / "workflow.jsonl",
        )
    )
    print(f"eval JSONL valid: {counts}")


if __name__ == "__main__":
    main()
