"""Exercise the real offline UI against an already running ResearchOS server.

Install the optional browser dependency and Chromium before use:
    uv sync --extra dev --extra browser
    uv run playwright install chromium
    uv run python scripts/browser_smoke.py http://127.0.0.1:8000

RESEARCH_BROWSER_EXECUTABLE optionally selects an existing Chromium executable.
RESEARCH_API_KEY optionally authenticates through the actual UI connection form.
Use a demonstration database: screenshots include the visible research history.
"""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright, expect


async def smoke(url: str, screenshot: Path, corpus: str = "campus") -> dict:
    console_errors: list[str] = []
    async with async_playwright() as playwright:
        launch = {"headless": True}
        if executable := os.environ.get("RESEARCH_BROWSER_EXECUTABLE"):
            launch["executable_path"] = executable
        browser = await playwright.chromium.launch(**launch)
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on("pageerror", lambda error: console_errors.append(str(error)))
            await page.goto(url, wait_until="networkidle")
            if api_key := os.environ.get("RESEARCH_API_KEY"):
                await page.locator("#api-settings").evaluate("node => { node.open = true; }")
                await page.locator("#api-key").fill(api_key)
                await page.locator("#save-key").click()
            await page.locator("#mode").select_option("offline")
            await page.locator("#corpus").select_option(corpus)
            if corpus == "campus":
                await expect(page.locator("#campus-filters")).to_be_visible()
                await expect(page.locator("#generic-date")).to_be_hidden()
            else:
                await expect(page.locator("#campus-filters")).to_be_hidden()
                await expect(page.locator("#generic-date")).to_be_visible()
                await page.locator("#topic").fill("Agent Memory 的持久化与检索")
            await page.locator("#max-sources").fill("6")
            await page.locator("#submit-research").click()
            await expect(page.locator("#run-status")).to_have_text("已完成", timeout=20_000)
            expected_sources = 3 if corpus == "campus" else 6
            await expect(page.locator("#run-mode")).to_have_text(
                "本地历史资料" if corpus == "campus" else "DEMO / OFFLINE"
            )
            await expect(page.locator("#sources-list .source-item")).to_have_count(expected_sources)
            await expect(page.locator("#stream-state")).to_have_text(
                "全部事件已保存", timeout=15_000
            )
            source_count = await page.locator("#sources-list .source-item").count()
            event_count = await page.locator("#trace-body tr").count()
            assert event_count > 0, "The actual SSE stream produced no events"
            assert not await page.evaluate(
                "() => document.documentElement.scrollWidth > innerWidth"
            ), "Desktop layout overflows horizontally"

            await page.locator("#sources-list .source-item").first.click()
            await expect(page.locator("#source-dialog")).to_be_visible()
            await expect(page.locator(".source-passage")).not_to_be_empty()
            if corpus == "campus":
                await expect(page.locator(".source-facts")).to_contain_text("原文段落")
            await page.locator("#close-source").click()

            await page.locator("#tab-report").click()
            await expect(page.locator("#report-frame")).to_be_visible()
            await page.frame_locator("#report-frame").locator('a[href="#ref-1"]').first.click()
            await expect(page.locator("#source-dialog")).to_be_visible()
            await expect(page.locator(".source-claim").first).to_contain_text("[1]")
            await page.locator("#close-source").click()
            downloaded = []
            with tempfile.TemporaryDirectory(prefix="researchos-browser-") as temp:
                for extension in ("md", "html"):
                    async with page.expect_download() as pending:
                        await page.locator(f"#download-{extension}").click()
                    download = await pending.value
                    target = Path(temp) / download.suggested_filename
                    await download.save_as(target)
                    label = "本地资料 / 历史快照" if corpus == "campus" else "DEMO"
                    assert label in target.read_text(), "Export lost its offline label"
                    downloaded.append(extension)

            await page.locator("#tab-trace").click()
            await expect(page.locator("#trace-body")).to_contain_text("span_end")
            await expect(page.locator("#trace-body")).to_contain_text("tool_result")
            await page.locator("#trace-body details").first.locator("summary").click()
            await expect(page.locator("#trace-body details").first.locator("pre")).to_be_visible()

            await page.locator("#tab-memory").click()
            await page.locator("#memory-query").fill("统计学" if corpus == "campus" else "SQLite")
            await page.locator("#memory-form button").click()
            await expect(page.locator(".memory-count")).to_contain_text("找到")
            memory_count = await page.locator("#memory-results .source-item").count()
            assert memory_count > 0
            run_url = page.url
            await page.reload(wait_until="networkidle")
            await expect(page.locator("#run-workspace")).to_be_visible()
            await expect(page.locator("#run-status")).to_have_text("已完成")
            assert await page.locator(".history-item").count() >= 1
            assert page.url == run_url, "Reload lost the selected run"
            await page.locator(".history-item.active").click()
            await expect(page.locator("#sources-list .source-item")).to_have_count(expected_sources)

            await page.set_viewport_size({"width": 390, "height": 844})
            await page.locator("#tab-report").click()
            assert (
                not await page.frame_locator("#report-frame")
                .locator("body")
                .evaluate("node => node.scrollWidth > innerWidth")
            ), "Mobile report body overflows; tables should scroll within their container"
            assert not await page.evaluate(
                "() => document.documentElement.scrollWidth > innerWidth"
            ), "Mobile research detail overflows horizontally"
            await page.locator("#new-research").click()
            await expect(page.locator("#composer")).to_be_visible()
            assert not await page.evaluate(
                "() => document.documentElement.scrollWidth > innerWidth"
            ), "Mobile request form overflows horizontally"
            await page.goto(run_url, wait_until="networkidle")
            await page.set_viewport_size({"width": 1440, "height": 1100})
            await expect(page.locator("#run-status")).to_have_text("已完成")
            await expect(page.locator("#sources-list .source-item")).to_have_count(expected_sources)
            await page.evaluate("() => document.fonts.ready")
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot), full_page=True)
            assert not console_errors, console_errors
            return {
                "mode": "offline",
                "corpus": corpus,
                "sources": source_count,
                "sse_events": event_count,
                "source_dialog": "passed",
                "iframe_citation_dialog": "passed",
                "downloads": downloaded,
                "memory_results": memory_count,
                "history_reload": "passed",
                "desktop_mobile_overflow": False,
                "mobile_report_body_overflow": False,
                "javascript_errors": console_errors,
                "screenshot": str(screenshot),
            }
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--screenshot", type=Path, default=Path("docs/screenshots/researchos.png"))
    parser.add_argument("--corpus", choices=["teaching", "campus"], default="campus")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(smoke(args.url, args.screenshot, args.corpus)), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
