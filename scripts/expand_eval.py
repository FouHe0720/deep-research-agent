"""Expand pending candidates, validate a human-review gate, or record explicit review."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

from researchos.services.evaluation import EvalQuestion, load_questions

PROMPTS = (
    "有哪些主要路线与适用条件？",
    "应如何设计可重复的比较实验？",
    "如何记录来源、版本与时间范围？",
    "常见实现有哪些工程取舍？",
    "失败案例和反例应如何收集？",
    "哪些结论需要额外的一手证据？",
    "应使用哪些定量与定性指标？",
    "如何分析成本、延迟与质量的关系？",
    "需要怎样的隐私与数据保留策略？",
    "现有资料有哪些未解决的问题？",
)


def _write(path: Path, questions: list[EvalQuestion]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(question.model_dump_json() + "\n" for question in questions), encoding="utf-8"
    )


def generate_candidates(source: Path, destination: Path) -> list[EvalQuestion]:
    bases = {}
    for question in load_questions(source):
        bases.setdefault(question.category, question)
    if len(bases) < 10:
        raise ValueError("candidate expansion requires at least 10 source categories")
    questions = [
        EvalQuestion(
            id=f"candidate-{index * 10 + variant + 1:03d}",
            category=question.category,
            topic=f"{question.topic[:350]} · {prompt}",
            reviewed=False,
        )
        for index, question in enumerate(list(bases.values())[:10])
        for variant, prompt in enumerate(PROMPTS)
    ]
    _write(destination, questions)
    return questions


def mark_reviewed(path: Path, identifier: str, reviewer: str):
    """Only called by explicit CLI action after a person reviews the question."""
    if not reviewer.strip():
        raise ValueError("an explicit reviewer label is required")
    questions = load_questions(path)
    selected = next((question for question in questions if question.id == identifier), None)
    if selected is None:
        raise ValueError("unknown question id")
    selected.reviewed = True
    selected.reviewer = reviewer.strip()[:100]
    selected.reviewed_at = datetime.now(UTC).isoformat()
    _write(path, questions)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--generate", type=Path, help="write exactly 100 unreviewed candidates")
    action.add_argument("--validate", type=Path, help="require >=30 unique, human-reviewed prompts")
    action.add_argument("--mark-reviewed", metavar="ID", help="affirm an explicit human review")
    parser.add_argument("--dataset", type=Path, default=Path("eval/research_questions.jsonl"))
    parser.add_argument("--reviewer", help="human review label; required for --mark-reviewed")
    args = parser.parse_args(argv)
    try:
        if args.generate:
            values = generate_candidates(args.dataset, args.generate)
            print(f"Generated {len(values)} pending candidates; no human review was approved.")
        elif args.validate:
            values = load_questions(args.validate, require_reviewed=True)
            print(f"Validated {len(values)} unique prompts with explicit human-review records.")
        else:
            if not args.reviewer:
                raise ValueError("--reviewer is required; this records your manual review")
            mark_reviewed(args.dataset, args.mark_reviewed, args.reviewer)
            print(f"Recorded explicit review for {args.mark_reviewed}.")
    except ValueError as error:
        parser.exit(1, f"Validation failed: {error}\n")


if __name__ == "__main__":
    main()
