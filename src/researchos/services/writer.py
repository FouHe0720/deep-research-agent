import html
import re

from markdown_it import MarkdownIt

from researchos.domain.models import Claim, Run


def safe_text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>|#!])", r"\\\1", value.replace("\n", " "))


class Writer:
    def write(self, run: Run) -> str:
        run.claims = []
        lines = [
            f"# {safe_text(run.request.topic)}",
            "",
            "**DEMO / OFFLINE：固定教学资料，非实时互联网调研。**"
            if run.request.mode == "offline"
            else "**LIVE：仅以下已采集证据支持的有限结果。**",
            "",
            "## 问题定义",
            safe_text(run.request.topic),
            f"范围：{run.request.time_range or '未限制'}；无来源日期的资料不视为范围内证据。",
            "",
            "## 核心发现",
        ]
        if run.request.mode == "offline":
            lines += ["资料仅覆盖 Agent Memory 教学案例；其它主题只演示程序流程，结论待验证。"]
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
            lines += [f"- 来源摘录：{safe_text(claim.text)} [{cid}]"]
        lines += [
            "",
            "## 技术路线对比表",
            "| 证据条目 | 来源类型 | 可确定的范围 |",
            "| --- | --- | --- |",
        ]
        for c in run.claims:
            s = next(s for s in run.sources if s.id == c.source_id)
            lines += [f"| {safe_text(s.title)} [{c.citation_id}] | {s.category} | 仅来源摘录 |"]
        lines += ["", "## 代表论文 / 项目"]
        representatives = [s for s in run.sources if s.category in {"paper", "github"}]
        lines += [safe_text(s.title) for s in representatives] or [
            "固定教学材料没有真实论文，待联网验证。"
        ]
        lines += [
            "",
            "## 优势与限制",
            "可回查摘录和采集时间；不将链接有效等同于语义蕴含。",
            "本报告使用保守的抽取式写作，不做无证据的跨来源推断。",
            "",
            "## 开放问题",
            "证据是否充分支持主题、资料是否过期、是否存在反例，均需要人工复核。",
        ]
        if run.reasons:
            lines += ["", "## 未完成事项"] + [f"- {safe_text(r)}" for r in run.reasons]
        lines += ["", "## 参考文献"]
        for c in run.claims:
            s = next(s for s in run.sources if s.id == c.source_id)
            # Reference definitions are audit-friendly, not Markdown footnote extensions.
            lines += [
                f"[{c.citation_id}]: {safe_text(s.title)} — <{s.url}> "
                f"（采集 {s.fetched_at}；发表 {s.published_at or '未知'}）"
            ]
        return "\n\n".join(lines) + "\n"


def render_html(markdown: str) -> str:
    body = MarkdownIt("commonmark", {"html": False}).render(markdown)
    # Both external and fixture citations resolve to matching reference anchors offline.
    body = re.sub(r"\[(\d+)\]:", r'<span id="ref-\1">[\1]</span>:', body)
    body = re.sub(r"(?<![\w\-])\[(\d+)\](?!</span>)", r'<a href="#ref-\1">[\1]</a>', body)
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
