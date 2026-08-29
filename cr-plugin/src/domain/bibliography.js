// cr-tools paper 领域模型：bibliography.csl.json 只读解析层（T4）
//
// 对齐对象：pipeline/m6_cite/bibliography_store.py（单一事实来源，只读不写）
// 容错对齐 Python load()：损坏/非 dict/items 非 list → 空骨架 {paper_id, format, items: []}。

export const DEFAULT_FORMAT = 'GB/T 7714-2015';
export const FORMATS = Object.freeze(['GB/T 7714-2015', 'APA', 'MLA', 'Chicago']);

/** 解析 CSL 清单 JSON 文本；非法输入返回空骨架。 */
export function parseBibliography(text, paperId = null) {
  if (typeof text !== 'string' || text.trim() === '') {
    return new Bibliography({ paper_id: paperId, format: DEFAULT_FORMAT, items: [] });
  }
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return new Bibliography({ paper_id: paperId, format: DEFAULT_FORMAT, items: [] });
  }
  if (typeof data !== 'object' || data === null || Array.isArray(data)) {
    return new Bibliography({ paper_id: paperId, format: DEFAULT_FORMAT, items: [] });
  }
  return new Bibliography({ ...data, paper_id: data.paper_id ?? paperId });
}

export class Bibliography {
  constructor(data = {}) {
    this.paperId = typeof data.paper_id === 'string' ? data.paper_id : null;
    this.format = typeof data.format === 'string' ? data.format : DEFAULT_FORMAT;
    this.updatedAt = typeof data.updated_at === 'string' ? data.updated_at : '';
    this.items = Array.isArray(data.items) ? data.items.map(normalizeItem) : [];
  }

  static fromJSON(data) {
    return new Bibliography(data);
  }

  static parse(text, paperId = null) {
    return parseBibliography(text, paperId);
  }

  /** 规范化输出：items 条目保留原始字段（含 snippet 等可选元数据）。 */
  toJSON() {
    const out = { format: this.format, items: this.items.map((it) => ({ ...it })) };
    if (this.paperId !== null) out.paper_id = this.paperId;
    if (this.updatedAt !== '') out.updated_at = this.updatedAt;
    return out;
  }

  /** 按 doc_id 查条目（去重/查号用）；无则 null。 */
  findByDocId(docId) {
    return this.items.find((it) => it.doc_id === docId) ?? null;
  }

  /** 按清单序号查条目；无则 null。 */
  findBySeq(seq) {
    return this.items.find((it) => it.seq === seq) ?? null;
  }

  get count() {
    return this.items.length;
  }
}

function normalizeItem(item) {
  if (typeof item !== 'object' || item === null) return {};
  const out = {};
  // 保留已知键（含可选元数据），未知键透传
  for (const [k, v] of Object.entries(item)) {
    if (v !== undefined) out[k] = v;
  }
  return out;
}
