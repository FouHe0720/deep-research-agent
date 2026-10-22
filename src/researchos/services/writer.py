import html
import re

from markdown_it import MarkdownIt

from researchos.domain.models import Claim, Run, SourceRecord


def safe_text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>|#!])", r"\\\1", value.replace("\n", " "))


def reference_line(citation_id: int, source: SourceRecord) -> str:
    return (
        f"[{citation_id}]: {safe_text(source.title)} — <{source.url}> "
        f"（采集 {source.fetched_at}；发表 {source.published_at or '未知'}）"
    )


class Writer:
    def write(self, run: Run) -> str:
        run.claims = []
        english = run.request.language == "en"

        def tr(zh: str, en: str) -> str:
            return en if english else zh

        lines = [
            f"# {safe_text(run.request.topic)}",
            "",
            tr(
                "**DEMO / OFFLINE：固定教学资料，非实时互联网调研。**",
                "**DEMO / OFFLINE: fixed teaching notes, not internet research.**",
            )
            if run.request.mode == "offline"
            else tr(
                "**LIVE：仅以下已采集证据支持的有限结果。**",
                "**LIVE: limited to the evidence actually collected below.**",
            ),
            "",
            tr("## 问题定义", "## Problem definition"),
            safe_text(run.request.topic),
            f"Time range: {run.request.time_range or 'unrestricted'}; "
            "undated items cannot pass a year filter.",
            "",
            tr("## 核心发现", "## Findings"),
        ]
        if run.request.mode == "offline":
            lines += [
                tr(
                    "资料仅覆盖 Agent Memory 教学案例；其它主题只演示程序流程，结论待验证。",
                    "Agent Memory notes only; other topics lack relevant evidence.",
                )
            ]
        for s in run.sources:
            cid = len(run.claims) + 1
            claim = Claim(
                text=s.excerpt,
                source_id=s.id,
                passage=s.excerpt,
                citation_id=cid,
                subtask_id=s.subtask_ids[0],
            )
            run.claims.append(claim)
            lines += [f"- {tr('来源摘录', 'Original excerpt')}: {safe_text(claim.text)} [{cid}]"]
        lines += [
            "",
            tr("## 技术路线对比表", "## Evidence comparison"),
            tr("| 证据条目 | 来源类型 | 可确定的范围 |", "| Evidence | Type | Scope |"),
            "| --- | --- | --- |",
        ]
        for c in run.claims:
            s = next(s for s in run.sources if s.id == c.source_id)
            lines += [
                f"| {safe_text(s.title)} [{c.citation_id}] | {s.category} | "
                f"{tr('仅来源摘录', 'excerpt only')} |"
            ]
        lines += ["", tr("## 代表论文 / 项目", "## Papers and projects")]
        representatives = [s for s in run.sources if s.category in {"paper", "github"}]
        lines += [safe_text(s.title) for s in representatives] or [
            tr(
                "固定教学材料没有真实论文，待联网验证。",
                "No representative paper/project was collected; further verification is needed.",
            )
        ]
        lines += [
            "",
            tr("## 优势与限制", "## Strengths and limitations"),
            tr(
                "可回查摘录和采集时间；不将链接有效等同于语义蕴含。",
                "Auditable excerpts and fetch dates do not establish semantic entailment.",
            ),
            tr(
                "本报告使用保守的抽取式写作，不做无证据的跨来源推断。",
                "Conservative extractive writing avoids unsupported cross-source inference.",
            ),
            "",
            tr("## 开放问题", "## Open questions"),
            tr(
                "证据是否充分支持主题、资料是否过期、是否存在反例，均需要人工复核。",
                "Humans should review topic support, staleness and possible counterexamples.",
            ),
        ]
        if run.reasons:
            lines += ["", tr("## 未完成事项", "## Incomplete work")] + [
                f"- {safe_text(r)}" for r in run.reasons
            ]
        if run.config.get("auxiliary_analysis"):
            lines += [
                "",
                tr("## 自动辅助分析（待人工核验）", "## Auxiliary analysis (unverified)"),
                tr(
                    "以下模型输出不属于已校验主张，不视为已验证结论。",
                    "Model output below is outside verified claims and requires human review.",
                ),
                safe_text(run.config["auxiliary_analysis"]),
            ]
        lines += ["", tr("## 参考文献", "## References")]
        for c in run.claims:
            s = next(s for s in run.sources if s.id == c.source_id)
            lines += [reference_line(c.citation_id, s)]
        # Keep table rows adjacent; surrounding prose uses paragraph breaks.
        report = "\n\n".join(lines) + "\n"
        return re.sub(r"(?<=\|)\n\n(?=\|)", "\n", report)


def render_html(markdown: str) -> str:
    reference_ids = {int(n) for n in re.findall(r"^\[(\d+)\]:", markdown, re.MULTILINE)}

    def link(match):
        number = int(match[1])
        return f"[{number}](#ref-{number})" if number in reference_ids else match[0]

    prepared = re.sub(r"(?<!\\)\[(\d+)\](?!:)", link, markdown)
    prepared = re.sub(r"^\[(\d+)\]:", r"Reference \1 ·", prepared, flags=re.MULTILINE)
    body = MarkdownIt("commonmark", {"html": False}).enable("table").render(prepared)
    body = re.sub(r"<p>Reference (\d+) ·", r'<p id="ref-\1">[\1] ·', body)
    return (
        '<!doctype html><html lang="zh"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>ResearchOS report</title><style>body{max-width:920px;margin:40px auto;"
        "padding:0 24px;font:16px/1.8 system-ui;color:#1e293b}table{border-collapse:collapse}"
        "td,th{border:1px solid #cbd5e1;padding:10px}a{color:#047857}"
        "</style><body>"
        + body
        + "<footer>"
        + html.escape(
            "ResearchOS · structural citation checks do not establish semantic faithfulness."
        )
        + "</footer></body></html>"
    )
