# 可复制演示

所有命令从仓库根目录运行，使用项目 `.venv`。默认路径不会调用付费模型或真实搜索。
界面、报告和输出中的 **DEMO / OFFLINE** 表示自编教学资料，不能当作实时互联网研究。

## 1. 安装、离线报告和本地页面

```bash
uv sync --extra dev --extra mcp --frozen
make demo
make serve
```

浏览器打开 <http://127.0.0.1:8000>，主题使用“Agent Memory 有哪些主要路线和优缺点”，
模式保持 Offline，提交后查看计划、子问题、事件、来源摘录、引用和 Trace。点击引用应能
定位对应来源；下载 Markdown/HTML 后可以不访问外网再次阅读。

`make demo` 是独立 CLI 路径，产物在 `artifacts/demo/`，包含 report.md、report.html、
run.json 和 research.db。自定义主题和导出位置：

```bash
.venv/bin/researchos 'Agent Memory 的持久化与检索' --output artifacts/memory-demo
```

其它主题仍使用同一套 Agent Memory Fixture，适合检查流程，不能拿输出冒充对应主题的知识。
不要把 `fixture://` 链接改成看起来真实的论文或网页地址。

## 2. 实际 HTTP + SSE + 导出

保持 `make serve` 运行，另开终端：

```bash
curl --fail-with-body -sS http://127.0.0.1:8000/health
curl --fail-with-body -sS http://127.0.0.1:8000/api/config
```

下面一次脚本会创建任务、消费真实 SSE、取得最终 Run，再把报告和 Trace 保存到忽略目录：

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
import httpx

