"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const terminal = new Set(["completed", "partial", "failed", "cancelled"]);
  const statusNames = {queued: "等待中", running: "研究中", completed: "已完成", partial: "部分完成", failed: "执行失败", cancelled: "已停止"};
  const taskNames = {pending: "等待中", searching: "检索中", complete: "已完成", missing: "缺少证据"};
  const categoryNames = {fixture: "教学资料", paper: "论文", github: "GitHub", web: "网页", document: "本地文档"};
  const state = {run: null, history: [], events: new Map(), config: {allow_live: false, auth_required: false}, key: "", selection: 0, poll: null, stream: null, streamRetry: null, reportVersion: "", submitting: false};

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function setNotice(message, kind = "") {
    $("notice").textContent = message || "";
    $("notice").className = `notice ${kind}`.trim();
    $("notice").hidden = !message;
  }

  function formatDate(value, short = false) {
    if (!value) return "未知";
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", short ? {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false} : {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZoneName: "short"}).format(date);
  }

  function formatTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("zh-CN", {hour12: false});
  }

  function safeLink(url) {
    try {
      const parsed = new URL(url);
      return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
    } catch {
      return null;
    }
  }

  function headers(extra = {}) {
    return {...extra, ...(state.key ? {Authorization: `Bearer ${state.key}`} : {})};
  }

  function errorMessage(payload, status) {
    if (Array.isArray(payload?.error?.details)) return payload.error.details.map((item) => `${(item.field || []).slice(1).join(".")}: ${item.message}`).join("\n");
    const detail = payload?.detail ?? payload?.error?.message ?? payload?.error ?? payload?.message;
    if (Array.isArray(detail)) return detail.map((item) => `${(item.loc || []).slice(1).join(".")}: ${item.msg}`).join("\n");
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") return detail.message || JSON.stringify(detail);
    if (status === 401 || status === 403) return "连接需要有效的 API key。请在侧栏“连接设置”中填写。";
    return `请求失败（HTTP ${status}）`;
  }

  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {...options, headers: headers(options.headers), cache: "no-store"});
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new Error("无法连接研究服务，请检查服务是否正在运行。");
    }
    if (!response.ok) {
      let payload = null;
      try { payload = await response.json(); } catch { /* A proxy may return a non-JSON error. */ }
      throw new Error(errorMessage(payload, response.status));
    }
    return response;
  }

  async function json(path, options) {
    const response = await request(path, options);
    return response.json();
  }

  async function loadConfig() {
    try {
      state.config = await json("/api/config");
      const liveOption = $("mode").querySelector('option[value="live"]');
      liveOption.disabled = !state.config.allow_live;
      liveOption.textContent = state.config.allow_live ? "联网检索" : "联网检索 · 未启用";
      if (!state.config.allow_live && $("mode").value === "live") $("mode").value = "offline";
      $("connection-status").textContent = state.config.auth_required ? "API 认证已启用" : "本地服务已连接";
      if (state.config.auth_required && !state.key) {
        $("api-settings").open = true;
        setNotice("此研究服务启用了 API 认证。请在侧栏连接设置中填写 API key。", "warning");
      }
      updateModeDescription();
    } catch (error) {
      $("connection-status").textContent = "服务连接失败";
      setNotice(error.message, "error");
    }
  }

  async function loadHistory(showError = true) {
    try {
      const result = await json("/api/research");
      state.history = Array.isArray(result) ? result : (result.runs || []);
      renderHistory();
    } catch (error) {
      if (showError) setNotice(error.message, "error");
      if (!state.history.length) $("history").replaceChildren(element("p", "sidebar-empty", "连接后可查看研究记录。"));
    }
  }

  function renderHistory() {
    if (!state.history.length) {
      $("history").replaceChildren(element("p", "sidebar-empty", "还没有研究记录。\n从一个问题开始。"));
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const item of state.history) {
      const button = element("button", `history-item${state.run?.id === item.id ? " active" : ""}`);
      button.type = "button";
      button.title = item.topic || item.request?.topic || "研究记录";
      if (state.run?.id === item.id) button.setAttribute("aria-current", "page");
      const body = element("span", "history-item-body");
      body.append(element("span", "history-title", item.topic || item.request?.topic || "未命名研究"));
      const meta = element("span", "history-meta");
      meta.append(element("span", "", formatDate(item.created_at, true)), element("span", "", statusNames[item.status] || item.status));
      body.append(meta);
      button.append(element("span", `history-dot ${item.status}`), body);
      button.addEventListener("click", () => selectRun(item.id));
      fragment.append(button);
    }
    $("history").replaceChildren(fragment);
  }

  function stopUpdates() {
    clearTimeout(state.poll);
    clearTimeout(state.streamRetry);
    state.poll = null;
    state.streamRetry = null;
    state.stream?.abort();
    state.stream = null;
  }

  function showComposer() {
    stopUpdates();
    state.selection += 1;
    state.run = null;
    state.events.clear();
    state.reportVersion = "";
    history.replaceState(null, "", location.pathname);
    $("composer").hidden = false;
    $("welcome").hidden = false;
    $("run-workspace").hidden = true;
    $("run-actions").hidden = true;
    $("page-eyebrow").textContent = "DOCUMENT RESEARCH";
    $("page-title").replaceChildren(document.createTextNode("研究工作台"), element("span", "heading-period", "."));
    $("page-subtitle").textContent = "对照招生简章与培养方案，保留年级、版本和原文位置。";
    $("breadcrumb-page").textContent = "新建研究";
    document.title = "ResearchOS · 研究工作台";
    setNotice("");
    renderHistory();
    $("topic").focus();
    window.scrollTo({top: 0, behavior: "smooth"});
  }

  async function selectRun(id, initial = null) {
    stopUpdates();
    const selection = ++state.selection;
    state.events.clear();
    state.reportVersion = "";
    state.run = null;
    $("trace-body").replaceChildren();
    $("trace-empty").hidden = false;
    $("event-count").textContent = "0";
    $("report-frame").hidden = true;
    $("report-frame").removeAttribute("srcdoc");
    $("memory-results").replaceChildren(element("p", "muted", "输入关键词，检索本次研究中已保存的证据。"));
    setNotice("");
    try {
      const run = initial || await json(`/api/research/${encodeURIComponent(id)}`);
      if (selection !== state.selection) return;
      state.run = run;
      history.replaceState(null, "", `${location.pathname}?run=${encodeURIComponent(run.id)}`);
      $("composer").hidden = true;
      $("welcome").hidden = true;
      $("run-workspace").hidden = false;
      $("run-actions").hidden = false;
      renderRun();
      renderHistory();
      openStream(selection);
      if (!terminal.has(run.status)) schedulePoll(selection);
    } catch (error) {
      if (selection === state.selection) setNotice(error.message, "error");
    }
  }

  function schedulePoll(selection, delay = 1300) {
    clearTimeout(state.poll);
    state.poll = setTimeout(async () => {
      if (selection !== state.selection || !state.run) return;
      try {
        const run = await json(`/api/research/${encodeURIComponent(state.run.id)}`);
        if (selection !== state.selection) return;
        const wasTerminal = terminal.has(state.run.status);
        state.run = run;
        renderRun();
        if (terminal.has(run.status)) {
          if (!wasTerminal) loadHistory(false);
        } else schedulePoll(selection);
      } catch (error) {
        if (selection !== state.selection) return;
        $("stream-state").textContent = "连接中断，正在重试";
        setNotice(error.message, "warning");
        schedulePoll(selection, 3500);
      }
    }, delay);
  }

  async function refreshRun(selection) {
    if (!state.run || selection !== state.selection) return;
    try {
      const run = await json(`/api/research/${encodeURIComponent(state.run.id)}`);
      if (selection !== state.selection) return;
      state.run = run;
      renderRun();
      if (terminal.has(run.status)) {
        clearTimeout(state.poll);
        loadHistory(false);
      }
    } catch (error) {
      if (selection === state.selection) setNotice(error.message, "warning");
    }
  }

  function parseEvent(block, selection) {
    const lines = block.split(/\r?\n/);
    const data = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
    if (!data || selection !== state.selection) return;
    let event;
    try { event = JSON.parse(data); } catch { return; }
    if (!Number.isInteger(event.seq) || !event.kind || state.events.has(event.seq)) return;
    state.events.set(event.seq, event);
    renderEvent(event);
    $("event-count").textContent = String(state.events.size);
    $("trace-empty").hidden = true;
    if (["finished", "cancelled", "failed"].includes(event.kind)) refreshRun(selection);
  }

  async function openStream(selection) {
    if (!state.run || selection !== state.selection) return;
    state.stream?.abort();
    const controller = new AbortController();
    state.stream = controller;
    const after = state.events.size ? Math.max(...state.events.keys()) : 0;
    $("stream-state").textContent = terminal.has(state.run.status) ? "读取保存的事件" : "● 实时事件已连接";
    try {
      const response = await request(`/api/research/${encodeURIComponent(state.run.id)}/events?after=${after}`, {signal: controller.signal, headers: {Accept: "text/event-stream", ...(after ? {"Last-Event-ID": String(after)} : {})}});
      if (!response.body) throw new Error("此浏览器不支持事件流。");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (selection === state.selection) {
        const {value, done} = await reader.read();
        buffer += decoder.decode(value, {stream: !done});
        let separator;
        while ((separator = /\r?\n\r?\n/.exec(buffer))) {
          parseEvent(buffer.slice(0, separator.index), selection);
          buffer = buffer.slice(separator.index + separator[0].length);
        }
        if (done) {
          if (buffer.trim()) parseEvent(buffer, selection);
          break;
        }
      }
      if (selection !== state.selection) return;
      $("stream-state").textContent = terminal.has(state.run.status) ? "全部事件已保存" : "事件流重连中";
      if (!terminal.has(state.run.status)) state.streamRetry = setTimeout(() => openStream(selection), 1800);
    } catch (error) {
      if (error.name === "AbortError" || selection !== state.selection) return;
      $("stream-state").textContent = "事件流暂不可用，状态自动刷新";
      if (!terminal.has(state.run.status)) state.streamRetry = setTimeout(() => openStream(selection), 4000);
    }
  }

  function renderRun() {
    const run = state.run;
    if (!run) return;
    $("page-eyebrow").textContent = "RESEARCH WORKSPACE";
    $("page-title").textContent = run.request.topic;
    $("page-subtitle").textContent = run.request.corpus === "campus" ? `资料对照 · ${run.request.campus || "按题目匹配校区"} · ${run.request.document_year ? `${run.request.document_year}年文档` : "已收录历史版本"} · 最多 ${run.request.max_sources} 个来源` : `${run.request.depth === "deep" ? "深入研究" : run.request.depth === "quick" ? "快速概览" : "标准研究"} · ${run.request.time_range || "时间范围不限"} · 最多 ${run.request.max_sources} 个来源`;
    $("breadcrumb-page").textContent = "研究详情";
    document.title = `${run.request.topic.slice(0, 40)} · ResearchOS`;
    $("run-mode").textContent = run.request.mode === "live" ? "LIVE / 真实联网" : run.request.corpus === "campus" ? "本地历史资料" : "DEMO / OFFLINE";
    $("run-mode").className = `badge ${run.request.mode === "live" ? "badge-live" : "badge-offline"}`;
    $("cancel-run").hidden = terminal.has(run.status);
    $("cancel-run").disabled = false;
    $("run-status").textContent = statusNames[run.status] || run.status;
    $("run-status").className = `status-pill ${run.status}`;
    $("run-id").textContent = `RUN ${run.id.slice(0, 10)}`;
    $("run-id").title = run.id;
    $("run-timestamp").textContent = `创建于 ${formatDate(run.created_at)}`;
    $("run-duration").textContent = run.config?.latency_ms != null ? `执行耗时 ${(run.config.latency_ms / 1000).toFixed(2)} 秒` : "有限预算执行中";
    renderPlan(run);
    renderSources(run.sources || []);
    renderChecks(run);
    renderUsage(run);
    renderReasons(run.reasons || []);
    renderCitations(run);
    const hasReport = Boolean(run.report_md);
    $("download-md").disabled = !hasReport;
    $("download-html").disabled = !hasReport;
    $("report-empty").hidden = hasReport;
    $("report-hint").textContent = run.request.corpus === "campus" ? "山东大学（威海）历史资料。文档版号与适用年级分别记录；引用可查看原文位置。" : run.request.mode === "offline" ? "DEMO / OFFLINE：固定教学资料，非实时互联网调研。" : "LIVE：结论仅限已采集证据支持的范围。";
    if (hasReport && state.reportVersion !== run.report_md) loadReport(run);
    if (!hasReport && terminal.has(run.status)) {
      $("report-empty").querySelector("h3").textContent = "本次研究未生成报告";
      $("report-empty").querySelector("p").textContent = "请查看执行原因与 Trace，调整配置后重新研究。";
    } else {
      $("report-empty").querySelector("h3").textContent = "报告准备中";
      $("report-empty").querySelector("p").textContent = "来源采集和引用检查完成后，报告会显示在这里。";
    }
  }

  function renderPlan(run) {
    const tasks = run.plan?.subtasks || [];
    const complete = tasks.filter((task) => task.status === "complete").length;
    $("plan-progress").textContent = tasks.length ? `${complete} / ${tasks.length}` : "";
    if (!tasks.length) {
      $("plan-cards").replaceChildren(element("p", "muted", terminal.has(run.status) ? "本次研究未生成计划。" : "正在生成研究计划…"));
      return;
    }
    const fragment = document.createDocumentFragment();
    tasks.forEach((task, index) => {
      const card = element("article", `plan-card ${task.status}`);
      const top = element("div", "plan-card-top");
      top.append(element("span", "plan-index", `${String(index + 1).padStart(2, "0")} / ${task.id}`), element("span", "plan-status", `${task.status === "complete" ? "✓ " : ""}${taskNames[task.status] || task.status}`));
      let question = task.question;
      if (question.startsWith(run.request.topic)) question = question.slice(run.request.topic.length).replace(/^\s*[:：]\s*/, "");
      card.append(top, element("h3", "", question));
      const keywords = element("p", "plan-keywords", (task.keywords || []).join(" · "));
      keywords.title = task.evidence_type || keywords.textContent;
      card.append(keywords);
      fragment.append(card);
    });
    $("plan-cards").replaceChildren(fragment);
  }

  function sourceButton(source, claims = state.run?.claims || []) {
    const button = element("button", "source-item");
    button.type = "button";
    button.setAttribute("aria-label", `查看来源：${source.title}`);
    const top = element("div", "source-item-top");
    top.append(element("span", "source-item-title", source.title), element("span", "source-type", categoryNames[source.category] || source.category));
    const meta = element("div", "source-item-meta");
    const citations = claims.filter((claim) => claim.source_id === source.id).map((claim) => `[${claim.citation_id}]`).join(" ");
    meta.append(element("span", "source-citation", citations || "已保存来源"), element("span", "", `${source.provenance === "local" ? "本地历史资料" : source.provenance === "fixture" ? "DEMO" : "NETWORK"} · ${formatDate(source.fetched_at, true)}`));
    const relevant = source.document ? source.excerpt.split("\n").filter((line) => /招生计划人数|面向|173学分|179学分|弹性修业年限/.test(line)).slice(0, 2).join("\n") : "";
    button.append(top, element("span", "source-item-excerpt", relevant || source.excerpt), meta);
    button.addEventListener("click", () => showSource(source));
    return button;
  }

  function renderSources(sources) {
    $("source-count").textContent = String(sources.length);
    if (!sources.length) {
      const empty = element("div", "empty-state");
      empty.append(element("span", "empty-icon", "⌕"), element("h3", "", terminal.has(state.run.status) ? "暂无可用证据" : "正在收集来源"), element("p", "", terminal.has(state.run.status) ? "查看执行记录与具体原因，调整范围或来源后重试。" : "检索结果将保存在本次研究中。"));
      $("sources-list").replaceChildren(empty);
      return;
    }
    $("sources-list").replaceChildren(...sources.map((source) => sourceButton(source)));
  }

  function renderChecks(run) {
    const checks = run.checks;
    $("check-symbol").className = `check-symbol${checks ? checks.passed ? " passed" : " failed" : ""}`;
    $("check-symbol").textContent = checks ? checks.passed ? "✓" : "!" : "—";
    if (!checks) {
      $("checks-content").replaceChildren(element("p", "muted", "报告生成后运行确定性检查。"));
      return;
    }
    const fragment = document.createDocumentFragment();
    fragment.append(element("p", `check-result-title${checks.passed ? "" : " failed"}`, checks.passed ? "结构核验通过" : "引用需要复核"));
    if (checks.passed) {
      for (const text of ["来源与原文摘录绑定", "引用编号与参考文献一致", "来源类型与时间信息检查"]) {
        const row = element("div", "check-line");
        row.append(element("span", "", "✓"), element("span", "", text));
        fragment.append(row);
      }
    }
    for (const error of checks.errors || []) {
      const row = element("div", "check-line error");
      row.append(element("span", "", "!"), element("span", "", error));
      fragment.append(row);
    }
    $("checks-content").replaceChildren(fragment);
    $("checks-content").title = (checks.warnings || []).join("\n");
  }

  function renderUsage(run) {
    const tokens = run.usage?.tokens;
    const cost = run.usage?.cost_usd;
    const rows = [
      ["来源 / 证据绑定", `${run.sources?.length || 0} / ${run.claims?.length || 0}`],
      ["工具调用", `${run.config?.tool_calls ?? 0} / ${run.request.budget.max_tool_calls}`],
      ["研究轮次", `${run.config?.rounds ?? 0} / ${run.request.budget.max_rounds}`],
      ["LLM Tokens", tokens == null ? (run.request.mode === "offline" ? "未调用" : "未记录") : typeof tokens === "object" ? JSON.stringify(tokens) : String(tokens)],
      ["LLM 费用", cost == null ? (run.request.mode === "offline" ? "未调用" : "未记录") : `$${Number(cost).toFixed(5)}`],
    ];
    $("usage-list").replaceChildren(...rows.map(([label, value]) => {
      const row = element("div");
      row.append(element("dt", "", label), element("dd", "", value));
      return row;
    }));
  }

  function renderReasons(reasons) {
    $("run-reasons").hidden = !reasons.length;
    if (!reasons.length) return;
    const list = element("ul");
    for (const reason of reasons) list.append(element("li", "", reason));
    $("run-reasons").replaceChildren(element("strong", "", "执行说明与待完成事项"), list);
  }

  function renderCitations(run) {
    const claims = run.claims || [];
    if (!claims.length) {
      $("citation-links").replaceChildren();
      return;
    }
    const fragment = document.createDocumentFragment();
    fragment.append(element("span", "", "回查引用"));
    for (const claim of claims) {
      const source = run.sources.find((item) => item.id === claim.source_id);
      if (!source) continue;
      const button = element("button", "citation-chip", `[${claim.citation_id}]`);
      button.type = "button";
      button.title = source.title;
      button.setAttribute("aria-label", `引用 ${claim.citation_id}：${source.title}`);
      button.addEventListener("click", () => showSource(source));
      fragment.append(button);
    }
    $("citation-links").replaceChildren(fragment);
  }

  async function loadReport(run) {
    const selection = state.selection;
    const version = run.report_md;
    state.reportVersion = version;
    try {
      const response = await request(`/api/research/${encodeURIComponent(run.id)}/report?format=html`);
      const html = await response.text();
      if (selection !== state.selection || state.reportVersion !== version) return;
      $("report-frame").srcdoc = html;
      $("report-frame").hidden = false;
      $("report-empty").hidden = true;
    } catch (error) {
      if (selection !== state.selection) return;
      state.reportVersion = "";
      $("report-empty").hidden = false;
      $("report-empty").querySelector("h3").textContent = "报告预览暂不可用";
      $("report-empty").querySelector("p").textContent = error.message;
    }
  }

  function showSource(source) {
    const body = document.createDocumentFragment();
    body.append(element("h2", "", source.title));
    body.firstChild.id = "source-dialog-title";
    const link = safeLink(source.url);
    if (link) {
      const anchor = element("a", "source-url", source.url);
      anchor.href = link;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      body.append(anchor);
    } else body.append(element("p", "source-url mono", source.url));
    const facts = element("dl", "source-facts");
    const data = [["来源类型", `${categoryNames[source.category] || source.category} / ${source.provenance === "local" ? "本地历史资料" : source.provenance === "fixture" ? "DEMO · 教学资料" : "NETWORK"}`], ["采集时间", formatDate(source.fetched_at)], ["发表时间", source.published_at ? formatDate(source.published_at) : "未知 · 不推定为时间范围内证据"], ["日期依据", source.date_basis || "未提供"], ["关联子问题", (source.subtask_ids || []).join("、") || "—"], ["作者 / 维护者", (source.authors || []).join("、") || "未提供"]];
    if (source.doi) data.push(["DOI", source.doi]);
    if (source.document) data.push(["原文件", source.document.source_path], ["校区 / 文档年度", `${source.document.campus} / ${source.document.document_year}`], ["明确适用年级", source.document.cohort ? `${source.document.cohort}级` : "原文未明确；版号不作为年级依据"], ["原文段落", source.document.paragraph_numbers.join("、")], ["源文件 SHA-256", source.document.source_sha256]);
    for (const [label, value] of data) {
      const row = element("div");
      row.append(element("dt", "", label), element("dd", "", value));
      facts.append(row);
    }
    body.append(facts, element("h3", "source-detail-section", "SUPPORTING PASSAGE / 保存的来源摘录"), element("blockquote", "source-passage", source.excerpt));
    const claims = (state.run?.claims || []).filter((claim) => claim.source_id === source.id);
    if (claims.length) {
      body.append(element("h3", "source-detail-section", "CLAIM BINDINGS / 报告中的引用"));
      for (const claim of claims) {
        const item = element("div", "source-claim");
        item.append(element("strong", "", `[${claim.citation_id}] · ${claim.subtask_id}`), element("p", "", claim.text));
        body.append(item);
      }
    }
    body.append(element("p", "source-hash mono", `SOURCE ${source.id}\nSHA-256 ${source.content_hash || "未记录"}`), element("p", "source-dialog-note", source.provenance === "local" ? "摘录来自本地 RAG 知识库中的历史文档，段落号包含 DOCX 空段和表格段。源文件哈希用于持有原库时核对；本次只校验打包摘录。" : source.provenance === "fixture" ? "这是项目自有的固定教学资料，用于离线回归和流程演示。fixture:// 地址不会访问互联网。" : "保存的摘录与采集时间属于此次研究快照。外部页面内容可能在之后发生变化。"));
    $("source-detail").replaceChildren(body);
    if (!$("source-dialog").open) $("source-dialog").showModal();
  }

  function eventSummary(event) {
    const p = event.payload || {};
    if (event.kind === "span_end") return `${p.name || "执行阶段"} · ${p.status === "error" ? "异常结束" : "完成"}`;
    if (event.kind === "span_start") return `${p.name || "执行阶段"} · 开始`;
    if (event.kind === "plan") return `研究计划已生成 · ${p.plan?.subtasks?.length ?? "—"} 个子问题`;
    if (event.kind === "subtask") return `${p.subtask?.id || ""} · ${taskNames[p.subtask?.status] || p.subtask?.status || ""}`;
    if (event.kind === "tool_call") return `${p.tool || "工具调用"} · ${p.subtask_id || ""}`;
    if (event.kind === "tool_result") return `${p.tool || "工具结果"} · ${p.error || `${p.candidates ?? 0} 条候选来源`} · ${p.status || ""}`;
    if (event.kind === "review") return `第 ${p.round || 1} 轮复核 · ${p.checks?.passed ? "结构检查通过" : "需要补充或复核"}`;
    if (event.kind === "deduplicated") return `合并重复来源 · ${String(p.duplicate_of || "").slice(0, 12)}`;
    if (event.kind === "finished") return statusNames[p.status] || p.status || "研究结束";
    if (p.error || p.message || p.reason) return String(p.error || p.message || p.reason);
    if (p.name || p.status) return [p.name, p.status].filter(Boolean).join(" · ");
    return "事件已保存";
  }

  function renderEvent(event) {
    const payload = event.payload || {};
    const row = element("tr");
    const time = element("td", "", formatTime(event.at));
    time.title = formatDate(event.at);
    const kind = element("td");
    kind.append(element("span", `event-kind${payload.status === "error" || event.kind.includes("fail") ? " error" : ""}`, event.kind));
    if (payload.role) kind.append(element("div", "trace-role", payload.role));
    const detail = element("td", "trace-detail");
    detail.append(element("p", "", eventSummary(event)));
    const more = element("details");
    more.append(element("summary", "", `事件 #${event.seq} · 查看数据`), element("pre", "", JSON.stringify(payload, null, 2)));
    detail.append(more);
    row.append(time, kind, detail, element("td", "mono", payload.duration_ms != null ? `${Number(payload.duration_ms).toFixed(1)} ms` : "—"));
    $("trace-body").append(row);
  }

  function updateModeDescription() {
    const live = $("mode").value === "live";
    $("corpus").disabled = live;
    const campus = !live && $("corpus").value === "campus";
    $("campus-filters").hidden = !campus;
    $("case-shortcuts").hidden = !campus;
    for (const id of ["generic-depth", "generic-date", "generic-language"]) {
      $(id).hidden = campus;
      $(id).querySelector("input, select").disabled = campus;
    }
    $("corpus-note").textContent = campus ? "山东大学（威海）历史资料 · 2023—2024" : live ? "保存实际采集的来源与摘录" : "Agent Memory 教学资料 · 流程回归";
    for (const input of $("campus-filters").querySelectorAll("input, select")) input.disabled = !campus;
    $("mode-description").replaceChildren(element("span", "small-dot"), document.createTextNode(live ? "实际请求所选来源；缺失密钥、限流与失败将明确记录。" : campus ? "本地历史资料，无需密钥。查看原文后再判断适用范围。" : "离线教学资料演示，无需密钥，不请求外网。"));
    document.querySelectorAll('input[name="provider"]').forEach((input) => { input.disabled = !live; });
  }

  async function submitResearch(event) {
    event.preventDefault();
    if (state.submitting) return;
    const form = $("research-form");
    if (!form.reportValidity()) return;
    const topic = $("topic").value.trim();
    if (topic.length < 3) { setNotice("请输入至少 3 个非空白字符的研究主题。", "warning"); return; }
    const campus = $("mode").value === "offline" && $("corpus").value === "campus";
    const range = campus ? "" : $("time-range").value.trim();
    if (range) {
      const [start, end] = range.split("-").map(Number);
      if (start < 1900 || end > 2100 || start > end) { setNotice("时间范围须为 1900–2100 内递增年份，例如 2020-2026。", "warning"); return; }
    }
    const providers = Array.from(document.querySelectorAll('input[name="provider"]:checked'), (input) => input.value);
    if ($("mode").value === "live" && !providers.length) { setNotice("请至少选择一个联网来源。", "warning"); return; }
    const payload = {topic, corpus: campus ? "campus" : "teaching", campus: campus ? $("campus").value : null, document_year: campus && $("document-year").value ? Number($("document-year").value) : null, cohort: campus && $("cohort").value ? Number($("cohort").value) : null, time_range: range || null, max_sources: Number($("max-sources").value), depth: campus ? "standard" : $("depth").value, language: campus ? "zh" : $("language").value, mode: $("mode").value, providers: $("mode").value === "live" ? providers : ["github", "arxiv"], budget: {max_seconds: Number($("max-seconds").value), max_tool_calls: Number($("max-tool-calls").value), max_rounds: Number($("max-rounds").value), max_cost_usd: Number($("max-cost").value)}};
    state.submitting = true;
    $("submit-research").disabled = true;
    $("submit-research").textContent = "正在创建…";
    setNotice("");
    try {
      const run = await json("/api/research", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
      await selectRun(run.id, run);
      loadHistory(false);
      activateTab("overview");
    } catch (error) {
      setNotice(error.message, "error");
    } finally {
      state.submitting = false;
      $("submit-research").disabled = false;
      $("submit-research").replaceChildren(document.createTextNode("开始研究 "), element("span", "", "↗"));
    }
  }

  function activateTab(name, moveFocus = false) {
    document.querySelectorAll(".tab").forEach((tab) => {
      const active = tab.dataset.panel === name;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      $(`panel-${tab.dataset.panel}`).hidden = !active;
      if (active && moveFocus) tab.focus();
    });
    if (name === "report" && !$("report-frame").hidden) sizeReport();
  }

  function saveBlob(blob, filename) {
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }

  async function downloadReport(format) {
    if (!state.run) return;
    const runId = state.run.id;
    try {
      const response = await request(`/api/research/${encodeURIComponent(runId)}/report?format=${format}`);
      saveBlob(await response.blob(), `researchos-${runId.slice(0, 10)}.${format === "md" ? "md" : "html"}`);
    } catch (error) { setNotice(error.message, "error"); }
  }

  function sizeReport() {
    try {
      const doc = $("report-frame").contentDocument;
      if (doc?.body) $("report-frame").style.height = `${Math.max(700, Math.min(4500, doc.documentElement.scrollHeight + 35))}px`;
    } catch { /* The sandbox prevents access if the browser changes origin policy. */ }
  }

  function bindReport() {
    try {
      const doc = $("report-frame").contentDocument;
      if (!doc) return;
      doc.addEventListener("click", (event) => {
        const anchor = event.target.closest?.("a");
        if (!anchor) return;
        const match = (anchor.getAttribute("href") || "").match(/^#ref-(\d+)$/);
        if (match) {
          const claim = state.run?.claims?.find((item) => item.citation_id === Number(match[1]));
          const source = state.run?.sources?.find((item) => item.id === claim?.source_id);
          if (source) { event.preventDefault(); showSource(source); }
        } else {
          const url = safeLink(anchor.getAttribute("href"));
          if (url) {
            event.preventDefault();
            window.open(url, "_blank", "noopener,noreferrer");
          }
        }
      });
      sizeReport();
    } catch { /* Citation chips outside the preview remain available. */ }
  }

  async function searchMemory(event) {
    event.preventDefault();
    if (!state.run) return;
    const query = $("memory-query").value.trim();
    if (!query) return;
    const selection = state.selection;
    const button = $("memory-form").querySelector("button");
    button.disabled = true;
    $("memory-results").replaceChildren(element("p", "muted", "正在检索已保存证据…"));
    try {
      const result = await json(`/api/research/${encodeURIComponent(state.run.id)}/memory?q=${encodeURIComponent(query)}`);
      if (selection !== state.selection) return;
      const sources = Array.isArray(result) ? result : result.sources || result.results || [];
      if (!sources.length) $("memory-results").replaceChildren(element("p", "muted", "未找到匹配证据。可以缩短关键词或尝试其它词。"));
      else $("memory-results").replaceChildren(element("p", "memory-count", `找到 ${sources.length} 条证据`), ...sources.map((source) => sourceButton(source)));
    } catch (error) {
      if (selection === state.selection) $("memory-results").replaceChildren(element("p", "muted", error.message));
    } finally { button.disabled = false; }
  }

  async function cancelRun() {
    if (!state.run || terminal.has(state.run.status)) return;
    const selection = state.selection;
    $("cancel-run").disabled = true;
    try {
      await json(`/api/research/${encodeURIComponent(state.run.id)}/cancel`, {method: "POST"});
      await refreshRun(selection);
      if (selection === state.selection && !terminal.has(state.run.status)) schedulePoll(selection, 400);
    } catch (error) {
      if (selection === state.selection) { setNotice(error.message, "error"); $("cancel-run").disabled = false; }
    }
  }

  async function saveKey() {
    state.key = $("api-key").value.trim();
    try {
      if (state.key) sessionStorage.setItem("researchos_api_key", state.key);
      else sessionStorage.removeItem("researchos_api_key");
    } catch { /* Storage may be unavailable in private browsing. */ }
    setNotice("");
    await loadConfig();
    await loadHistory();
    if (state.run) selectRun(state.run.id);
  }

  function bindUI() {
    $("new-research").addEventListener("click", showComposer);
    $("refresh-history").addEventListener("click", () => loadHistory());
    $("save-key").addEventListener("click", saveKey);
    $("api-key").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); saveKey(); } });
    $("research-form").addEventListener("submit", submitResearch);
    $("topic").addEventListener("input", () => { $("topic-count").textContent = `${$("topic").value.length} / 500`; });
    $("topic-count").textContent = `${$("topic").value.length} / 500`;
    $("mode").addEventListener("change", updateModeDescription);
    $("corpus").addEventListener("change", updateModeDescription);
    document.querySelectorAll("[data-example]").forEach((button) => button.addEventListener("click", () => {
      $("mode").value = "offline";
      $("corpus").value = "campus";
      $("campus").value = "威海";
      $("document-year").value = "2024";
      $("cohort").value = "";
      $("time-range").value = "";
      $("topic").value = button.dataset.example === "curriculum" ? "比较计算机科学与技术和人工智能专业培养方案的毕业学分与修业年限" : "比较智能导航、数据科学和计算法学实验班的招生条件、名额与选拔方式";
      $("topic").dispatchEvent(new Event("input"));
      updateModeDescription();
    }));
    $("cancel-run").addEventListener("click", cancelRun);
    $("download-md").addEventListener("click", () => downloadReport("md"));
    $("download-html").addEventListener("click", () => downloadReport("html"));
    $("download-trace").addEventListener("click", () => {
      if (!state.run) return;
      const trace = {schema: "researchos.events.v1", run_id: state.run.id, status: state.run.status, config: state.run.config, usage: state.run.usage, events: [...state.events.values()].sort((a, b) => a.seq - b.seq)};
      saveBlob(new Blob([JSON.stringify(trace, null, 2)], {type: "application/json"}), `researchos-trace-${state.run.id.slice(0, 10)}.json`);
    });
    $("memory-form").addEventListener("submit", searchMemory);
    $("report-frame").addEventListener("load", bindReport);
    $("close-source").addEventListener("click", () => $("source-dialog").close());
    $("source-dialog").addEventListener("click", (event) => { if (event.target === $("source-dialog")) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close(); } });
    const tabs = [...document.querySelectorAll(".tab")];
    tabs.forEach((tab, index) => {
      tab.tabIndex = index === 0 ? 0 : -1;
      tab.addEventListener("click", () => activateTab(tab.dataset.panel));
      tab.addEventListener("keydown", (event) => {
        let next = null;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = tabs.length - 1;
        if (next !== null) { event.preventDefault(); activateTab(tabs[next].dataset.panel, true); }
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key.toLowerCase() === "n" && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.matches("input,textarea,select,[contenteditable=true]") && !$("source-dialog").open) { event.preventDefault(); showComposer(); }
    });
    window.addEventListener("pagehide", stopUpdates);
    window.addEventListener("resize", sizeReport);
  }

  async function init() {
    try { state.key = sessionStorage.getItem("researchos_api_key") || ""; } catch { /* Session storage is optional. */ }
    $("api-key").value = state.key;
    bindUI();
    updateModeDescription();
    await loadConfig();
    await loadHistory(false);
    const runId = new URLSearchParams(location.search).get("run");
    if (runId) await selectRun(runId);
  }

  init().catch((error) => setNotice(error.message, "error"));
})();
