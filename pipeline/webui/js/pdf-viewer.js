// PDF 预览渲染器（PDF.js 本地托管，Apache-2.0）
// canvas 渲染 + 翻页 + 缩放 + textLayer（文字可选中）→ 选区「引用此段」登记引用清单
// 交互链：选中文本 → pdfCiteBar 显示 → 点「引用此段」→ window.__onPdfCite({docId,page,snippet})

(function () {
  const API_BASE = "";

  let pdfDoc = null;
  let pageNum = 1;
  let scale = 1.2;
  let container = null;
  let canvas = null;
  let ctx = null;
  let wrapper = null;
  let textLayer = null;
  let textLayerDiv = null;
  let currentUrl = null;
  let currentDocId = null;
  let token = "";
  let renderTask = null;
  // 当前渲染的 PDF 文档引用（detect 异步完成时校验是否已被新文档替换）
  let currentPdfDoc = null;
  // 原始页码映射（入库页码 → PDF 原始页码）：{startIdx, startVal}，即 startVal + (pageNum-1-startIdx) = 原始页码
  // null = 未检测成功/已失效（宁缺毋假）
  let origInfo = null;
  // 连续不符计数（自纠用）：连续 3 页与理论偏移不符 → 全局失效
  let origMissStreak = 0;

  const ui = {
    get box() { return document.getElementById("pdfBox"); },
    get prevBtn() { return document.getElementById("pdfPrev"); },
    get nextBtn() { return document.getElementById("pdfNext"); },
    get pageLabel() { return document.getElementById("pdfPage"); },
    get pageCount() { return document.getElementById("pdfPages"); },
    get zoomInBtn() { return document.getElementById("pdfZoomIn"); },
    get zoomOutBtn() { return document.getElementById("pdfZoomOut"); },
    get fitBtn() { return document.getElementById("pdfFit"); },
    get status() { return document.getElementById("pdfStatus"); },
    get origPage() { return document.getElementById("pdfOrigPage"); },
    get citeBar() { return document.getElementById("pdfCiteBar"); },
    get citeInfo() { return document.getElementById("pdfCiteInfo"); },
    get citeBtn() { return document.getElementById("pdfCiteBtn"); },
  };

  async function initPdfJs() {
    if (window.__pdfjsReady) return;
    // 经典版（3.x UMD/IIFE）：由 index.html 的 <script> 引入，全局暴露 window.pdfjsLib
    const pdfjs = window.pdfjsLib;
    if (!pdfjs) throw new Error("pdf.js 未加载（vendor/pdfjs/pdf.min.js 缺失）");
    // 经典 Worker（.js）：不走 module worker，规避 ESM 顶层语法在 fake-worker 回退下的解析错误
    pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.min.js";
    window.__pdfjsLib = pdfjs;
    window.__pdfjsReady = true;
  }

  function setStatus(msg, isErr) {
    if (ui.status) {
      ui.status.textContent = msg;
      ui.status.style.color = isErr ? "var(--danger, #c62828)" : "var(--text-secondary, #666)";
    }
  }

  /** 渲染 PDF 到预览容器。url: /docs/{id}/file；token: 管线 API token；opts: {docId, page}（page = 初始页码锚点，A4 回跳用） */
  async function renderPdf(url, apiToken, opts) {
    currentUrl = url;
    currentDocId = (opts && opts.docId) || null;
    const initialPage = (opts && opts.page && Number.isInteger(opts.page) && opts.page >= 1) ? opts.page : 1;
    token = apiToken || "";
    container = ui.box;
    if (!container) return;
    // 状态行在 pdfBox 之外，先显示加载态
    setStatus("正在加载 PDF...");
    hideCiteBar();
    canvas = document.createElement("canvas");
    // 不用 max-width 压缩（CSS 缩放会与 textLayer 像素定位错位 → 选中错位）；
    // 尺寸 = 像素尺寸，靠首次适宽保证不超宽，缩放时出滚动条由 pdfBox 处理
    canvas.style.display = "block";
    canvas.style.margin = "0 auto";
    wrapper = document.createElement("div");
    wrapper.style.position = "relative";
    wrapper.style.margin = "0 auto";
    wrapper.style.width = "fit-content";
    wrapper.appendChild(canvas);
    container.innerHTML = "";
    container.appendChild(wrapper);
    ctx = canvas.getContext("2d");
    // 选区监听只绑一次（同一 pdfBox 多次渲染会累积监听器）
    if (!container.dataset.selectionBound) {
      container.addEventListener("mouseup", onSelection);
      container.dataset.selectionBound = "1";
    }

    try {
      await initPdfJs();
      const pdfjs = window.__pdfjsLib;
      // 带鉴权头取字节（token 不进 URL）
      const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}));
        throw new Error(errBody.error || `HTTP ${resp.status}`);
      }
      const data = await resp.arrayBuffer();
      pdfDoc = await pdfjs.getDocument({ data }).promise;
      currentPdfDoc = pdfDoc;
      origInfo = null;
      pageNum = Math.min(initialPage, pdfDoc.numPages);
      if (ui.pageCount) ui.pageCount.textContent = String(pdfDoc.numPages);
      // 首次适宽：按容器宽度算 scale，canvas 像素尺寸不超宽被 CSS 压缩
      // （保证 textLayer 与 canvas 严格对齐，选中准确）
      const firstPage = await pdfDoc.getPage(1);
      const vp1 = firstPage.getViewport({ scale: 1 });
      const cw = Math.max(200, (container.clientWidth || 600) - 24);
      scale = Math.max(0.5, cw / vp1.width);
      setStatus(`共 ${pdfDoc.numPages} 页（选中文字可点「引用此段」登记引用）`);
      await renderPage();
      // 后台检测原始页码偏移（不阻塞首次渲染），完成后按当前页刷新参考显示
      detectOriginalOffset(pdfjs, pdfDoc).then(() => {
        if (pdfDoc === currentPdfDoc) updateOrigDisplay();
      }).catch(() => {});
    } catch (err) {
      console.error("[pdf-viewer] 加载失败:", err);
      // 移除 0 尺寸 canvas（否则显示空白小方块），展示错误
      canvas.remove();
      setStatus(`PDF 加载失败：${err.message || err}`, true);
    }
  }

  /** 选区检测：textLayer 内选中 ≥5 字 → 显示引用条 */
  function onSelection() {
    const sel = window.getSelection();
    const text = sel ? sel.toString().trim() : "";
    if (text.length < 5) { hideCiteBar(); return; }
    // 确认选区源自预览文本层（textLayer 内的 span）
    let inLayer = false;
    let node = sel.anchorNode;
    while (node && node !== document) {
      if (node.classList && node.classList.contains("textLayer")) { inLayer = true; break; }
      node = node.parentNode;
    }
    if (!inLayer) { hideCiteBar(); return; }
    if (ui.citeInfo) ui.citeInfo.textContent = `已选中 ${text.length} 字（第 ${pageNum} 页）`;
    if (ui.citeBar) ui.citeBar.style.display = "flex";
  }

  function hideCiteBar() {
    if (ui.citeBar) ui.citeBar.style.display = "none";
  }

  // ── 原始页码（入库页码 ↔ PDF 原页）──────────────────────────────
  // 数据源：textLayer 已取的 getTextContent（PDF 用户空间坐标，原点左下，y 向上）
  // 规则：底部(y 小)/顶部(y 大)边缘的孤立数字（排除年份/超大值）。
  // 候选保留全部（不裁剪）：干扰数字靠跨页递增约束（DFS/自纠）筛除，而非单页裁剪。
  // 连续 3 页与理论值不符才判全局失效（宁缺毋假），个别页不符只隐藏当页显示。
  function pickPageLabelCandidates(textContent, pageHeight) {
    const toks = [];
    const items = (textContent && textContent.items) || [];
    for (const it of items) {
      const str = (it.str || "").trim();
      if (!/^\d{1,4}$/.test(str)) continue;
      const t = it.transform || [0, 0, 0, 0, 0, 0];
      const x = t[4], y = t[5];
      const inFooter = y < pageHeight * 0.15;   // PDF 坐标原点左下 → 页脚 y 小
      const inHeader = y > pageHeight * 0.85;   // 页眉 y 接近页高
      if (!inFooter && !inHeader) continue;
      toks.push({ x, y, n: parseInt(str, 10), digits: str.length });
    }
    if (!toks.length) return null;
    // 合并同 y 行、x 相邻的数字 token 为多位数（PDF 提取字序可能乱序：个位在前十位在后）
    // 例：页码 35 被拆成 '3'@456 + '5'@461 → 合并为 35。干扰数字靠 DFS/自纠的跨页约束筛除。
    toks.sort((a, b) => (a.y !== b.y ? a.y - b.y : a.x - b.x));
    const merged = [];
    let cur = null;
    for (const t of toks) {
      // 同行（y 差 < 3）且横向紧邻（间距 < 8 + 串宽估算）→ 拼接
      if (cur && Math.abs(t.y - cur.y) < 3 && t.x - (cur.x + cur.n / 10) < 8) {
        cur.digits += String(t.n);
        cur.x = Math.min(cur.x, t.x);
        cur.n = parseInt(cur.digits, 10);
      } else {
        if (cur) merged.push(cur);
        cur = { x: t.x, y: t.y, n: t.n, digits: String(t.n) };
      }
    }
    if (cur) merged.push(cur);
    const out = merged.filter(c => !(c.n >= 1900 && c.n <= 2100) && c.n <= 3000);
    if (!out.length) return null;
    out.sort((a, b) => a.x - b.x);
    return out;
  }

  function origLabelFor(pno) {
    if (!origInfo) return null;
    return origInfo.startVal + (pno - 1 - origInfo.startIdx);
  }

  function updateOrigDisplay() {
    const el = ui.origPage;
    if (!el) return;
    const v = origLabelFor(pageNum);
    if (v === null || v < 1) { el.style.display = "none"; el.textContent = ""; return; }
    el.style.display = "";
    el.textContent = `（原始第 ${v} 页）`;
  }

  /** 逐页自纠：当前页实际页码与理论偏移不符 → 隐藏当页参考；连续 3 页不符 → 全局失效 */
  function maybeVerifyOrig(pno, textContent, pageHeight) {
    const el = ui.origPage;
    if (!origInfo) return;
    const cands = pickPageLabelCandidates(textContent, pageHeight);
    if (!cands) { el.style.display = "none"; return; } // 该页无页码候选（图版页等）隐藏当页
    const expected = origLabelFor(pno);
    if (expected === null) return;
    if (cands.some(c => c.n === expected)) {
      origMissStreak = 0;
      updateOrigDisplay();
      return;
    }
    origMissStreak += 1;
    el.style.display = "none"; // 当页不符 → 当页不显示参考
    if (origMissStreak >= 3) {
      origInfo = null;         // 连续 3 页不符 → 偏移整体不可信，宁缺毋假
      origMissStreak = 0;
    }
  }

  /** 检测原始页码偏移：PDF 内部 PageLabels 优先，否则前 4 页边缘数字找差=1 递增段（长度≥3） */
  async function detectOriginalOffset(pdfjs, pdfDoc) {
    try {
      // 1) PDF 内部 PageLabels（最准，如学术全文分段：前言 I、正文 1）
      const labels = await pdfDoc.getPageLabels();
      if (Array.isArray(labels) && labels.length >= 3) {
        const first = parseInt(labels[0], 10);
        if (Number.isInteger(first) && first > 0) {
          let ok = true;
          for (let i = 1; i < Math.min(labels.length, 5); i++) {
            if (parseInt(labels[i], 10) !== first + i) { ok = false; break; }
          }
          if (ok) { origInfo = { startIdx: 0, startVal: first }; updateOrigDisplay(); return; }
        }
      }
      // 2) 启发式：前 4 页边缘数字 → DFS 找差=1 递增段（论文页码连续是常态）
      const seq = [];
      for (let p = 1; p <= Math.min(pdfDoc.numPages, 4); p++) {
        const page = await pdfDoc.getPage(p);
        const tc = await page.getTextContent();
        seq.push(pickPageLabelCandidates(tc, page.getViewport({ scale: 1 }).height));
      }
      let best = null;
      const dfs = (idx, prevN, chain) => {
        if (idx >= seq.length) {
          if (chain.length >= 3 && (!best || chain.length > best.length)) best = chain.slice();
          return;
        }
        if (!seq[idx]) { dfs(idx + 1, prevN, chain); return; }
        for (const c of seq[idx]) {
          if (prevN === null || c.n === prevN + 1) {
            chain.push({ pno: idx + 1, n: c.n });
            dfs(idx + 1, c.n, chain);
            chain.pop();
          }
        }
      };
      dfs(0, null, []);
      if (best) origInfo = { startIdx: best[0].pno - 1, startVal: best[0].n };
      updateOrigDisplay();
    } catch (e) {
      console.warn("[pdf-viewer] 原始页码检测失败:", e);
      origInfo = null;
    }
  }

  /** 「引用此段」：通过回调交给页面层（doc-detail.js 定义 window.__onPdfCite） */
  function attachCite() {
    if (ui.citeBtn) {
      ui.citeBtn.onclick = () => {
        const sel = window.getSelection();
        const text = sel ? sel.toString().trim() : "";
        const cb = window.__onPdfCite;
        if (cb && text && currentDocId) {
          cb({ docId: currentDocId, page: pageNum, snippet: text.slice(0, 500) });
        }
        if (sel) sel.removeAllRanges();
        hideCiteBar();
      };
    }
  }

  async function renderPage() {
    if (!pdfDoc || !ctx) return;
    if (renderTask) { renderTask.cancel(); renderTask = null; }
    const page = await pdfDoc.getPage(pageNum);
    const viewport = page.getViewport({ scale });
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    if (ui.pageLabel) ui.pageLabel.value = String(pageNum);
    if (ui.prevBtn) ui.prevBtn.disabled = pageNum <= 1;
    if (ui.nextBtn) ui.nextBtn.disabled = pageNum >= pdfDoc.numPages;
    updateOrigDisplay();
    // 清旧 textLayer
    if (textLayerDiv && textLayerDiv.parentNode) textLayerDiv.parentNode.removeChild(textLayerDiv);
    textLayerDiv = null;
    textLayer = null;
    renderTask = page.render({ canvasContext: ctx, viewport });
    try {
      await renderTask.promise;
    } catch (e) {
      if (e?.name !== "RenderingCancelledException") throw e;
    }
    // 叠加文本层（PDF.js 官方机制，真实文本可选中）——状态写入 pdfStatus 便于诊断
    try {
      const pdfjs = window.__pdfjsLib;
      const textContent = await page.getTextContent();
      const itemCount = (textContent && textContent.items ? textContent.items.length : 0);
      if (itemCount === 0) {
        setStatus("⚠ 该 PDF 页面无内嵌文本（可能是扫描件/图片型页面），无法选中文字", true);
        return;
      }
      textLayerDiv = document.createElement("div");
      textLayerDiv.className = "textLayer";
      // 3.x 要求容器显式设 --scale-factor = viewport.scale：
      // span 字体/定位靠它放大，缺省 1 → 字不放大 → 小字叠加在原文上
      textLayerDiv.style.setProperty("--scale-factor", String(scale));
      // 显式尺寸与 canvas 对齐（textLayer 绝对定位覆盖依赖容器尺寸）
      textLayerDiv.style.width = `${viewport.width}px`;
      textLayerDiv.style.height = `${viewport.height}px`;
      // 3.x UMD 无 TextLayer 类：对外暴露 renderTextLayer({textContentSource,container,viewport}) → 返回 task（带 .promise）
      const task = pdfjs.renderTextLayer({
        textContentSource: textContent,
        container: textLayerDiv,
        viewport,
      });
      wrapper.appendChild(textLayerDiv);
      await task.promise;
      const spans = textLayerDiv.querySelectorAll("span").length;
      setStatus(`共 ${pdfDoc.numPages} 页 · 文本层已加载（本页 ${spans} 段文字，可选中引用）`);
      // 原始页码自纠：当前页实际数字与理论偏移不符（期刊分节/页码重启）→ 失效隐藏，宁缺毋假
      maybeVerifyOrig(pageNum, textContent, viewport.height);
    } catch (e) {
      console.warn("[pdf-viewer] textLayer 失败:", e);
      if (textLayerDiv && textLayerDiv.parentNode) textLayerDiv.parentNode.removeChild(textLayerDiv);
      textLayerDiv = null;
      setStatus(`文本层加载失败：${e.message || e}`, true);
    }
  }

  function setScale(factor) {
    scale = Math.min(3, Math.max(0.5, scale * factor));
    if (pdfDoc) void renderPage();
  }

  function fitWidth() {
    if (!pdfDoc || !container) return;
    const cw = container.clientWidth - 24;
    pdfDoc.getPage(pageNum).then((page) => {
      const vp = page.getViewport({ scale: 1 });
      scale = Math.max(0.5, cw / vp.width);
      void renderPage();
    });
  }

  /** 跳转到指定物理页（速览卡章节地图锚点用），越界钳制 */
  function gotoPage(n) {
    if (!pdfDoc) return;
    const p = Math.max(1, Math.min(Number(n) || 1, pdfDoc.numPages));
    if (p !== pageNum) { pageNum = p; void renderPage(); }
  }

  function attachEvents() {
    if (ui.prevBtn) ui.prevBtn.onclick = () => { if (pageNum > 1) { pageNum--; void renderPage(); } };
    if (ui.nextBtn) ui.nextBtn.onclick = () => { if (pageNum < pdfDoc.numPages) { pageNum++; void renderPage(); } };
    if (ui.zoomInBtn) ui.zoomInBtn.onclick = () => setScale(1.2);
    if (ui.zoomOutBtn) ui.zoomOutBtn.onclick = () => setScale(1 / 1.2);
    if (ui.fitBtn) ui.fitBtn.onclick = fitWidth;
    if (ui.pageLabel) ui.pageLabel.onchange = () => {
      const n = parseInt(ui.pageLabel.value, 10);
      if (n >= 1 && n <= pdfDoc.numPages) { pageNum = n; void renderPage(); }
    };
    attachCite();
  }

  window.PdfViewer = {
    render: renderPdf,
    attachEvents,
    gotoPage,
  };
})();