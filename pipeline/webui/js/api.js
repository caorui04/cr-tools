// API 调用封装 — 全部 C3 路由
// 同源调用（页面由管线服务托管）；token 由服务端注入 window.__KB_TOKEN__
const API_BASE = "";

async function _fetch(method, path, body) {
  try {
    const headers = { "Content-Type": "application/json" };
    const token = window.__KB_TOKEN__ || "";
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(API_BASE + path, opts);
    const data = await resp.json();
    if (!resp.ok) return { data: null, error: data.error || resp.statusText };
    return { data, error: null };
  } catch (e) {
    return { data: null, error: e.message };
  }
}

const api = {
  // Web UI 文件上传（浏览器拿不到磁盘路径，走字节上传）
  async uploadFiles(fileList, translate) {
    try {
      const fd = new FormData();
      for (const f of fileList) fd.append("files", f, f.name);
      fd.append("translate", translate ? "true" : "false");
      const headers = {};
      const token = window.__KB_TOKEN__ || "";
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const resp = await fetch(API_BASE + "/upload", { method: "POST", headers, body: fd });
      const data = await resp.json();
      if (!resp.ok) return { data: null, error: data.error || resp.statusText };
      return { data, error: null };
    } catch (e) {
      return { data: null, error: e.message };
    }
  },
  submitFiles(paths, translate, translatePaths) {
    return _fetch("POST", "/submit", { paths, translate, translate_paths: translatePaths });
  },
  getStatus() { return _fetch("GET", "/status"); },
  retryDoc(docId) { return _fetch("POST", "/retry", { doc_id: docId }); },
  retryAll() { return _fetch("POST", "/retry", { all: true }); },
  search(query, topK, docHash) {
    return _fetch("POST", "/search", { query, top_k: topK || 5, doc_hash: docHash || null });
  },
  citeDoc(docId, format) {
    return _fetch("POST", "/cite", { doc_id: docId, format: format || "GB/T 7714-2015" });
  },
  getProvider() { return _fetch("POST", "/provider", {}); },
  setProviderMode(mode) { return _fetch("POST", "/provider", { mode }); },
  // P3.5-1 引用链清单
  getPapers() { return _fetch("GET", "/papers"); },
  getPaperDetail(paperId) { return _fetch("GET", `/papers/${encodeURIComponent(paperId)}`); },
  renamePaper(paperId, name) { return _fetch("POST", `/papers/${encodeURIComponent(paperId)}/rename`, { name }); },
  getPaperBiblio(paperId) { return _fetch("GET", `/papers/${encodeURIComponent(paperId)}/bibliography`); },
  addPaperBiblio(paperId, docId, extra) {
    return _fetch("POST", `/papers/${encodeURIComponent(paperId)}/bibliography/add`, { doc_id: docId, ...(extra || {}) });
  },
  removePaperBiblio(paperId, seq) { return _fetch("POST", `/papers/${encodeURIComponent(paperId)}/bibliography/remove`, { seq }); },
  formatPaperBiblio(paperId, format) { return _fetch("POST", `/papers/${encodeURIComponent(paperId)}/bibliography/format`, { format }); },
  verifyPaperBiblio(paperId, seq, claim) { return _fetch("POST", `/papers/${encodeURIComponent(paperId)}/bibliography/verify`, { seq, claim }); },
  listDocs(offset, limit, q) {
    return _fetch("GET", `/docs?offset=${offset||0}&limit=${limit||20}&q=${encodeURIComponent(q||"")}`);
  },
  getDocDetail(docId) { return _fetch("GET", `/docs/${docId}`); },
  getDocDigest(docId, force) { return _fetch("GET", `/docs/${docId}/digest${force ? "?force=1" : ""}`); },
  getDocDigestProgress(docId) { return _fetch("GET", `/docs/${docId}/digest/progress`); },
  verifyDoc(docId, claim) { return _fetch("GET", `/docs/${docId}/verify?claim=${encodeURIComponent(claim)}`); },
  translateDoc(docId, force) { return _fetch("POST", `/docs/${docId}/translate`, { force }); },
  webLitSearch(topic, maxResults) { return _fetch("POST", "/web/literature_search", { topic, max_results: maxResults || 8 }); },
  confirmBibliography(docId, biblio) {
    return _fetch("POST", `/docs/${docId}/bibliography`, { bibliography: biblio });
  },
  getImportList() { return _fetch("GET", "/import-list"); },
  deleteImportItem(idx) { return _fetch("DELETE", `/import-list/${idx}`); },
  // E-1：待处理队列操作
  pendingForce(name) { return _fetch("POST", `/pending/${encodeURIComponent(name)}/force`); },
  pendingDelete(name) { return _fetch("DELETE", `/pending/${encodeURIComponent(name)}`); },
};
