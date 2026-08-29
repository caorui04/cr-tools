// 联网文献检索视图（B4：arXiv 开放 API，来源声明 + 可核对）
async function renderLiteratureView() {
  const container = document.getElementById("view-container");
  container.innerHTML = `
    <div class="card">
      <div style="font-weight:600;margin-bottom:8px">🌐 联网文献检索（arXiv）</div>
      <div style="display:flex;gap:6px;margin-bottom:8px">
        <input id="litTopic" class="ipt" placeholder="输入研究主题 / 关键词（英文效果更佳）" style="flex:1" />
        <button class="btn btn-primary" id="litSearchBtn">检索</button>
      </div>
      <div id="litContent"><div style="font-size:12px;color:var(--text-secondary)">检索结果将带来源声明，供文献发现；引用前请回原文核对。</div></div>
    </div>`;
  const doSearch = async () => {
    const topic = document.getElementById("litTopic").value.trim();
    if (!topic) { toast("请输入检索主题", true); return; }
    const btn = document.getElementById("litSearchBtn");
    btn.disabled = true;
    btn.textContent = "检索中...";
    const content = document.getElementById("litContent");
    content.innerHTML = `<div style="text-align:center;padding:16px;color:var(--text-secondary)">正在查询 arXiv 开放 API...</div>`;
    const { data, error } = await api.webLitSearch(topic, 8);
    btn.disabled = false;
    btn.textContent = "检索";
    if (error) {
      content.innerHTML = `<div style="color:var(--danger);font-size:12px">检索失败：${error}</div>`;
      return;
    }
    if (data.ok === false) {
      content.innerHTML = `<div style="color:var(--warning);font-size:12px">${esc(data.message || "检索未开启")}</div>`;
      return;
    }
    const results = data.results || [];
    content.innerHTML = `
      <div class="disclaimer-bar">ℹ️ ${esc(data.disclaimer || "结果来自 arXiv 开放 API，仅供文献发现，引用前请回原文核对。")}</div>
      ${results.length === 0
        ? `<div style="color:var(--text-secondary);font-size:12px">未找到相关文献，换个关键词试试。</div>`
        : results.map(r => `
          <div class="lit-item">
            <div class="lit-title">${esc(r.title)}</div>
            <div class="lit-meta">${esc((r.authors || []).slice(0, 5).join(", "))} · ${esc(r.year || "?")} · <a href="${esc(r.url)}" target="_blank" rel="noopener">原文链接 ↗</a></div>
            ${r.abstract ? `<div class="lit-abstract">${esc(r.abstract)}...</div>` : ""}
          </div>`).join("")}`;
  };
  document.getElementById("litSearchBtn").onclick = doSearch;
  document.getElementById("litTopic").addEventListener("keydown", e => {
    if (e.key === "Enter") doSearch();
  });
  document.getElementById("litTopic").focus();
}
