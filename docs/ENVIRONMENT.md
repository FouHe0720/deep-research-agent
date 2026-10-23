# 运行环境

核心应用需要Python 3.11+、uv和SQLite FTS5。依赖通过仓库锁文件安装：

```bash
uv sync --extra dev --extra mcp --frozen
```

本地回归使用Linux / WSL和Python 3.12.15。CI配置包含Python 3.11、3.12，以及独立Docker构建与健康检查。具体运行记录见 [VALIDATION](VALIDATION.md)。

网页由FastAPI直接提供，不需要Node构建。`node --check src/researchos/static/app.js` 是可选的JavaScript语法检查。

浏览器回归另需Playwright、Chromium及对应系统库；截图包含中文时需要可用中文字体：

```bash
uv sync --extra dev --extra mcp --extra browser --frozen
uv run playwright install chromium
.venv/bin/python scripts/browser_smoke.py http://127.0.0.1:8000
```

离线校园和教学资料均不需要API Key、GPU或模型下载。联网搜索和可选模型的配置见 [INTEGRATION](INTEGRATION.md)。
