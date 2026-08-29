// 异常管理（仅显示计数 + 重试按钮，按组装方裁定）
async function renderErrorsView() {
  const container = document.getElementById("view-container");
  container.innerHTML = `<div id="errorsContent"><div style="text-align:center;padding:20px">加载中...</div></div>`;
  await refreshErrors();
}

async function refreshErrors() {
  const { data, error } = await api.getStatus();
  const content = document.getElementById("errorsContent");
  if (!content) return;

  const errorCount = data?.queues?.["99_异常"] || 0;
  content.innerHTML = `
    <div class="card">
      <h3 style="margin-bottom:8px">异常管理</h3>
      <div style="font-size:14px;margin-bottom:8px">
        异常文件: <strong style="color:${errorCount > 0 ? 'var(--danger)' : 'var(--success)'}">${errorCount}</strong>
      </div>
      ${errorCount > 0 ? `
        <button class="btn btn-warning btn-sm" id="retryAllBtn">全部重试</button>
        <div style="margin-top:6px">
          <input type="text" class="input" id="retryDocId" placeholder="输入 doc_id 单个重试" style="width:200px">
          <button class="btn btn-sm btn-warning" id="retryOneBtn">重试</button>
        </div>
      ` : `<div style="color:var(--text-secondary)">无异常文件</div>`}
    </div>
  `;

  document.getElementById("retryAllBtn")?.addEventListener("click", async () => {
    const { data, error } = await api.retryAll();
    if (error) toast("重试失败: " + error, true);
    else { toast(`已移回 ${data.moved} 个文件`); refreshErrors(); }
  });
  document.getElementById("retryOneBtn")?.addEventListener("click", async () => {
    const docId = document.getElementById("retryDocId").value.trim();
    if (!docId) return;
    const { data, error } = await api.retryDoc(docId);
    if (error) toast("重试失败: " + error, true);
    else { toast(`已移回 ${data.moved} 个文件`); refreshErrors(); }
  });
}
