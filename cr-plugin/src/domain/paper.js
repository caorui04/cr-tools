// cr-tools paper 领域模型：workbench-state.json 只读解析层（T4）
//
// 对齐对象：pipeline/server/workbench_state.py（单一事实来源，只读不写）
// 边界：本模块绝不写文件、不发网络请求；写操作一律走管线 HTTP 路由。
// 容错行为对齐 Python load()：损坏/缺失 → 空骨架（schema=1, 空 paths/filemap）。

const EMPTY_STATE = Object.freeze({
  schema: 1,
  created_at: '',
  last_updated: '',
  paths: {},
  filemap: {},
});

const PATH_KEYS = Object.freeze([
  'project_root',
  'drafts',
  'output',
  'history',
  'versions_log',
  'biblio',
  'inputs',
]);

/** 解析 workbench-state JSON 文本；非法输入返回空骨架（对齐 Python load 容错）。 */
export function parseWorkbenchState(text) {
  if (typeof text !== 'string' || text.trim() === '') return { ...EMPTY_STATE };
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return { ...EMPTY_STATE };
  }
  if (typeof data !== 'object' || data === null || Array.isArray(data)) {
    return { ...EMPTY_STATE };
  }
  return Paper.fromJSON(data).toJSON();
}

export class Paper {
  /**
   * @param {object} data workbench-state 原始对象
   * @param {string|null} paperId 项目标识（缺省取 data.paper_id，再缺省 null）
   */
  constructor(data = {}, paperId = null) {
    this.paperId = paperId ?? data.paper_id ?? null;
    this.schema = typeof data.schema === 'number' ? data.schema : 1;
    this.createdAt = typeof data.created_at === 'string' ? data.created_at : '';
    this.lastUpdated = typeof data.last_updated === 'string' ? data.last_updated : '';
    this.paths = normalizePaths(data.paths);
    this.filemap = normalizeFilemap(data.filemap);
    this.inputsLog = normalizeInputsLog(data.inputs_log);
  }

  static fromJSON(data) {
    return new Paper(data);
  }

  static parse(text, paperId = null) {
    if (typeof text !== 'string' || text.trim() === '') return new Paper({}, paperId);
    try {
      const data = JSON.parse(text);
      return new Paper(data, paperId);
    } catch {
      return new Paper({}, paperId);
    }
  }

  /** 规范化输出（round-trip 稳定）：所有键齐备，缺失填默认。 */
  toJSON() {
    const out = {
      schema: this.schema,
      created_at: this.createdAt,
      last_updated: this.lastUpdated,
      paths: { ...this.paths },
      filemap: { ...this.filemap },
    };
    if (this.paperId !== null) out.paper_id = this.paperId;
    if (this.inputsLog.length > 0) out.inputs_log = this.inputsLog.map((i) => ({ ...i }));
    return out;
  }

  /** 固化路径（7 键之一），缺失返回 null。 */
  path(key) {
    return this.paths[key] ?? null;
  }

  /** filemap 中 drafts/ 前缀的逻辑相对路径（排序稳定）。 */
  draftLogicalNames() {
    return Object.keys(this.filemap)
      .filter((k) => k.startsWith('drafts/'))
      .sort();
  }

  /** filemap 中 output/ 前缀的逻辑相对路径（排序稳定）。 */
  outputLogicalNames() {
    return Object.keys(this.filemap)
      .filter((k) => k.startsWith('output/'))
      .sort();
  }

  /**
   * 指定草稿的最新阶段交付件逻辑名（对齐 Python output_latest）：
   * 逻辑名匹配 `output/{stem}_v{N}.docx`，N 取最大；无则 null。
   */
  latestOutput(draftStem) {
    const prefix = `output/${draftStem}_v`;
    let best = null; // (version, logicalRel)
    for (const lr of Object.keys(this.filemap)) {
      if (!lr.startsWith(prefix) || !lr.endsWith('.docx')) continue;
      const ver = lr.slice(prefix.length, -'.docx'.length);
      if (/^\d+$/.test(ver)) {
        const n = Number(ver);
        if (best === null || n > best[0]) best = [n, lr];
      }
    }
    return best === null ? null : best[1];
  }

  /** inputs_log 按文件名查找条目；无则 null。 */
  inputByName(name) {
    return this.inputsLog.find((i) => i.name === name) ?? null;
  }

  /** inputs_log 中已登记向量 doc_id 的条目（「本项目资料」徽标对照用）。 */
  inputsWithDocId() {
    return this.inputsLog.filter((i) => typeof i.doc_id === 'string' && i.doc_id.length > 0);
  }
}

function normalizePaths(paths) {
  const out = {};
  if (paths && typeof paths === 'object' && !Array.isArray(paths)) {
    for (const key of PATH_KEYS) {
      const v = paths[key];
      if (typeof v === 'string' && v.length > 0) out[key] = v;
    }
  }
  return out;
}

function normalizeFilemap(filemap) {
  const out = {};
  if (filemap && typeof filemap === 'object' && !Array.isArray(filemap)) {
    for (const [logical, physical] of Object.entries(filemap)) {
      if (typeof physical === 'string' && physical.length > 0) out[logical] = physical;
    }
  }
  return out;
}

function normalizeInputsLog(log) {
  if (!Array.isArray(log)) return [];
  const out = [];
  for (const item of log) {
    if (typeof item !== 'object' || item === null) continue;
    const name = typeof item.name === 'string' ? item.name : '';
    if (name === '') continue;
    const entry = { name };
    if (typeof item.ts === 'string') entry.ts = item.ts;
    if (typeof item.source === 'string') entry.source = item.source;
    if (typeof item.doc_id === 'string') entry.doc_id = item.doc_id;
    out.push(entry);
  }
  return out;
}
