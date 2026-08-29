// SPA 路由 + 应用初始化
(function () {
  const container = document.getElementById("view-container");

  const routes = {
    "import": renderImportView,
    "docs": renderDocsView,
    "search": renderSearchView,
    "status": renderStatusView,
    "errors": renderErrorsView,
    "citations": renderCitationsView,
    "literature": renderLiteratureView,
  };

  function parseHash() {
    const hash = location.hash.slice(1) || "import";
    // /docs/<doc_id>[?page=N]（?page 为 A4 页码回跳锚点，不进 doc_id）
    const docMatch = hash.match(/^docs\/(.+?)(\?.*)?$/);
    if (docMatch) return { view: "doc-detail", param: docMatch[1] };
    if (routes[hash]) return { view: hash, param: null };
    return { view: "import", param: null };
  }

  async function navigate() {
    const { view, param } = parseHash();
    // 高亮当前导航
    document.querySelectorAll("#sidebar .nav-links a").forEach(a => {
      const href = a.getAttribute("href").replace("#", "");
      a.classList.toggle("active", href === view || (view === "doc-detail" && href === "docs"));
    });

    if (view === "doc-detail") {
      await renderDocDetailView(param);
    } else if (routes[view]) {
      await routes[view]();
    }
  }

  window.addEventListener("hashchange", navigate);
  navigate();
  // M2：论文项目上下文（宿主 postMessage 通道 + 下拉）
  if (typeof initPaperContext === "function") initPaperContext();
})();
