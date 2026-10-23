# 技术决策

- **Python 3.11+ + FastAPI + 原生 JS**：页面和API由同一进程提供，无需Node构建或GPU。uv管理依赖及锁文件，测试同时覆盖Python 3.11和3.12。
- **SQLite / FTS5**：来源、摘录、运行快照及事件持久化，适合本机单进程运行。迁移Postgres需要实现Store接口，并单独管理事件序列和全文索引。
- **显式角色与有限状态**：角色在同一进程内通过显式契约协作，由Engine统一管理预算和停止条件。
- **保守抽取式写作**：默认逐条保留原始摘录，提供可审计的引用绑定；教学数据不能变成论文结论。没有模型也可跑通。自动结构校验不等于事实正确、语义蕴含或检索质量。
- **Fixtures 的范围**：自编Agent Memory教学材料采用MIT授权，仅用于流程回归。校园历史摘录独立存储，并保留原作者权利与原文定位。
- **配置与凭证**：只有操作者配置的服务地址可以访问内网；用户/搜索结果给出的 URL 走安全公网抓取路径。密钥不写入配置快照和 Trace。

## 外部协议依据

实现时核对的官方资料：
- [HTTPX transports](https://www.python-httpx.org/advanced/transports/) 与 [HTTPCore SNI extension](https://www.encode.io/httpcore/extensions/#sni_hostname)：为公网抓取绑定已验证 IP，保持原 Host/TLS 验证。
- [arXiv API terms](https://info.arxiv.org/help/api/tou.html)：单连接，至少 3 秒间隔；只取元数据/摘要，不批量下载 PDF。
- [GitHub rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)：显式处理限流，不绕过额度。
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)：兼容接口与Gateway的 /v1/chat 分开处理；不固定模型或私人 URL。
