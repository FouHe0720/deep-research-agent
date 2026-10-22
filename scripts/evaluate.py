"""Run the offline regression dataset and export measured metrics plus one demo."""

import argparse
import asyncio
from pathlib import Path

from researchos.services.evaluation import evaluate_dataset


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("eval/research_questions.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("eval/results/offline.json"))
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args(argv)
    result = asyncio.run(
        evaluate_dataset(args.dataset, args.output, artifacts_dir=args.artifacts_dir)
    )
    print(
        f"Evaluated {result['question_count']} offline structural cases; "
        f"human-reviewed prompts: {result['human_reviewed_count']}."
    )
    print("Internet validity and semantic faithfulness remain null; see constraints in output.")


if __name__ == "__main__":
    main()
