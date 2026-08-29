// 文档库页面
async function renderDocsView() {
  const container = document.getElementById("view-container");
  container.innerHTML = `<div id="docsContent"><div style="text-align:center;padding:20px;color:var(--text-secondary)">加载中...</div></div>`;
  await loadDocs(0);
}

async function loadDocs(offset) {
  const { data, error } = await api.listDocs(offset, 20, "");
  const content = document.getElementById("docsContent");
  if (error) {
    content.innerHTML = `<div style="color:var(--danger);text-align:center;padding:20px">加载失败: ${esc(error)}</div>`;
    return;
  }
  const docs = data || [];
  content.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <h3>文档库 (${docs.length})</h3>
    </div>
    ${docs.length === 0 ? '<div style="text-align:center;color:var(--text-secondary);padding:20px">暂无文档</div>' : docs.map(d => renderDocCard(d)).join("")}
    <div style="display:flex;gap:8px;justify-content:center;margin-top:10px">
      <button class="btn btn-sm" ${offset === 0 ? "disabled" : ""} onclick="loadDocs(${Math.max(0,offset-20)})">上一页</button>
      <button class="btn btn-sm" onclick="loadDocs(${offset+20})">下一页</button>
    </div>
  `;
}
