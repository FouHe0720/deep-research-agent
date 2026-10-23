"""Versioned comparison tables composed from checked, verbatim DOCX passages."""

import json
from importlib.resources import files

from researchos.adapters.campus import manifest, research_kind
from researchos.domain.models import Claim, Run, SourceRecord
from researchos.services.writer import reference_line, safe_text


def comparison_quote(source: SourceRecord, field: str) -> str:
    comparisons = json.loads(
        files("researchos").joinpath("data/campus/comparisons.json").read_text()
    )
    fact = comparisons.get(source.id, {}).get(field)
    if not fact:
        return "摘录未说明"
    document = next(document for document in manifest()["documents"] if document["id"] == source.id)
    paragraph = next(p for p in document["paragraphs"] if p["index"] == fact["paragraph"])
    if fact["quote"] not in paragraph["text"] or paragraph["text"] not in source.excerpt:
        raise ValueError("comparison quote is not bound to its source paragraph")
    return f"{fact['quote']}（第 {fact['paragraph']} 段）"


def comparison_table(sources: list[SourceRecord], curriculum: bool) -> str:
    fields = (
        ["毕业学分", "标准学制", "修业年限", "学位"]
        if curriculum
        else ["招生对象", "名额", "选拔规则", "推免依据"]
    )
    lines = [
        "| 文档与范围 | " + " | ".join(fields) + " |",
        "| " + " | ".join(["---"] * (len(fields) + 1)) + " |",
    ]
    for citation_id, source in enumerate(sources, 1):
        origin = source.document
        scope = (
            f"{origin.campus}；文档年度{origin.document_year}；"
            f"{str(origin.cohort) + '级' if origin.cohort else '适用年级未明确'}"
        )
        lines += [
            f"| {safe_text(source.title)} [{citation_id}]；{safe_text(scope)} | "
            + " | ".join(safe_text(comparison_quote(source, field)) for field in fields)
            + " |"
        ]
    return "\n".join(lines)


def write_campus_report(run: Run) -> str:
    run.claims = []
    curriculum = research_kind(run.request.topic) == "curriculum"
    lines = [
        f"# {safe_text(run.request.topic)}",
        "",
        "**本地资料 / 历史快照：山东大学（威海）2023—2024 年资料摘录。**",
        "",
        "这些材料来自本地 RAG 知识库。报告保留原文条件、文档版号和段落位置，"
        "不代表当前招生或教务政策。未调用搜索服务或模型。",
        "",
        "## 资料范围",
        "",
        f"校区：{run.request.campus or '按题目检索'}；"
        f"文档年度：{run.request.document_year or '按题目检索'}；"
        f"明确适用年级：{str(run.request.cohort) + '级' if run.request.cohort else '未另设筛选'}。",
        "",
        "文档版本年度与适用年级分别记录。仅写“2024版”的培养方案，"
        "不据此推定适用于2024级；发布时间缺失时保留为空。",
        "",
        "## 培养要求对照" if curriculum else "## 招生条件对照",
        "",
        "下表为原文短摘录。报名限制及例外条件见后面的完整节选。",
        "",
    ]
    lines += [comparison_table(run.sources, curriculum)]
    if not run.sources:
        lines += ["", "没有匹配的资料，无法生成对照。可调整文档年度、年级、校区或研究主题。"]
    lines += ["", "## 阅读时需要保留的条件", ""]
    lines += (
        [
            "各方案学分组成、学位和弹性修业年限分别展示。学分总量相同也不能说明专业课程相同。",
            "资料包未收录完整课程表，也没有培养方案修订记录；"
            "不能据此判断课程替代关系或跨年级执行要求。",
        ]
        if curriculum
        else [
            "各班的招生对象和不得报名情形不同。表内人数属于各自简章中的计划或录取规则，"
            "不能据此认定个人符合条件。",
            "推免条件按各自原文展示；“摘录未说明”表示资料不足。"
            "报名时间属于2024年，原文中的时间歧义保留，不能作为当前报名提醒。",
        ]
    )
    lines += ["", "## 原文与定位", ""]
    for citation_id, source in enumerate(run.sources, 1):
        run.claims.append(
            Claim(
                text=source.excerpt,
                source_id=source.id,
                passage=source.excerpt,
                citation_id=citation_id,
                subtask_id=source.subtask_ids[0],
            )
        )
        lines += [f"### {safe_text(source.title)} [{citation_id}]", ""]
        lines += [safe_text(paragraph) for paragraph in source.excerpt.splitlines()]
        lines += [
            "",
            f"原文件：{safe_text(source.document.source_path)}；"
            f"原文件 SHA-256：`{source.document.source_sha256}`。",
            "",
        ]
    if run.reasons:
        lines += ["## 待补资料", ""] + [f"- {safe_text(reason)}" for reason in run.reasons]
    lines += ["", "## 参考文献", ""]
    lines += [reference_line(index, source) for index, source in enumerate(run.sources, 1)]
    lines += [
        "",
        "来源定位使用 DOCX word/document.xml 中所有 w:p 的一基序号，包含空段和表格段。"
        "原文件哈希用于持有原库时核对；本次离线运行只校验仓库内摘录和清单，"
        "未重新读取原始 DOCX。学校材料版权归原作者，代码许可证不覆盖这些材料。",
    ]
    return "\n\n".join(line for line in lines if line).rstrip() + "\n"
