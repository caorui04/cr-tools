// 搜索页面
async function renderSearchView() {
  const container = document.getElementById("view-container");
  container.innerHTML = `
    <div class="card">
      <input type="text" class="input" id="searchQuery" placeholder="输入搜索关键词..." style="margin-bottom:6px">
      <div style="display:flex;gap:8px;align-items:center">
        <select class="select" id="searchTopK" style="width:80px">
          <option value="5">5</option><option value="10">10</option><option value="20">20</option>
        </select>
        <button class="btn btn-primary" id="searchBtn">搜索</button>
      </div>
    </div>
    <div id="searchResults"></div>
  `;

  document.getElementById("searchBtn").addEventListener("click", async () => {
    const q = document.getElementById("searchQuery").value.trim();
    if (!q) return;
    const topK = parseInt(document.getElementById("searchTopK").value);
    const { data, error } = await api.search(q, topK, null);
    const resultDiv = document.getElementById("searchResults");
    if (error) { resultDiv.innerHTML = `<div style="color:var(--danger)">搜索失败: ${esc(error)}</div>`; return; }
    const results = data?.results || [];
    resultDiv.innerHTML = results.length === 0
      ? `<div style="text-align:center;color:var(--text-secondary);padding:20px">未找到相关内容</div>`
      : results.map(r => renderSearchResult(r, q)).join("");
  });
}
