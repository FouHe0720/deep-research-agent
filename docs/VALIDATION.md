# 运行与测试记录

## 本地回归 · 2026-10-23

环境：Linux / WSL、Python 3.12.15。测试使用本地校园摘录与教学Fixture，不访问收费模型。

| 检查 | 命令 / 方法 | 结果 |
| --- | --- | --- |
| 完整测试 | `make test` | 135 passed，含15项校园场景回归 |
| 静态检查 | `make lint`、`node --check src/researchos/static/app.js` | Ruff lint、format和JavaScript语法检查通过 |
| 摘录完整性 | `make verify-campus` | 6份文档、55段打包摘录、清单及对照字段一致 |
| 原文核对 | `scripts/verify_campus_sources.py --knowledge-root ...` | 6份DOCX哈希、55段原文与摘录一致 |
| 招生对照 | `make campus-demo` | completed；3份本地简章；引用与对照表检查通过 |
| 培养对照 | `researchos ... --corpus campus --document-year 2024` | completed；2份2024版方案；年级为空；173学分、3至6/4至6年分别保留 |
| 校园浏览器流程 | 实际本地服务与Chromium / Playwright | 3来源、34条SSE；来源弹窗、引用定位、MD/HTML下载、中文查询及历史重载通过 |
| 教学浏览器流程 | 同一脚本加 `--corpus teaching` | 6来源、31条SSE；引用、下载、FTS和历史重载通过 |
| 响应式布局 | 1440px桌面、390px手机 | 页面与报告正文无横向溢出，报告表格在容器内滚动，0 JavaScript错误 |
| 培养方案交互 | 页面示例按钮、语言切换及年级筛选 | 培养方案2来源，保留不同修业年限；校园请求固定中文；不支持的明确年级返回空证据 |
| 内容检查 | `make privacy`、Markdown链接与差异检查 | 97个文件通过扫描，41条本地文档链接有效，新增文件空白字符检查通过 |
| Wheel打包 | `uv build --wheel --out-dir artifacts/package` | 成功，包含校园摘录、清单、对照字段与静态资源 |

样例报告在 [examples](../examples/campus-admissions.md)，截图在 [docs/screenshots](screenshots/researchos.png)。校园案例不测量真实模型质量、当前政策有效性或开放领域准确率。

## 教学题集

30题回归复用Agent Memory教学资料：30/30流程完成、30/30引用结构检查通过、90/90工具结果成功。人工审核为0/30，source_validity与faithfulness保留null；题目涉及其他领域不表示这些领域拥有相应资料。完整指标说明见 [eval/README](../eval/README.md)。

扩展脚本生成100条候选，默认 `reviewed=false`；严格审阅校验对未完成审阅的题目返回非零。它不会自动把候选记为人工批准。

## 网络与可选集成 · 2026-10-22

已保存的 [GitHub + arXiv报告](../eval/results/live-mixed.md) 包含2个GitHub项目描述和2篇arXiv论文摘要，2次实际HTTP请求，引用结构检查通过；没有读取完整代码库或论文PDF。

Gateway、OpenAI-compatible、RAG与AgentOps的协议和错误路径使用MockTransport测试。另以ASGITransport调用Gateway真实FastAPI与Mock后端、RAG真实查询接口与临时合成语料，并用AgentOps实际validator检查导出。上述检查不包含真实模型调用。Brave未执行带Key的网络请求。

此前 [CI run 39771277422](https://github.com/FouHe0720/deep-research-agent/actions/runs/39771277422) 在Python 3.11、3.12上各通过120项测试，Docker构建与健康检查通过。该记录对应校园案例加入前的版本。

## 复现

```bash
make lint test demo campus-demo verify-campus
node --check src/researchos/static/app.js
make serve
# 另一个终端，在配置好Playwright与Chromium后运行
.venv/bin/python scripts/browser_smoke.py http://127.0.0.1:8000
.venv/bin/python scripts/browser_smoke.py http://127.0.0.1:8000 --corpus teaching \
  --screenshot artifacts/teaching-browser.png
```

原库核对是可选步骤，见 [CAMPUS_CASE](CAMPUS_CASE.md)。正常运行仅校验仓库内摘录，不会读取原始DOCX。程序耗时随机器与负载变化；结构检查与语义质量分别记录。
