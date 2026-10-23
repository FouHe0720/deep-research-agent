"""Small, attributed local DOCX excerpts; no network or model calls.

Routing is deliberately limited to named admissions and curriculum documents.
An unsupported topic or scope returns no evidence instead of unrelated fixtures.
"""

import hashlib
import json
import re
from importlib.resources import files

from researchos.domain.models import DocumentOrigin, ResearchRequest, SourceRecord, Subtask

ALIASES = {
    "navigation-admission-2024": ("智能导航", "导航特色班"),
    "data-science-admission-2024": ("数据科学实验班", "数据科学", "统计学"),
    "computational-law-admission-2024": ("计算法学",),
    "cs-curriculum-2024": ("计算机科学与技术", "计算机专业", "计算机"),
    "ai-curriculum-2024": ("人工智能",),
    "physics-curriculum-2023": ("物理学", "物理专业"),
}

ADMISSION_TOPIC = "比较智能导航、数据科学和计算法学实验班的招生条件、名额与选拔方式"
CURRICULUM_TOPIC = "比较计算机科学与技术和人工智能专业培养方案的毕业学分与修业年限"


def manifest() -> dict:
    return json.loads(files("researchos").joinpath("data/campus/manifest.json").read_text())


def research_kind(topic: str) -> str:
    return "curriculum" if re.search("培养方案|毕业|学分|学制|修业|物理学", topic) else "admission"


def broad_topic(topic: str) -> bool:
    remainder = re.sub(
        r"20\d{2}[年级版]?|山东大学|山大|威海|济南|青岛|校区|"
        r"培养方案|毕业学分|弹性修业年限|修业年限|标准学制|学制|毕业|学分|学位|"
        r"实验班|特色班|招生|报名|录取|人数|名额|条件|对象|选拔方式|选拔|规则|推免|"
        r"对照|对比|比较|各专业|专业|年度|年级|历史|资料|哪些|多少|与|和|的|及|"
        r"[\s，,。；;？?、（）()：:—-]",
        "",
        topic,
    )
    return not remainder


def source_from_document(document: dict, subtask_id: str) -> SourceRecord:
    package = files("researchos").joinpath("data/campus")
    if (
        hashlib.sha256(package.joinpath(document["file_name"]).read_bytes()).hexdigest()
        != (document["excerpt_sha256"])
    ):
        raise ValueError("campus excerpt file checksum mismatch")
    markdown = package.joinpath(document["file_name"]).read_text()
    if any(
        f"## 原文第 {paragraph['index']} 段\n\n{paragraph['text']}\n" not in markdown
        for paragraph in document["paragraphs"]
    ):
        raise ValueError("campus manifest paragraph differs from packaged excerpt")
    excerpt = "\n".join(
        f"原文第 {paragraph['index']} 段：{paragraph['text']}"
        for paragraph in document["paragraphs"]
    )
    return SourceRecord(
        id=document["id"],
        url=f"campus://sdu-weihai/{document['id']}",
        title=document["title"],
        category="document",
        excerpt=excerpt,
        provenance="local",
        content_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
        subtask_ids=[subtask_id],
        document=DocumentOrigin(
            dataset_id=manifest()["dataset_id"],
            source_path=document["source_path"],
            source_sha256=document["source_sha256"],
            excerpt_sha256=document["excerpt_sha256"],
            paragraph_numbers=[paragraph["index"] for paragraph in document["paragraphs"]],
            campus=document["campus"],
            document_year=document["year"],
            cohort=int(document["cohort"]) if document["cohort"] else None,
        ),
    )


class CampusSourceProvider:
    def __init__(self, request: ResearchRequest):
        self.warnings: list[str] = []
        self.kind = research_kind(request.topic)
        documents = [d for d in manifest()["documents"] if f"-{self.kind}-" in d["id"]]
        named = [
            document
            for document in documents
            if any(alias in request.topic for alias in ALIASES[document["id"]])
        ]
        self.documents = named if named else documents if broad_topic(request.topic) else []
        # A year written in the question narrows the document version. Explicit
        # cohort filters only match documents that actually state a cohort.
        years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", request.topic)
        year = request.document_year or (int(years[0]) if len(set(years)) == 1 else None)
        cohorts = re.findall(r"(?<!\d)(20\d{2})级", request.topic)
        cohort = request.cohort or (int(cohorts[0]) if len(set(cohorts)) == 1 else None)
        campus = request.campus
        mentioned_campuses = [name for name in ("威海", "济南", "青岛") if name in request.topic]
        if campus is None and len(mentioned_campuses) == 1:
            campus = mentioned_campuses[0]
        self.documents = [
            document
            for document in self.documents
            if (campus is None or document["campus"] == campus)
            and (year is None or document["year"] == year)
            and (cohort is None or document["cohort"] == str(cohort))
        ]
        if not self.documents:
            self.warnings.append("所选主题、校区或年度没有匹配资料；未使用其他年级或教学资料补齐。")

    async def search(self, subtask: Subtask, limit: int) -> list[SourceRecord]:
        # Rotate the bounded candidate list so three selected documents are
        # reachable with the engine's two-candidates-per-subquestion budget.
        offset = (int(subtask.id[1:]) - 1) % max(1, len(self.documents))
        candidates = self.documents[offset:] + self.documents[:offset]
        return [source_from_document(document, subtask.id) for document in candidates[:limit]]
