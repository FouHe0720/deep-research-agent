from researchos.domain.models import Plan, ResearchRequest, Subtask


class Planner:
    def plan(self, request: ResearchRequest) -> Plan:
        aspects = [
            ("基础定义与主要路线", "definitions primary sources"),
            ("代表论文与公开实现", "papers open source implementations"),
            ("优势、局限与未解决问题", "limitations tradeoffs evidence"),
        ]
        if request.depth == "deep":
            aspects += [("评测与复现方法", "evaluation reproducibility")]
        return Plan(
            max_rounds=request.budget.max_rounds,
            subtasks=[
                Subtask(
                    id=f"q{i}",
                    question=f"{request.topic}: {keywords if request.language == 'en' else label}",
                    keywords=[request.topic, keywords],
                    evidence_type=keywords,
                )
                for i, (label, keywords) in enumerate(aspects, 1)
            ],
        )
