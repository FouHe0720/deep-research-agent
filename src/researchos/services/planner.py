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
        if request.corpus == "campus":
            from researchos.adapters.campus import research_kind

            aspects = (
                [
                    ("校区、版本与适用年级", "document scope cohort version"),
                    ("毕业学分、学制与学位", "credits study duration degree"),
                    ("跨专业差异与尚缺资料", "differences and missing evidence"),
                ]
                if research_kind(request.topic) == "curriculum"
                else [
                    ("招生对象与不得报名的情形", "eligibility and exclusions"),
                    ("招生人数、笔试面试与录取规则", "quota selection and ranking"),
                    ("学籍、退出机制与推免条件", "enrollment exit and recommendation"),
                ]
            )
        return Plan(
            strategy=(
                "campus document comparison; scope-limited deterministic planner"
                if request.corpus == "campus"
                else "bounded deterministic planner"
            ),
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
