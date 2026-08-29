// cr-tools 引用链「通道一：复制-粘贴即引用」纯函数权威定义（T16 移植老库 pdfPreviewHandle.ts）
// ── 背景 ──
// DSH client 半区的 `require(spec)` 只解析 seed word / shell-own 模块 / 已注册 bundle，
// 不支持相对路径引入项目内源文件（ADR-002 零依赖 + ADR-004 复用边界）。
// 因此 lib/client.js 内保留一份**逐字一致**的内联实现（匹配/规范化），
// 本文件是**可被 node --test 单测的权威版本**，二者须保持一致（由 test/capture.test.js 的语义守卫校验）。
//
// ── 契约（对齐方案 §2.1 / T16 A/C1）──
// * 捕获槽唯一、新复制覆盖旧、text 截断 1000 字
// * 规范化：去全部空白/换行/全角空格
// * 匹配规则：捕获包含粘贴 或 粘贴以捕获为前缀 + 10 分钟时间窗（双条件命中）；粘贴 <5 字不算
// * 来源标题解析：bibliography.title → source_file → docId

export const COPY_CITE_WINDOW_MS = 10 * 60 * 1000;

export const CAPTURE_TEXT_LIMIT = 1000;

// 规范化：去全部空白 / 换行 / 全角空格（含 \u3000 \u00a0）
export function normalizeCiteText(s) {
  return String(s || '').replace(/[\s\u3000\u00a0]+/g, '');
}

// 匹配规则（双条件 + 时间窗 + <5 字不算）
export function matchCopyCapture(cap, pasted, now) {
  if (!cap) return false;
  if (now - cap.ts > COPY_CITE_WINDOW_MS) return false;
  var p = normalizeCiteText(pasted);
  if (p.length < 5) return false;
  var c = normalizeCiteText(cap.text);
  if (!c) return false;
  return c.indexOf(p) >= 0 || p.indexOf(c) === 0;
}

// 来源标题解析（确认弹窗显示）：bibliography.title → source_file → docId
export function resolveDocTitle(biblioItems, docId, sourceFile) {
  if (biblioItems && docId) {
    var it = biblioItems.filter(function (i) { return i.doc_id === docId })[0];
    if (it && it.bibliography && it.bibliography.title) return it.bibliography.title;
  }
  if (sourceFile) return sourceFile.replace(/\.(pdf|md|txt|docx|png|jpg|jpeg)$/i, '');
  return docId || '未知来源';
}

// 捕获文本截断（1000 字上限；用 CAPTURE_TEXT_LIMIT 常量，client.js 内联实现与此一致）
export function sliceCaptureText(text) {
  return String(text || '').slice(0, CAPTURE_TEXT_LIMIT);
}