out = Path('artifacts/api-demo')
out.mkdir(parents=True, exist_ok=True)
with httpx.Client(base_url='http://127.0.0.1:8000', timeout=90) as client:
    response = client.post('/api/research', json={
        'topic': 'Agent Memory 有哪些主要路线和优缺点',
        'mode': 'offline', 'depth': 'standard', 'max_sources': 6,
        'budget': {'max_seconds': 60, 'max_tool_calls': 12,
                   'max_rounds': 1, 'max_cost_usd': 0},
    })
    response.raise_for_status()
    run_id = response.json()['id']
    print('Run:', run_id, 'HTTP:', response.status_code)
    with client.stream('GET', f'/api/research/{run_id}/events') as stream:
        stream.raise_for_status()
        for line in stream.iter_lines():
            if line.startswith('data: '):
                event = json.loads(line[6:])
                print(event['seq'], event['kind'])
    response = client.get(f'/api/research/{run_id}')
    response.raise_for_status()
    run = response.json()
    for format in ('md', 'html'):
        report = client.get(f'/api/research/{run_id}/report', params={'format': format})
        report.raise_for_status()
        (out / f'report.{format}').write_text(report.text, encoding='utf-8')
    trace = client.get(f'/api/research/{run_id}/trace', params={'format': 'agentops'})
    trace.raise_for_status()
    (out / 'trace.json').write_text(json.dumps(trace.json(), ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'run.json').write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Status:', run['status'], 'Sources:', len(run['sources']), 'Checks:', run['checks'])
    print('Files:', out)
PY
```

API POST 返回 HTTP 202 和完整 Run；任务标识字段是 `id`。事件流有递增 `id:`，可用
`after` 或 `Last-Event-ID` 续读，最终状态时结束。服务返回 partial/failed/cancelled 时，
检查 Run.reasons，不要只看 HTTP 202 就宣布研究成功。

## 3. API 浏览、记忆和取消

把下面 `<run_id>` 换成上一步打印的 ID：

```bash
curl --fail-with-body -sS http://127.0.0.1:8000/api/research
curl --fail-with-body -sS 'http://127.0.0.1:8000/api/research/<run_id>/sources'
curl --fail-with-body -sS --get 'http://127.0.0.1:8000/api/research/<run_id>/memory' --data-urlencode 'q=SQLite'
curl --fail-with-body -N 'http://127.0.0.1:8000/api/research/<run_id>/events?after=0'
curl --fail-with-body -sS 'http://127.0.0.1:8000/api/research/<run_id>/trace?format=json'
curl --fail-with-body -sS -X POST 'http://127.0.0.1:8000/api/research/<run_id>/cancel'
```

Offline 通常很快结束，取消已结束任务不会把成功结果改成取消；要验证队列取消和执行中取消，
使用相应自动化测试。已配置入站 `RESEARCH_API_KEY` 时，研究 API 需要 Bearer Header；
上面的无认证命令仅适用于默认本机配置。服务不会因为配置 Key 就自动获得多用户隔离能力。

## 4. 可选真实来源

这一步会访问真实公网 API。退出原服务，在新环境启动：

```bash
RESEARCH_ALLOW_LIVE=true make serve
```

另一个终端只选择 GitHub，避免把其它 Provider 的状态混进这次演示：

```bash
curl --fail-with-body -sS http://127.0.0.1:8000/api/research \
  -H 'Content-Type: application/json' \
  --data '{"topic":"agent memory","mode":"live","providers":["github"],"max_sources":2,"budget":{"max_seconds":30,"max_tool_calls":4,"max_rounds":1,"max_cost_usd":0}}'
```

这可能返回部分结果：来源上限、限流、超时或有子问题未覆盖都是实际约束。GitHub 描述不是
完整项目代码阅读；没有发布日期的仓库元数据不会被伪造日期。一次成功不代表长期可用。
arXiv 需要单独选择 `providers:["arxiv"]`；Brave 需要环境中的 `RESEARCH_WEB_API_KEY`，
不能在缺 Key 时把固定资料标成 Web Search。实际联网记录见 [VALIDATION](VALIDATION.md)。

模型调用默认关闭。若操作者自行配置 OpenAI-compatible/Gateway、明确价格并给正预算，
在 LIVE 模式下可选辅助分析才会执行；不要为演示随意启用付费服务。完整配置及费用语义见
[INTEGRATION](INTEGRATION.md)。

## 5. AgentOps 兼容入口

下面使用内置 Offline Fixture，无真实搜索或模型：

```bash
curl --fail-with-body -sS http://127.0.0.1:8000/api/agentops/research \
  -H 'Content-Type: application/json' \
  --data '{"task":"Agent Memory 的检索与持久化","category":"retrieval","fixtures":{},"seed":42,"config":{"planner":true,"tool_descriptions":"improved","tool_retries":0}}'
```

返回严格的 `{answer,trace}`，可交给项目 5 的 `ResearchAgentAdapter` 校验。
这里只支持内置教学资料和明确支持的配置；外部 fixtures、关闭 Planner 或不支持的工具描述
设置会被拒绝，不会无声忽略后伪装完成消融实验。固定流程不使用随机模型，seed 仅作为
兼容输入，不据此声称真实模型确定性。

## 6. 可选只读 MCP

```bash
uv sync --extra dev --extra mcp --frozen
.venv/bin/python -m researchos.mcp_server
```

这是 stdio MCP server，应由 MCP client 启动并连接，不能把它当成浏览器 HTTP 页面。
它只提供 get_run、list_sources、search_memory，读取已保存的研究，不创建任务或请求外部
服务。需先用页面/API 生成 `.runtime/` 中的 Run；CLI Demo 的 artifacts 数据库与默认
页面数据库分开。若读取其它显式数据库，按实现支持的环境配置设置 `RESEARCH_DATA_DIR`。
此 server 与 Workspace 兄弟项目的文件 MCP 不同，不提供任意文件读写工具。

## 7. 回归与公开产物

```bash
make lint
make test
make eval
```

评测题集、生成到 100 题和人工审核说明见 [eval/README](../eval/README.md)。公开样例只应
来自实际运行并审查；截图必须来自真实 UI。最终 pytest 数、Docker、CI 和对应产物位置见
[VALIDATION](VALIDATION.md)，不要把本说明中的预期动作当成已经通过的结果。


## 8. Docker 与浏览器验收

```bash
docker compose up --build -d
curl --fail http://127.0.0.1:8000/health
docker compose down
```

Compose 使用命名 volume 保存 /data，容器以非 root 运行、只读根文件系统并限制 capabilities。
`down` 默认保留数据 volume；本机 Python 服务占用 8000 时先停止该服务再运行 Compose。

可选浏览器工具：`uv sync --extra dev --extra browser --extra mcp --frozen`，安装所需
Playwright 浏览器后，保持服务运行，执行 `.venv/bin/python scripts/browser_smoke.py`。
也可用 `RESEARCH_BROWSER_EXECUTABLE` 显式选择现有 Chromium。截图仅来自真实操作，
该可选验收工具不是应用运行的依赖。
