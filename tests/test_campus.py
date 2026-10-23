import hashlib
import json
from importlib.resources import files

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from researchos.adapters.campus import (
    ADMISSION_TOPIC,
    CURRICULUM_TOPIC,
    CampusSourceProvider,
    manifest,
    source_from_document,
)
from researchos.api.app import create_app
from researchos.domain.models import ResearchRequest, Run
from researchos.infra.config import Settings
from researchos.infra.store import Store
from researchos.services.campus_report import comparison_quote
from researchos.services.engine import Engine
from researchos.services.evidence import EvidenceChecker


async def campus_run(tmp_path, topic=ADMISSION_TOPIC, **scope):
    store = Store(tmp_path / "campus.db")
    return await Engine(store).execute(
        Run(request=ResearchRequest(topic=topic, corpus="campus", **scope))
    )


async def test_campus_admissions_preserve_different_quotas_and_recommendation_scope(
    tmp_path, monkeypatch
):
    async def no_network(*args, **kwargs):
        pytest.fail("campus snapshot attempted a network or model call")

    monkeypatch.setattr("httpx.AsyncClient.send", no_network)
    run = await campus_run(tmp_path, document_year=2024, cohort=2024, campus="威海")
    assert run.status == "completed" and run.checks.passed
    sources = {source.id: source for source in run.sources}
    assert len(sources) == 3
    navigation = sources["navigation-admission-2024"]
    data = sources["data-science-admission-2024"]
    law = sources["computational-law-admission-2024"]
    assert "可同时录取同分学生" in comparison_quote(navigation, "名额")
    assert "排名前25" in comparison_quote(data, "名额")
    assert "具体名额信息以推免当年的学校政策为准" in comparison_quote(data, "推免依据")
    assert "30%" not in comparison_quote(data, "推免依据")
    assert "30%" in comparison_quote(law, "推免依据")
    assert "摘录未说明" == comparison_quote(navigation, "推免依据")
    assert "中午14点" in run.report_md  # Preserve the original ambiguity.
    assert all(source.published_at is None for source in run.sources)
    assert all(source.document.source_sha256 for source in run.sources)
    assert all(source.provenance == "local" for source in run.sources)
    assert all(task.status == "complete" for task in run.plan.subtasks)
    assert run.usage == {"tokens": None, "cost_usd": None}
    stored = Store(tmp_path / "campus.db")
    assert stored.get(run.id).sources[0].document == run.sources[0].document
    assert stored.search(run.id, "统计学")
    assert not stored.search("another-run", "统计学")
    assert not stored.search(run.id, "统计学%")


async def test_curricula_keep_version_distinct_from_cohort_and_preserve_duration_difference(
    tmp_path,
):
    run = await campus_run(tmp_path, CURRICULUM_TOPIC, document_year=2024)
    assert run.status == "completed" and len(run.sources) == 2
    sources = {source.id: source for source in run.sources}
    assert all(source.document.cohort is None for source in run.sources)
    assert "3至6年" in comparison_quote(sources["cs-curriculum-2024"], "修业年限")
    assert "4至6年" in comparison_quote(sources["ai-curriculum-2024"], "修业年限")
    assert all("173学分" in comparison_quote(source, "毕业学分") for source in run.sources)
    assert "适用年级未明确" in run.report_md
    cohort_run = await campus_run(tmp_path, CURRICULUM_TOPIC, cohort=2024)
    assert cohort_run.status == "failed" and not cohort_run.sources
    implicit_cohort = await campus_run(tmp_path, "2024级计算机专业培养方案的毕业学分")
    assert not implicit_cohort.sources


@pytest.mark.parametrize(
    "scope",
    [{"document_year": 2025}, {"cohort": 2025}, {"campus": "青岛"}, {"campus": "济南"}],
)
async def test_missing_scope_does_not_substitute_other_versions_or_teaching_data(tmp_path, scope):
    run = await campus_run(tmp_path, **scope)
    assert run.status == "failed" and run.sources == []
    assert "没有匹配资料" in " ".join(run.reasons)
    assert "Agent Memory" not in run.report_md


@pytest.mark.parametrize(
    "topic", ["2025年实验班招生条件", "青岛校区实验班招生", "量子信息实验班招生", "宿舍分配方案"]
)
async def test_unknown_topic_or_implicit_scope_returns_missing_evidence(tmp_path, topic):
    run = await campus_run(tmp_path, topic)
    assert not run.sources


async def test_physics_keeps_2023_cohort_and_179_credits(tmp_path):
    run = await campus_run(tmp_path, "2023级物理学专业培养方案的毕业学分")
    assert run.status == "completed" and len(run.sources) == 1
    source = run.sources[0]
    assert source.document.cohort == 2023 and source.document.document_year == 2023
    assert "179学分" in comparison_quote(source, "毕业学分")


async def test_summary_tampering_and_local_metadata_tampering_fail_validation(tmp_path):
    run = await campus_run(tmp_path)
    run.report_md = run.report_md.replace(
        "排名前25的同学可进入实验班（", "排名前30的同学可进入实验班（"
    )
    assert not EvidenceChecker().check(run).passed
    run = await campus_run(tmp_path, CURRICULUM_TOPIC)
    run.sources[0].document.cohort = 2024
    assert not EvidenceChecker().check(run).passed
    run.sources[0].document = None
    assert not EvidenceChecker().check(run).passed


def test_packaged_checksums_paragraphs_and_comparison_quotes_are_consistent():
    package = files("researchos").joinpath("data/campus")
    comparisons = json.loads(package.joinpath("comparisons.json").read_text())
    for document in manifest()["documents"]:
        assert (
            hashlib.sha256(package.joinpath(document["file_name"]).read_bytes()).hexdigest()
            == (document["excerpt_sha256"])
        )
        source = source_from_document(document, "q1")
        for field in comparisons[document["id"]]:
            assert "摘录未说明" != comparison_quote(source, field)
        assert "@" not in source.excerpt
    with pytest.raises(ValidationError, match="offline"):
        ResearchRequest(topic=ADMISSION_TOPIC, corpus="campus", mode="live")


def test_campus_api_persists_provenance_and_exports_report(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/research",
            json={"topic": CURRICULUM_TOPIC, "corpus": "campus", "document_year": 2024},
        )
        assert response.status_code == 202
        run_id = response.json()["id"]
        # The actual event endpoint waits for the bounded background job.
        events = client.get(f"/api/research/{run_id}/events")
        assert "finished" in events.text
        run = client.get(f"/api/research/{run_id}").json()
        assert run["status"] == "completed" and len(run["sources"]) == 2
        assert all(source["document"]["cohort"] is None for source in run["sources"])
        report = client.get(f"/api/research/{run_id}/report?format=html")
        assert "3至6年" in report.text and "4至6年" in report.text
        assert 'href="#ref-1"' in report.text


def test_broad_query_can_list_only_the_known_document_types():
    provider = CampusSourceProvider(ResearchRequest(topic="比较实验班招生条件", corpus="campus"))
    assert len(provider.documents) == 3
