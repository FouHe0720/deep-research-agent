# 技术决策

- **Python 3.11+ + FastAPI + 原生 JS**：页面和 API 同进程交付，无需 Node 构建或 GPU。实际开发使用 uv 管理的 Python 3.12；系统 Python 缺少 ensurepip，所以用现有 uv 建立项目级 .venv，没有安装系统包。
- **SQLite / FTS5**：来源、摘录、运行快照及事件持久化，适合本机和单进程演示。没有借用兄弟仓库数据库。Postgres 迁移需要实现 Store 接口并单独管理事件序列与 FTS 索引，不声称切换一个 URL 就可用。
- **显式角色与有限状态**：先完成 V0 单工作流，再在同一个进程加入角色契约；不引入大型 Agent 框架和无法控制的递归 handoff。
- **保守抽取式写作**：默认逐条保留原始摘录，提供可审计的引用绑定；教学数据不能变成论文结论。没有模型也可跑通。自动结构校验不等于事实正确、语义蕴含或检索质量。
- **Fixtures 的范围**：自编 Agent Memory 教学材料，MIT 授权，无真实网页伪装。其它主题可测程序行为，但不能拿结果作为主题研究；评测必须单列这个边界。
- **配置与凭证**：只有操作者配置的服务地址可以访问内网；用户/搜索结果给出的 URL 走安全公网抓取路径。密钥不写入配置快照和 Trace。

## 外部协议依据

实现时核对的官方资料：
- [HTTPX transports](https://www.python-httpx.org/advanced/transports/) 与 [HTTPCore SNI extension](https://www.encode.io/httpcore/extensions/#sni_hostname)：为公网抓取绑定已验证 IP，保持原 Host/TLS 验证。
- [arXiv API terms](https://info.arxiv.org/help/api/tou.html)：单连接，至少 3 秒间隔；只取元数据/摘要，不批量下载 PDF。
- [GitHub rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)：显式处理限流，不绕过额度。
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)：兼容接口与项目 1 的 /v1/chat 分开处理；不固定模型或私人 URL。
