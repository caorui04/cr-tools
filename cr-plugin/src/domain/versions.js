// cr-tools paper 领域模型：versions.jsonl 只读解析层（T4）
//
// 对齐对象：pipeline 版本日志（drafts/_history 快照 + versions.jsonl 时间线）
// 容错：坏行跳过（不中断整体解析）；空输入 → 空条目列表。

/** 解析 versions.jsonl 文本 → Version 条目数组（坏行跳过，保持文件顺序）。 */
export function parseVersions(text) {
  if (typeof text !== 'string' || text.trim() === '') return [];
  const entries = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line === '') continue;
    try {
      const data = JSON.parse(line);
      const entry = normalizeEntry(data);
      if (entry !== null) entries.push(entry);
    } catch {
      // 坏行跳过（容错，对齐 Python 侧宽容解析）
    }
  }
  return entries;
}

export class Versions {
  constructor(entries = []) {
    this.entries = entries;
  }

  static parse(text) {
    return new Versions(parseVersions(text));
  }

  /** 版本号最大的条目；空则 null。 */
  latest() {
    let best = null;
    for (const e of this.entries) {
      if (typeof e.version === 'number' && (best === null || e.version > best.version)) {
        best = e;
      }
    }
    return best;
  }

  /** 指定草稿名的全部版本（按版本号升序）；无则空数组。 */
  byDraft(draftName) {
    return this.entries
      .filter((e) => e.draft === draftName)
      .sort((a, b) => (a.version ?? 0) - (b.version ?? 0));
  }
}

function normalizeEntry(data) {
  if (typeof data !== 'object' || data === null || Array.isArray(data)) return null;
  const entry = {};
  if (typeof data.ts === 'string') entry.ts = data.ts;
  if (typeof data.action === 'string') entry.action = data.action;
  if (typeof data.draft === 'string') entry.draft = data.draft;
  if (typeof data.version === 'number') entry.version = data.version;
  if (typeof data.note === 'string') entry.note = data.note;
  if (typeof data.user === 'string') entry.user = data.user;
  if (entry.ts === undefined && entry.action === undefined && entry.version === undefined) {
    return null; // 无任何已知字段，视为坏行
  }
  return entry;
}
