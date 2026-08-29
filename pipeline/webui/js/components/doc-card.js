// doc-card 组件 — 文档卡片
function renderDocCard(doc) {
  const statusLabels = { pending: "待补录", auto_filled: "自动", confirmed: "已确认" };
  const statusClass = doc.metadata_status === "confirmed" ? "badge-success" :
    doc.metadata_status === "auto_filled" ? "badge-info" : "badge-warning";
  return `
    <div class="card doc-card" onclick="location.hash='#docs/${doc.doc_id}'" style="cursor:pointer">
      <div style="font-weight:600;margin-bottom:4px">${esc(doc.title || doc.source_file || doc.doc_id)}</div>
      <div style="font-size:12px;color:var(--text-secondary)">
        ${doc.authors ? esc(doc.authors.join(", ")) + " · " : ""}
        ${doc.year || ""}
        <span class="badge ${statusClass}">${statusLabels[doc.metadata_status] || doc.metadata_status}</span>
      </div>
      <div style="font-size:11px;color:var(--text-secondary);margin-top:2px">${doc.ingested_at || ""}</div>
    </div>`;
}

// search-result 组件
function renderSearchResult(r, query) {
  const text = highlightText(r.text, query);
  return `
    <div class="card">
      <div style="font-weight:600;margin-bottom:4px">
        ${esc(r.doc_meta?.title || r.doc_meta?.source_file || r.doc_hash)}
        <span style="font-size:12px;color:var(--text-secondary)"> · score: ${r.score?.toFixed(2)}</span>
      </div>
      <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">
        第${r.doc_meta?.page_estimate || "?"}页
      </div>
      <div style="font-size:13px;line-height:1.6">${text}</div>
    </div>`;
}

// queue-badge 组件
function renderQueueBadge(label, count) {
  return `<span class="badge ${count > 0 ? 'badge-warning' : 'badge-info'}" style="margin:2px">${label}: ${count}</span>`;
}

// confidence-bar 组件
function renderConfidenceBar(field, value) {
  const cls = value < 0.6 ? "low" : "";
  return `<div style="display:flex;align-items:center;gap:6px;margin:2px 0">
    <span style="font-size:11px;width:55px">${field}</span>
    <div class="conf-bar" style="flex:1"><div class="conf-bar-fill ${cls}" style="width:${(value*100).toFixed(0)}%"></div></div>
    <span style="font-size:11px;width:35px;text-align:right">${(value*100).toFixed(0)}%</span>
  </div>`;
}

function esc(s) { return (s || "").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]); }

function highlightText(text, query) {
  if (!query) return esc(text);
  const words = query.split(/\s+/).filter(Boolean);
  let result = esc(text);
  words.forEach(w => {
    result = result.replace(new RegExp(`(${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'), '<mark>$1</mark>');
  });
  return result;
}
