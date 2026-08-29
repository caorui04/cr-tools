// 导入页面
async function renderImportView() {
  const container = document.getElementById("view-container");
  const { data: importList } = await api.getImportList();
  container.innerHTML = `
    <div class="card">
      <h3 style="margin-bottom:8px">导入论文</h3>
      <div id="dropzone" style="border:2px dashed var(--border);border-radius:var(--radius);padding:30px;text-align:center;color:var(--text-secondary);margin-bottom:10px">
        拖拽 PDF 文件到此处 或 <label class="btn btn-primary btn-sm" style="cursor:pointer">选择文件<input type="file" id="fileInput" multiple accept=".pdf,.jpg,.png,.docx,.txt,.md" style="display:none"></label>
      </div>
      <div id="selectedFiles" style="font-size:12px;margin-bottom:8px"></div>
      <label style="font-size:13px;margin-right:10px">
        <input type="checkbox" id="translateAll"> 全部翻译
      </label>
      <button class="btn btn-primary" id="submitBtn" disabled>提交</button>
    </div>
    <div id="submitResult"></div>
    ${importList && importList.length ? `
    <div class="card"><h4>待导入清单</h4>
      ${importList.map((item, i) => `<div style="font-size:12px;display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border)"><span>${esc(item.path)}</span><button class="btn btn-danger btn-sm" data-idx="${i}">删除</button></div>`).join("")}
    </div>` : ""}
    <div id="pendingBox"></div>
  `;

  let selectedFiles = [];
  const fileInput = container.querySelector("#fileInput");
  const dropzone = container.querySelector("#dropzone");
  const submitBtn = container.querySelector("#submitBtn");
  const filesDiv = container.querySelector("#selectedFiles");

  function updateFiles() {
    filesDiv.innerHTML = selectedFiles.map(f => `<div>📄 ${esc(f.name)}</div>`).join("");
    submitBtn.disabled = selectedFiles.length === 0;
  }

  fileInput.addEventListener("change", () => {
    selectedFiles = Array.from(fileInput.files);
    updateFiles();
  });

  dropzone.addEventListener("dragover", e => { e.preventDefault(); dropzone.style.borderColor = "var(--primary)"; });
  dropzone.addEventListener("dragleave", () => { dropzone.style.borderColor = "var(--border)"; });
  dropzone.addEventListener("drop", e => {
    e.preventDefault();
    dropzone.style.borderColor = "var(--border)";
    selectedFiles = Array.from(e.dataTransfer.files);
    updateFiles();
  });

  // E-1：待处理队列窗格（[损坏] 红色标识 + 强制处理/删除）
  async function renderPending() {
    const box = container.querySelector("#pendingBox");
    const { data } = await api.getStatus();
    const pending = (data && data.pending) || [];
    if (!pending.length) { box.innerHTML = ""; return; }
    box.innerHTML = `
      <div class="card">
        <h4>待处理队列</h4>
        ${pending.map(name => {
          const corrupt = name.startsWith("[损坏]");
          const color = corrupt ? "var(--danger)" : "inherit";
          return `<div style="font-size:12px;display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)">
            <span style="color:${color};font-weight:${corrupt?"bold":"normal"}">${corrupt?"⚠ ":""}${esc(name)}</span>
            <span>
              ${corrupt ? `<button class="btn btn-warning btn-sm" data-force="${esc(name)}">强制处理</button>` : ""}
              <button class="btn btn-danger btn-sm" data-del="${esc(name)}">删除</button>
            </span>
          </div>`;
        }).join("")}
        ${pending.some(n => n.startsWith("[损坏]")) ? `<div style="font-size:11px;color:var(--warning);margin-top:6px">⚠ 损坏文件处理结果可能不完整或错误，建议删除后重新提交完整文件。</div>` : ""}
      </div>`;
    box.querySelectorAll("[data-force]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const name = btn.dataset.force;
        if (!confirm(`该文件已损坏，强制处理不保证完整准确的输出全部文本。是否继续？`)) return;
        const { data, error } = await api.pendingForce(name);
        if (error) { alert("操作失败: " + error); return; }
        renderPending();
      });
    });
    box.querySelectorAll("[data-del]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const name = btn.dataset.del;
        if (!confirm(`删除待处理文件 "${name}"？`)) return;
        const { data, error } = await api.pendingDelete(name);
        if (error) { alert("操作失败: " + error); return; }
        renderPending();
      });
    });
  }
  renderPending();

  submitBtn.addEventListener("click", async () => {
    const translate = container.querySelector("#translateAll").checked;
    const { data, error } = await api.uploadFiles(selectedFiles, translate);
    const resultDiv = container.querySelector("#submitResult");
    if (error) { resultDiv.innerHTML = `<div style="color:var(--danger)">错误: ${esc(error)}</div>`; return; }
    let html = "";
    if (data.accepted.length) html += `<div style="color:var(--success)">✅ 已接受 ${data.accepted.length} 个文件</div>`;
    if (data.rejected.length) html += `<div style="color:var(--danger)">❌ 已拒绝: ${data.rejected.map(r => esc(r.path)+": "+esc(r.reason)).join(", ")}</div>`;
    if (data.warnings && data.warnings.length) {
      const corrupt = data.warnings.filter(w => w.reason.includes("损坏"));
      const others = data.warnings.filter(w => !w.reason.includes("损坏"));
      if (corrupt.length) html += `<div style="color:var(--danger);font-weight:bold;margin-top:6px">⚠ 损坏警告: ${corrupt.map(w => esc(w.path)).join(", ")} 已损坏，处理结果可能不完整或错误</div>`;
      if (others.length) html += `<div style="color:var(--warning)">⚠ ${others.map(w => esc(w.reason)).join(", ")}</div>`;
    }
    resultDiv.innerHTML = html;
    renderPending();
  });
}
