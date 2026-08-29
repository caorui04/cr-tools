// 全局状态管理
const State = {
  status: null,
  docs: [],
  searchResults: [],
  currentView: "import",
  online: false,
  // M2：当前论文项目（宿主项目树选中 / webui 下拉切换；文献工作台/登记引用上下文）
  currentPaperId: null,
  /** 已加载的项目下拉选项缓存 {paper_id, name}[] */
  papers: [],
};

// ---- M2：论文项目上下文（宿主 postMessage 通道 + 下拉切换）----
/** 设置当前论文项目：同步 State + 广播事件（citations.js / doc-detail.js 等监听） */
function setCurrentPaper(paperId) {
  State.currentPaperId = paperId || null;
  document.dispatchEvent(new CustomEvent("paper-changed", { detail: { paperId: State.currentPaperId } }));
  const sel = document.getElementById("paperProjectSel");
  if (sel) sel.value = State.currentPaperId || "";
}

/** 加载论文项目下拉选项（GET /papers），渲染到 #paperProjectSel（存在时） */
async function loadPapersDropdown() {
  const sel = document.getElementById("paperProjectSel");
  if (!sel || sel.dataset.loaded) return;
  sel.dataset.loaded = "1";
  const { data, error } = await api.getPapers();
  if (error) return;
  State.papers = (data && data.papers) || [];
  sel.innerHTML = `<option value="">（未选择论文项目）</option>`
    + State.papers.map(p => `<option value="${esc(p.paper_id)}">${esc(p.name)}</option>`).join("");
  sel.value = State.currentPaperId || "";
}

function initPaperContext() {
  // 宿主 → webui：当前项目下发
  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!d || typeof d.type !== "string") return;
    if (d.type === "paper-kb:set-project") {
      setCurrentPaper(d.paperId || null);
    }
  });
  // webui 加载完成 → 向宿主握手要当前项目（宿主回复 set-project）
  window.parent.postMessage({ type: "paper-kb:request-project" }, "*");
  // 下拉选项加载由各视图（citations.js）在渲染时调用 loadPapersDropdown()
}

function toast(msg, isError = false) {
  const el = document.getElementById("toast");
  const div = document.createElement("div");
  div.className = "toast-msg" + (isError ? " error" : "");
  div.textContent = msg;
  el.appendChild(div);
  setTimeout(() => div.remove(), 3000);
}
