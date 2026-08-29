// cr-plugin Client 半区（T9：引用链面板 + client-log-monitor 三件套；T10：草稿编辑器+建议面板；T12：拖拽分栏布局重构）
// ⚠ 注册 id 必须等于 package name（cr-plugin）——DSH 按包名组装 __DSH_BOOT__ 图行并期待同 id 注册
// 能力：右侧 shell.overlay 工作台面板（方案①：草稿+文献工作台+PDF 三合一）拽分栏：
//       左 = 草稿编辑器区（T10）；右 = 文献工作台区（上：子 Tab 容器[引用链/速览卡/检索/输入资料]，下：PDF 预览区占位 T13）
// 可观测：reportState 上报渲染/健康/错误 → host /cr-paper/client-state 回收（三件套，client-log-monitor 技能）
// 数据通道：cr-paper host 只读路由（bibliography/paper）+ 写代理（format/verify/add 白名单）——单一事实来源 = 管线
window.__ModuleLoader__.load({
  id: 'cr-plugin',
  factory: (require) => {
    var module = { exports: {} }
    var exports = module.exports
    var React = require('react')
    // ── 可读性放大（路线一：默认字号全局上浮 1.15x；ff() 把 px 值缩放为字符串）──
    var WB_FONT_SCALE = 1.15
    function ff(px) { return Math.round(px * WB_FONT_SCALE) + 'px' }

    // ---- client-log-monitor 三件套：reportState（fire-and-forget，失败绝不影响渲染）----
    function reportState(report) {
      try {
        fetch('/cr-paper/client-state?report=' + encodeURIComponent(JSON.stringify(report)), { cache: 'no-store' }).catch(function () { })
      } catch (e) { }
    }
    // ── T16 引文登记：复制捕获槽 + 匹配纯函数（移植老库 pdfPreviewHandle.ts，零依赖）──
    // 捕获槽唯一、新复制覆盖旧、10 分钟窗、规范化匹配；粘贴 <5 字不算
    var COPY_CITE_WINDOW_MS = 10 * 60 * 1000
    var copyCapture = null   // { docId, page, text, ts, source: 'doc'|'input' }
    function recordCopyCapture(c) {
      if (!c) return
      // 幂等：ts+text 相同视为同一来源，不重复覆盖
      if (copyCapture && copyCapture.ts === c.ts && copyCapture.text === c.text) return
      copyCapture = c
      reportState({ event: 'capture-recorded', docId: c.docId || '', page: c.page || 0, source: c.source || 'doc', len: (c.text || '').length })
    }
    function getCopyCapture() { return copyCapture }
    function normalizeCiteText(s) {
      return String(s || '').replace(/[\s\u3000\u00a0]+/g, '')
    }
    function matchCopyCapture(cap, pasted, now) {
      if (!cap) return false
      if (now - cap.ts > COPY_CITE_WINDOW_MS) return false
      var p = normalizeCiteText(pasted)
      if (p.length < 5) return false
      var c = normalizeCiteText(cap.text)
      if (!c) return false
      return c.indexOf(p) >= 0 || p.indexOf(c) === 0
    }
    // 来源标题解析（确认弹窗显示）：bibliography.title → source_file → docId
    function resolveDocTitle(biblioItems, docId, sourceFile) {
      if (biblioItems && docId) {
        var it = biblioItems.filter(function (i) { return i.doc_id === docId })[0]
        if (it && it.bibliography && it.bibliography.title) return it.bibliography.title
      }
      if (sourceFile) return sourceFile.replace(/\.(pdf|md|txt|docx|png|jpg|jpeg)$/i, '')
      return docId || '未知来源'
    }
    // 捕获文本截断（1000 字上限；与 src/domain/capture.js sliceCaptureText 一致，由 capture.test.js 语义守卫校验）
    var CAPTURE_TEXT_LIMIT = 1000
    function sliceCaptureText(text) {
      return String(text || '').slice(0, CAPTURE_TEXT_LIMIT)
    }
    // ── T16 引文登记：草稿框粘贴确认（U2 通道）──
    // 命中 → 浮卡「📎 是否确认引用？」→ 确认登记+插 [N] / 仅粘贴落原文
    var citeConfirmStore = {
      visible: false,
      capture: null,     // CopyCapture
      pasted: '',        // 粘贴文本（保留原文，仅粘贴时落草稿）
      busy: false,
      error: '',
      listeners: [],
      get: function () { return this },
      set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
      subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
    }
    function useCiteConfirm() {
      var t = React.useState(0)
      React.useEffect(function () { return citeConfirmStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
      return citeConfirmStore
    }
    // ── T16 U3：会话框来源 chip（composer.dock 附加；复制即提示，不弹窗不打断）──
    var citeChipStore = {
      visible: false,
      docId: '',
      title: '',      // 来源标题
      page: 0,
      snippet: '',
      listeners: [],
      get: function () { return this },
      set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
      subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
    }
    function useCiteChip() {
      var t = React.useState(0)
      React.useEffect(function () { return citeChipStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
      return citeChipStore
    }
    // 显示 chip（选区复制后调用；title 从引用清单/文档解析）
    function showCiteChip(cap, title) {
      citeChipStore.set({
        visible: true,
        docId: cap.docId || '',
        title: title || cap.docId || '未知来源',
        page: cap.page || 0,
        snippet: String(cap.text || '').slice(0, 200),
      })
      reportState({ event: 'chip-attached', docId: cap.docId || '', page: cap.page || 0, len: String(cap.text || '').length })
    }
    function hideCiteChip() {
      citeChipStore.set({ visible: false, docId: '', title: '', page: 0, snippet: '' })
      reportState({ event: 'chip-removed' })
    }
    // 草稿框粘贴处理：命中捕获 → 拦截 + 显示浮卡；不命中 → 放行（裸粘贴）
    function jsonGet(url) {
      return fetch(url, { cache: 'no-store' }).then(function (r) { return r.json() })
    }
    function jsonPost(url, body, method, timeoutMs) {
      var m = method || 'POST'
      var p = fetch(url, {
        method: m,
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify(body || {}),
        cache: 'no-store',
      }).then(function (r) { return r.json() }).catch(function (e) { console.log('[cr-paper][T16] jsonPost ERR:', e && e.message || e); throw e })
      var t = timeoutMs || 0
      if (t > 0) {
        return Promise.race([p, new Promise(function (_, reject) {
          setTimeout(function () { reject(new Error('请求超时(' + t + 'ms)')) }, t)
        })])
      }
      return p
    }
    // 引用格式选项（与老库 CitationsPanel 对齐）
    var FORMATS = ['GB/T 7714-2015', 'APA', 'MLA', 'Chicago']
    // 通用小按钮样式
    var btnStyle = { fontSize: ff(12), padding: '3px 10px', border: '1px solid #ccc', borderRadius: '4px', background: '#fff', cursor: 'pointer' }

    var plugin = {
      apply(ctx) {
        var slots = ctx.get('slots')
        if (slots === undefined) return

        // 注册健康上报（三件套第 3 项）：会话区 Tab 已移除（2026-08-16 方案①），面板为唯一工作台入口
        try {
          var occ = slots.entries('conversation.view')
          reportState({ event: 'apply', viewOccupants: (occ || []).map(function (e) { return e.id || e.registrant || 'unknown' }), ready: true })
        } catch (e) { reportState({ event: 'apply-error', error: String(e).slice(0, 200) }) }
        // ── 引用链面板数据层（组件外 store，跨渲染共享 paper 选择）──
        var store = {
          papers: [],
          paperId: '',
          items: [],
          format: FORMATS[0],
          status: '',
          loading: false,
          listeners: [],
          get: function () { return this },
          set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
          subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
        }
        function useStore() {
          var t = React.useState(0)
          React.useEffect(function () { return store.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
          return store
        }
        // 拉取项目列表（cr-paper 只读 papers 路由）
        function loadPapers() {
          jsonGet('/cr-paper/papers').then(function (r) {
            var list = (r && r.papers) || []
            store.set({ papers: list })
            if (list.length && !store.paperId) { selectPaper(list[0].id) }
            else if (store.paperId) { refreshBiblio() }
            reportState({ event: 'papers-loaded', count: list.length })
          }).catch(function (e) {
            store.set({ status: '项目列表加载失败: ' + String(e).slice(0, 120) })
            reportState({ event: 'papers-error', error: String(e).slice(0, 200) })
          })
        }
        // 选择项目 → 拉取清单
        function selectPaper(id) {
          store.set({ paperId: id })
          // 项目切换联动：重置草稿当前态并加载对应项目草稿（F9）——
          // 否则 loadDrafts 的 `if (!draftStore.current)` 因旧值残留而不更新，切换后草稿编辑器停留旧项目草稿。
          draftStore.set({ suggest: { items: [], visible: false, polling: false, busy: false }, current: '', content: '', version: 0, versions: [], timelineOpen: false, dirty: false })
          loadDrafts()
          refreshBiblio()
        }
        // 新建论文项目（B：输入论文名 → ①管线建项目(空模板草稿) ②建论文工作区目录+注册 DSH 工作区）
        // 之后用户在左侧点这个论文工作区 → DSH 原生新会话 → auto-open 自动弹工作台加载该论文空模板。
        function createPaper(rawTitle) {
          // 外行友好：默认经侧边栏内嵌表单调用（已填 title）；兜底保留直接传参（不弹浏览器 prompt——那种开发者思维弹窗对外行突兀）。
          var t = (typeof rawTitle === 'string' && rawTitle.trim()) ? rawTitle.trim() : ''
          if (!t) { store.set({ status: '请先填写论文题目' }); reportState({ event: 'create-paper-no-title' }); return }
          store.set({ status: '正在创建论文工作区…' })
          reportState({ event: 'paper-create-start', title: t.slice(0, 60) })
          // ① 管线建项目（自动带空模板草稿 + 引用清单 + v1）
          fetch('/cr-paper/papers', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: t }), cache: 'no-store',
          }).then(function (r) { return r.json().catch(function () { return { error: '响应解析失败' } }) })
            .then(function (d) {
              if (!d || !d.ok) { store.set({ status: '创建失败: ' + ((d && d.error) || '未知') }); reportState({ event: 'paper-create-error', error: String((d && d.error) || '').slice(0, 200) }); return }
              reportState({ event: 'paper-create-done', paperId: d.paper_id })
              // ── 修复：新建论文后立即把工作台切到该论文并打开，让草稿编辑器显示其引导草稿。
              // 病因：此前 createPaper 从不 selectPaper(newPaperId)，工作台停留在旧项目（如 demo 预设的旧空模板），
              // 新建论文的 8 步引导词永不显示。前置：POST /papers 已写入引导草稿（_DRAFT_TEMPLATE）。
              setDetailsView('workbench')
              selectPaper(d.paper_id)
              openWorkbench()
              reportState({ event: 'create-paper-select', paperId: d.paper_id })
              // ② 建论文工作区目录 + 注册 DSH 工作区（host）
              fetch('/cr-paper/create-workspace', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: t, paper_id: d.paper_id }), cache: 'no-store' })
                .then(function (r2) { return r2.json().catch(function () { return { error: '响应解析失败' } }) })
                .then(function (w) {
                  if (w && w.ok) {
                    store.set({ status: '已建论文工作区（' + t + '）' })
                    reportState({ event: 'paper-workspace-created', workspacePath: w.workspacePath, workspaceId: w.workspaceId || null, paperId: d.paper_id })
                    // ③ 用脚本新建/连接该工作区的会话（这样会话固化、cwd=论文工作区 → auto-open 能识别并弹工作台加载论文）
                    if (w.workspaceId) {
                      try {
                        var wsSvc = (ctx.get && ctx.get('workspaces')) || ctx.workspaces || null
                        var sessSvc = (ctx.get && ctx.get('sessions')) || ctx.sessions || null
                        reportState({ event: 'paper-ws-services', wsMethods: wsSvc ? Object.keys(wsSvc).slice(0, 16) : null, sessMethods: sessSvc ? Object.keys(sessSvc).slice(0, 16) : null, wsId: w.workspaceId })
                        if (wsSvc && typeof wsSvc.connectWorkspace === 'function') {
                          wsSvc.connectWorkspace(w.workspaceId).then(function (sid) {
                            reportState({ event: 'paper-session-created', sessionId: sid })
                            store.set({ status: '已在论文工作区新建会话，正在打开…' })
                            if (sessSvc && typeof sessSvc.open === 'function') sessSvc.open(sid)
                          }).catch(function (e) { reportState({ event: 'paper-session-error', error: String(e).slice(0, 160) }) })
                        } else {
                          reportState({ event: 'paper-ws-skip', reason: 'cr-plugin ctx 无 connectWorkspace' })
                        }
                      } catch (e) { reportState({ event: 'paper-session-error', error: String(e).slice(0, 160) }) }
                    }
                  } else {
                    store.set({ status: '论文项目已建，但工作区登记失败: ' + ((w && (w.error || w.message)) || '未知') })
                    reportState({ event: 'paper-workspace-error', error: String((w && (w.error || w.message)) || '').slice(0, 200) })
                  }
                })
                .catch(function (e) { store.set({ status: '论文项目已建，但工作区登记异常: ' + String(e).slice(0, 120) }); reportState({ event: 'paper-workspace-error', error: String(e).slice(0, 200) }) })
            })
            .catch(function (e) { store.set({ status: '创建异常: ' + String(e).slice(0, 120) }); reportState({ event: 'paper-create-error', error: String(e).slice(0, 200) }) })
        }
        function refreshBiblio() {
          if (!store.paperId) return
          store.set({ loading: true })
          jsonGet('/cr-paper/bibliography?id=' + encodeURIComponent(store.paperId)).then(function (r) {
            store.set({ items: (r && r.items) || [], format: (r && r.format) || FORMATS[0], loading: false, status: '' })
            reportState({ event: 'biblio-loaded', paperId: store.paperId, count: ((r && r.items) || []).length })
          }).catch(function (e) {
            store.set({ loading: false, status: '清单加载失败: ' + String(e).slice(0, 120) })
            reportState({ event: 'biblio-error', error: String(e).slice(0, 200) })
          })
        }
        // 格式切换（写代理：POST bibliography/format）
        function switchFormat(fmt) {
          if (!store.paperId) return
          store.set({ format: fmt })
          jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/bibliography/format', { format: fmt }).then(function (r) {
            if (r && r.ok === false) { store.set({ status: '格式切换失败: ' + (r.reason || '未知') }) }
            else refreshBiblio()
            reportState({ event: 'format-switch', format: fmt, ok: !(r && r.ok === false) })
          }).catch(function (e) { store.set({ status: '格式切换异常: ' + String(e).slice(0, 120) }); reportState({ event: 'format-error', error: String(e).slice(0, 200) }) })
        }
        // 核验（写代理：POST bibliography/verify）
        function verifyItem(seq) {
          if (!store.paperId) return
          var claim = window.prompt('粘贴你在论文中写的论点（claim），核验引用 #' + seq + '：')
          if (!claim) return
          store.set({ status: '核验中（云端/本地 10-60s）...' })
          jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/bibliography/verify', { seq, claim }).then(function (r) {
            var v = r && r.verified
            if (v && v.verdict) {
              var label = v.verdict === 'supported' ? '✅ 支持' : v.verdict === 'not_supported' ? '⚠️ 不支持' : '❓ 不确定'
              window.alert('核验结果：' + label + '（置信度 ' + Math.round((v.confidence || 0) * 100) + '%）')
              store.set({ status: '' })
            } else if (r && r.error) { store.set({ status: '核验失败: ' + r.error }) }
            else { store.set({ status: '' }) }
            reportState({ event: 'verify-done', seq, verdict: (v && v.verdict) || null })
            refreshBiblio()
          }).catch(function (e) { store.set({ status: '核验异常: ' + String(e).slice(0, 120) }); reportState({ event: 'verify-error', error: String(e).slice(0, 200) }) })
        }
        // 插入草稿：从引用清单插 [N] 到草稿编辑器光标处（T10 宿主已就位）
        function insertMarkToEditor(seq) {
          if (!draftStore.current) { store.set({ status: '请先选择草稿（草稿编辑器）再插入引用标记' }); return }
          var ta = document.getElementById('cr-wb-panel-draft-ta')
          // 点击「插入草稿」按钮会让 textarea 失焦、selectionStart 不可靠；优先用失焦前缓存的
          // draftStore.cursor，其次现场读 ta.selectionStart，最后回退文末。
          var start = (typeof draftStore.cursor === 'number' && draftStore.cursor >= 0)
            ? draftStore.cursor
            : (ta ? ta.selectionStart : (draftStore.content || '').length)
          var end = (ta && start === ta.selectionStart) ? ta.selectionEnd : start
          var mark = '[' + seq + ']'
          var content = (draftStore.content || '').slice(0, start) + mark + (draftStore.content || '').slice(end)
          draftStore.set({ content: content, dirty: true, cursor: start + mark.length })
          store.set({ status: '已在光标处插入引用标记 [' + seq + ']（记得保存）' })
          reportState({ event: 'insert-mark', seq })
        }
        // 页码回跳（T13：真实跳页——切 pdf 视图 + 打开面板 + 设置 pdfStore；加载由 PdfPreview 组件发起）
        function jumpToPage(docId, page) {
          reportState({ event: 'jump-page', docId, page, placeholder: false })
          setDetailsView('workbench')
          openWorkbench()
          var target = (Number(page) > 0) ? Number(page) : 1
          pdfStore.set({ docId: docId, page: target, captureSlot: null })
          reportState({ event: 'jump-page-applied', docId, page: target })
        }

        // ── T10 草稿编辑器 + 疑似引用建议（数据层）──
        // 草稿 store：drafts 列表 / current(逻辑名) / content / version / versions / timelineOpen
        var draftStore = {
          drafts: [],
          current: '',
          content: '',
          version: 0,
          versions: [],
          timelineOpen: false,
          dirty: false,
          saving: false,
          // 建议面板：items 非空才 visible；polling 防重入
          suggest: { items: [], visible: false, polling: false, busy: false },
          // 光标位置（插入 [N] 用；点击「插入草稿」按钮会使 textarea 失焦、selectionStart 不可靠，
          // 故在 onSelect/onMouseUp 时缓存到 draftStore.cursor）
          cursor: null,
          listeners: [],
          get: function () { return this },
          set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
          subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
        }
        function useDraftStore() {
          var t = React.useState(0)
          React.useEffect(function () { return draftStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
          return draftStore
        }
        // ── T15 输入资料面板数据层（inputsStore：列表/选中/预览/操作状态）──
        var inputsStore = {
          list: [],
          selected: null,       // { name, size, updated_at }
          ingMap: {},           // 监控×输入资料联动：name → 'ing'(入库中) | 'done'(已入库)
          preview: '',          // 文本预览内容（md/txt/csv）
          previewBlob: '',      // 图片预览 blob URL
          previewType: '',      // 'text' | 'image' | 'pdf' | 'other'
          loading: false,
          busy: false,
          error: '',
          listeners: [],
          get: function () { return this },
          set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
          subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
        }
        function useInputsStore() {
          var t = React.useState(0)
          React.useEffect(function () { return inputsStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
          return inputsStore
        }
        // ── T14 速览卡数据层（digestStore：文档列表/选中/卡片/轮询状态）──
        var digestStore = {
          docs: [],          // GET /docs 列表（数组）
          docsLoading: false,
          docId: '',         // 当前选中文档
          docLabel: '',      // 下拉显示名（title || source_file）
          card: null,        // 速览卡内容（含 core_points/sections/key_concepts）
          status: 'idle',    // 'idle' | 'loading' | 'processing' | 'done' | 'error'
          error: '',
          pollCount: 0,
          listeners: [],
          get: function () { return this },
          set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
          subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
        }
        function useDigestStore() {
          var t = React.useState(0)
          React.useEffect(function () { return digestStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
          return digestStore
        }
        // ── T14 检索数据层（searchStore：本地/联网双标签 + 收藏勾选）──
        var searchStore = {
          mode: 'local',        // 'local' | 'web'
          localQuery: '',
          webQuery: '',
          channel: 'zh',        // 'zh' 中文文献 | 'intl' 国际文献（对齐老库双标签，默认中文）
          localResults: [],
          localLoading: false,
          localError: '',
          localQueryEn: null,
          webResults: [],
          webLoading: false,
          webError: '',
          webHint: '',
          webChannel: '',
          webSources: [],
          webSearchDate: '',
          webDisclaimer: '',    // 来源声明行（T6 统一 schema）
          srcFilter: {},        // 来源筛选态：source → true/false（默认全选；false = 关闭）
          checked: {},          // 勾选收藏：key = url + title → item
          saving: false,
          saveMsg: '',
          oaState: {},          // OA 下载入库状态：key = url+title → 状态文案
          editedEn: '',         // 「已翻译为」可编辑框（用户改过即按改后词检索）
          lastWebQuery: '',     // 上次检索中文原词（换词 → 重新翻译）
          translationMethod: '',
          translationHint: '',
          listeners: [],
          get: function () { return this },
          set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
          subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
        }
        function useSearchStore() {
          var t = React.useState(0)
          React.useEffect(function () { return searchStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
          return searchStore
        }
        // ── T14 收藏清单数据层（collectionStore：列表/详情/操作状态）──
        var collectionStore = {
          list: [],             // [{name, query, channel, created_at, count}]
          loading: false,
          detailName: '',       // 展开查看的清单名（'' = 不展开）
          detail: null,         // 详情（含 items）
          csvPreview: null,     // CSV 表格预览（行数组：第一行表头）
          csvLoading: false,
          csvError: '',
          busy: false,
          error: '',
          msg: '',
          listeners: [],
          get: function () { return this },
          set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
          subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
        }
        function useCollectionStore() {
          var t = React.useState(0)
          React.useEffect(function () { return collectionStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
          return collectionStore
        }
        // ── 分析后端（本地/云端/完全本地）数据层：面板顶栏切换，影响速览卡提炼/引用核验/联网检索 ──
        var backendStore = {
          mode: '',        // 'local' | 'cloud' | 'off' | ''（未加载）
          provider: '',
          message: '',
          loading: false,
          menuOpen: false, // 切换菜单展开
          listeners: [],
          get: function () { return this },
          set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
          subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
        }
        function useBackendStore() {
          var t = React.useState(0)
          React.useEffect(function () { return backendStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
          return backendStore
        }
        var BACKEND_LABEL = { local: '💻 本地', cloud: '☁️ 云端', off: '🔌 完全本地' }
        // 查询当前后端（POST /provider 无 body → 返回当前 mode）
        function loadBackend() {
          if (backendStore.loading) return
          backendStore.set({ loading: true })
          jsonPost('/cr-paper/pipe/provider', {}, 'POST', 8000).then(function (r) {
            backendStore.set({ loading: false, mode: (r && r.mode) || '', provider: (r && r.provider) || '', message: (r && r.message) || '' })
            reportState({ event: 'backend-loaded', mode: (r && r.mode) || '', provider: (r && r.provider) || '' })
          }).catch(function (e) {
            backendStore.set({ loading: false })
            reportState({ event: 'backend-error', error: String(e && e.message || e).slice(0, 120) })
          })
        }
        // 切换后端（local/cloud/off）
        function setBackendMode(mode) {
          if (mode === backendStore.mode) { backendStore.set({ menuOpen: false }); return }
          backendStore.set({ loading: true, menuOpen: false })
          reportState({ event: 'backend-switch-start', mode: mode })
          jsonPost('/cr-paper/pipe/provider', { mode: mode }, 'POST', 10000).then(function (r) {
            backendStore.set({ loading: false, mode: (r && r.mode) || mode, provider: (r && r.provider) || '', message: (r && r.message) || '' })
            reportState({ event: 'backend-switched', mode: (r && r.mode) || mode, provider: (r && r.provider) || '', msg: ((r && r.message) || '').slice(0, 200) })
            // 切换影响后续生成/检索；若正在看速览卡，提示用户重生成
          }).catch(function (e) {
            backendStore.set({ loading: false, menuOpen: false })
            reportState({ event: 'backend-switch-error', error: String(e && e.message || e).slice(0, 120) })
          })
        }
        // ── 入库监控数据层（monitorStore：/status 5s 轮询，阶段进度 + 队列徽标）──
        var monitorStore = {
          data: null,       // /status 响应
          loading: false,
          error: '',
          updatedAt: 0,
          listeners: [],
          get: function () { return this },
          set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
          subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
        }
        function useMonitorStore() {
          var t = React.useState(0)
          React.useEffect(function () { return monitorStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
          return monitorStore
        }
        // 拉取管线状态（/cr-paper/status 透传；5s 轮询由 MonitorBody 驱动）
        function loadStatus() {
          jsonGet('/cr-paper/status').then(function (r) {
            monitorStore.set({ data: r, loading: false, error: '', updatedAt: Date.now() })
            var qs = (r && r.queues) || {}
            var qsum = 0
            for (var k in qs) qsum += Number(qs[k] || 0)
            reportState({ event: 'monitor-status', docs: (r && r.docs_total) || 0, chunks: (r && r.chunks_total) || 0, queueSum: qsum, stages: r && r.stages ? Object.keys(r.stages).length : 0 })
          }).catch(function (e) {
            monitorStore.set({ loading: false, error: '状态拉取失败: ' + String(e && e.message || e).slice(0, 120) })
            reportState({ event: 'monitor-error', error: String(e && e.message || e).slice(0, 160) })
          })
        }
        // T14 文档列表（GET /docs 返回数组，兼容 {docs} 包装）
        function loadDocList() {
          digestStore.set({ docsLoading: true })
          jsonGet('/cr-paper/pipe/docs').then(function (r) {
            var list = Array.isArray(r) ? r : ((r && r.docs) || [])
            digestStore.set({ docs: list, docsLoading: false })
            reportState({ event: 'digest-docs-loaded', count: list.length })
            // 默认选中文档（有 title 优先；无则第一个）
            if (list.length && !digestStore.docId) {
              var first = list[0]
              openDigest(first.doc_id, first.title || first.source_file || '')
            }
          }).catch(function (e) {
            digestStore.set({ docsLoading: false, error: '文档列表加载失败: ' + String(e).slice(0, 120) })
            reportState({ event: 'digest-error', error: String(e).slice(0, 200) })
          })
        }
        // 打开速览卡：GET digest → 缓存直接显示 / processing 轮询 / 未缓存自动触发生成
        function openDigest(docId, label, force) {
          if (!docId) return
          digestStore.set({ docId: docId, docLabel: label || docId, status: 'loading', card: null, error: '', pollCount: 0 })
          reportState({ event: 'digest-open', docId: docId, force: !!force })
          var url = '/cr-paper/pipe/docs/' + encodeURIComponent(docId) + '/digest' + (force ? '?force=1' : '')
          jsonGet(url).then(function (d) {
            if (!d) { digestStore.set({ status: 'error', error: '无响应' }); return }
            if (d.status === 'processing') {
              digestStore.set({ status: 'processing', error: '' })
              reportState({ event: 'digest-processing', docId: docId })
              pollDigest(docId, 0)
              return
            }
            if (d.core_points || d.sections) {
              digestStore.set({ card: d, status: 'done', error: '' })
              reportState({ event: 'digest-loaded', docId: docId, points: (d.core_points || []).length, sections: (d.sections || []).length })
              return
            }
            digestStore.set({ status: 'error', error: '无法生成速览卡（响应异常）' })
            reportState({ event: 'digest-error', docId: docId, error: 'unexpected-response' })
          }).catch(function (e) {
            var is404 = /404|未找到|过短|尚未完成入库/i.test(String(e && e.message || e))
            digestStore.set({ status: 'error', error: is404 ? '该文档尚未完成入库或文本过短，无法生成速览卡。' : ('速览卡读取失败: ' + String(e && e.message || e).slice(0, 120)) })
            reportState({ event: 'digest-error', docId: docId, error: String(e && e.message || e).slice(0, 160) })
          })
        }
        // 轮询 digest 进度（2s 间隔；done/idle 后重取卡片；上限 90 次 ≈ 3 分钟）
        function pollDigest(docId, n) {
          if (n >= 90) { digestStore.set({ status: 'error', error: '速览卡生成超时（3 分钟），请稍后重试或点「重新生成」。' }); reportState({ event: 'digest-timeout', docId: docId }); return }
          digestStore.set({ pollCount: n + 1 })
          jsonGet('/cr-paper/pipe/docs/' + encodeURIComponent(docId) + '/digest/progress').then(function (p) {
            var st = p && p.status
            if (st === 'processing') {
              setTimeout(function () { pollDigest(docId, n + 1) }, 2000)
              return
            }
            // done / idle / error：重取卡片（idle 表示无 job 无缓存 → digest 端点会触发生成）
            jsonGet('/cr-paper/pipe/docs/' + encodeURIComponent(docId) + '/digest').then(function (d) {
              if (d && (d.core_points || d.sections)) {
                digestStore.set({ card: d, status: 'done', error: '' })
                reportState({ event: 'digest-loaded', docId: docId, via: 'poll', points: (d.core_points || []).length })
              } else if (d && d.status === 'processing') {
                setTimeout(function () { pollDigest(docId, n + 1) }, 2000)
              } else {
                digestStore.set({ status: 'error', error: '速览卡生成失败' })
                reportState({ event: 'digest-error', docId: docId, error: 'poll-failed', p: st })
              }
            }).catch(function (e) {
              digestStore.set({ status: 'error', error: '速览卡读取失败: ' + String(e && e.message || e).slice(0, 120) })
              reportState({ event: 'digest-error', docId: docId, error: String(e && e.message || e).slice(0, 160) })
            })
          }).catch(function (e) {
            digestStore.set({ status: 'error', error: '进度查询失败: ' + String(e && e.message || e).slice(0, 120) })
            reportState({ event: 'digest-error', docId: docId, error: String(e && e.message || e).slice(0, 160) })
          })
        }
        // 本地检索：POST /search（向量库 topN；is_project_input 徽标对照）
        function runLocalSearch() {
          var q = (searchStore.localQuery || '').trim()
          if (!q) return
          searchStore.set({ localLoading: true, localError: '', localResults: [], localQueryEn: null })
          reportState({ event: 'search-local-start', queryLen: q.length })
          jsonPost('/cr-paper/pipe/search', { query: q, top_k: 8, paper_id: store.paperId || null }, 'POST', 30000).then(function (r) {
            if (r && r.error) {
              searchStore.set({ localLoading: false, localError: r.error })
              reportState({ event: 'search-local-error', error: String(r.error).slice(0, 200) })
              return
            }
            var results = (r && r.results) || []
            searchStore.set({ localLoading: false, localResults: results, localQueryEn: r && r.query_en || null })
            reportState({ event: 'search-local-done', count: results.length, queryEn: (r && r.query_en) || null })
          }).catch(function (e) {
            searchStore.set({ localLoading: false, localError: '本地检索失败: ' + String(e && e.message || e).slice(0, 160) })
            reportState({ event: 'search-local-error', error: String(e && e.message || e).slice(0, 200) })
          })
        }
        // 联网检索：POST /web/search（统一 schema；channel=zh/intl 对齐老库双标签）
        function runWebSearch() {
          var q = (searchStore.webQuery || '').trim()
          if (!q) return
          searchStore.set({ webLoading: true, webError: '', webResults: [], webHint: '', checked: {}, saveMsg: '', oaState: {} })
          reportState({ event: 'search-web-start', queryLen: q.length, channel: searchStore.channel })
          // 换词 → 清旧翻译词由服务端走英译降级链；用户改过「已翻译为」→ 按改后词检索
          if (q !== searchStore.lastWebQuery) { searchStore.set({ editedEn: '', lastWebQuery: q }) }
          var body = { query: q, max_results: 8, channel: searchStore.channel }
          if (searchStore.editedEn) body.query_en = searchStore.editedEn
          jsonPost('/cr-paper/pipe/web/search', body, 'POST', 45000).then(function (r) {
            if (r && r.ok === false) {
              searchStore.set({ webLoading: false, webError: (r && r.message) || '联网检索不可用' })
              reportState({ event: 'search-web-disabled', error: String(r.message || '').slice(0, 200) })
              return
            }
            if (r && r.error) {
              searchStore.set({ webLoading: false, webError: r.error })
              reportState({ event: 'search-web-error', error: String(r.error).slice(0, 200) })
              return
            }
            var results = (r && r.results) || []
            var sources = (r && r.sources) || []
            // 来源筛选初始化（默认全选）
            var filt = {}
            for (var i = 0; i < sources.length; i++) filt[sources[i]] = true
            searchStore.set({
              webLoading: false, webResults: results, webError: '',
              webHint: (r && r.empty_hint) || '', webChannel: (r && r.channel) || '',
              webSources: sources, webSearchDate: (r && r.search_date) || '',
              webDisclaimer: (r && r.disclaimer) || '',
              srcFilter: filt,
              editedEn: searchStore.editedEn || (r && r.query_en) || '',
              translationMethod: (r && r.translation_method) || 'none',
              translationHint: (r && r.translation_hint) || '',
            })
            reportState({ event: 'search-web-done', count: results.length, sources: sources, queryEn: (r && r.query_en) || null })
          }).catch(function (e) {
            searchStore.set({ webLoading: false, webError: '联网检索失败: ' + String(e && e.message || e).slice(0, 160) })
            reportState({ event: 'search-web-error', error: String(e && e.message || e).slice(0, 200) })
          })
        }
        // 勾选切换（key = url + title，对齐老库 oaKey）
        function checkedKey(r) { return String(r.url || '') + String(r.title || '') }
        function toggleChecked(key, item) {
          var next = Object.assign({}, searchStore.checked)
          if (next[key]) delete next[key]
          else next[key] = item
          searchStore.set({ checked: next })
        }
        // 全选/取消当前来源筛选下的全部结果（对齐老库 pickAllShown）
        function pickAllShown() {
          var shown = shownWebResults()
          var allOn = shown.length > 0 && shown.every(function (r) { return searchStore.checked[checkedKey(r)] })
          var next = Object.assign({}, searchStore.checked)
          for (var i = 0; i < shown.length; i++) {
            var k = checkedKey(shown[i])
            if (allOn) delete next[k]
            else next[k] = shown[i]
          }
          searchStore.set({ checked: next })
          reportState({ event: 'search-pick-all', count: shown.length, allOn: allOn })
        }
        // 当前筛选下可见结果（对齐老库 shownWebResults）
        function shownWebResults() {
          return searchStore.webResults.filter(function (r) { return searchStore.srcFilter[r.source] !== false })
        }
        // OA 一键下载入库（对齐老库 oaIngest：仅 oa_url 命中可点）
        function oaIngest(r) {
          var key = checkedKey(r)
          if (!r || !r.oa_url) return
          searchStore.set({ oaState: Object.assign({}, searchStore.oaState, { [key]: '下载中…' }) })
          reportState({ event: 'oa-ingest-start', titleLen: (r.title || '').length, urlLen: (r.oa_url || '').length })
          jsonPost('/cr-paper/pipe/web/oa_ingest', {
            url: r.oa_url, title: r.title || '', authors: r.authors || [], journal: r.journal || null,
            year: r.year || null, doi: r.doi || null, source: r.source || '', search_date: searchStore.webSearchDate,
          }, 'POST', 90000).then(function (d) {
            var msg
            if (d && d.ok === false) msg = '❌ ' + (d.message || '入库失败')
            else if (d && d.duplicate) msg = '✓ 已在库（内容判重，未重复入库）'
            else if (d && d.ok) msg = '✅ 已提交入库，数秒后本地可检索'
            else msg = '❌ 入库失败: ' + (d && d.error || '未知')
            searchStore.set({ oaState: Object.assign({}, searchStore.oaState, { [key]: msg }) })
            reportState({ event: 'oa-ingest-done', ok: !!(d && d.ok), duplicate: !!(d && d.duplicate), titleLen: (r.title || '').length })
            if (d && d.ok && !d.duplicate) loadDocList() // 新文献入库 → 速览卡文档列表刷新
          }).catch(function (e) {
            searchStore.set({ oaState: Object.assign({}, searchStore.oaState, { [key]: '❌ ' + String(e && e.message || e).slice(0, 80) }) })
            reportState({ event: 'oa-ingest-error', error: String(e && e.message || e).slice(0, 200) })
          })
        }
        // 复制单条引用信息（对齐老库 copyCiteInfo：粘贴去知网等渠道检索下载）
        function copyCiteInfo(it) {
          var parts = [
            '标题：' + (it.title || ''),
            (it.authors && it.authors.length) ? '作者：' + it.authors.join(', ') : '',
            it.journal ? '期刊：' + it.journal : '',
            it.year ? '年份：' + it.year : '',
            it.doi ? 'DOI：' + it.doi : '',
            it.url ? '链接：' + it.url : '',
          ].filter(Boolean).join('\n')
          if (!parts) { collectionStore.set({ msg: '无可复制内容' }); return }
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(parts).then(function () { collectionStore.set({ msg: '✅ 引用信息已复制' }) }).catch(function () { collectionStore.set({ msg: '复制失败（剪贴板不可用）' }) })
          } else { collectionStore.set({ msg: '复制失败（剪贴板不可用）' }) }
          reportState({ event: 'collection-copy-cite', titleLen: (it.title || '').length })
        }
        // 保存勾选为收藏清单（POST /web/collections）
        function saveCollection() {
          var keys = Object.keys(searchStore.checked)
          if (!keys.length) return
          var items = keys.map(function (k) { return searchStore.checked[k] })
          searchStore.set({ saving: true, saveMsg: '' })
          reportState({ event: 'collection-save-start', count: items.length })
          jsonPost('/cr-paper/pipe/web/collections', {
            items: items,
            query: searchStore.webQuery,
            channel: searchStore.webChannel,
            search_date: searchStore.webSearchDate,
          }, 'POST', 15000).then(function (r) {
            searchStore.set({ saving: false, saveMsg: (r && r.ok) ? ('已保存清单「' + (r.name || '') + '」（' + (r.count || 0) + ' 条）') : ('保存失败: ' + (r && r.error || '未知')) })
            reportState({ event: 'collection-saved', ok: !!(r && r.ok), name: (r && r.name) || '', count: (r && r.count) || 0 })
            if (r && r.ok) { searchStore.set({ checked: {} }); loadCollections() }
          }).catch(function (e) {
            searchStore.set({ saving: false, saveMsg: '保存异常: ' + String(e && e.message || e).slice(0, 120) })
            reportState({ event: 'collection-error', error: String(e && e.message || e).slice(0, 200) })
          })
        }
        // 收藏清单列表
        function loadCollections() {
          collectionStore.set({ loading: true, error: '' })
          jsonGet('/cr-paper/pipe/web/collections').then(function (r) {
            var list = (r && r.collections) || []
            collectionStore.set({ list: list, loading: false })
            reportState({ event: 'collections-loaded', count: list.length })
          }).catch(function (e) {
            collectionStore.set({ loading: false, error: '收藏清单加载失败: ' + String(e).slice(0, 120) })
            reportState({ event: 'collection-error', error: String(e).slice(0, 200) })
          })
        }
        // 查看清单详情（展开/收起）
        function toggleCollectionDetail(name) {
          if (collectionStore.detailName === name) {
            collectionStore.set({ detailName: '', detail: null })
            return
          }
          collectionStore.set({ detailName: name, detail: null })
          jsonGet('/cr-paper/pipe/web/collections/' + encodeURIComponent(name)).then(function (r) {
            collectionStore.set({ detail: r || null })
            reportState({ event: 'collection-detail', name: name, items: ((r && r.items) || []).length })
          }).catch(function (e) {
            collectionStore.set({ detail: { error: '详情加载失败: ' + String(e).slice(0, 120) } })
            reportState({ event: 'collection-error', error: String(e).slice(0, 200) })
          })
        }
        // 删除收藏清单（回收站语义）
        function deleteCollection(name) {
          if (!window.confirm('确定删除收藏清单「' + name + '」？将移入 _trash 回收站。')) return
          collectionStore.set({ busy: true })
          jsonPost('/cr-paper/pipe/web/collections/' + encodeURIComponent(name), {}, 'DELETE', 10000).then(function (r) {
            collectionStore.set({ busy: false, msg: (r && r.ok) ? ('已删除「' + name + '」') : ('删除失败: ' + (r && r.error || '未知')) })
            reportState({ event: 'collection-deleted', ok: !!(r && r.ok), name: name })
            loadCollections()
          }).catch(function (e) {
            collectionStore.set({ busy: false, msg: '删除异常: ' + String(e).slice(0, 120) })
            reportState({ event: 'collection-error', error: String(e).slice(0, 200) })
          })
        }
        // 导出收藏清单（CSV → 管线 exports/ 目录）
        function exportCollection(name) {
          collectionStore.set({ busy: true, msg: '' })
          jsonPost('/cr-paper/pipe/web/collections/' + encodeURIComponent(name) + '/export', {}, 'POST', 20000).then(function (r) {
            var ok = !!(r && r.ok)
            collectionStore.set({ busy: false, msg: ok ? ('✅ 已导出 CSV（表格已展开预览；或点「下载 CSV」存到本机用 Excel/WPS 打开）') : ('导出失败: ' + (r && r.error || '未知')) })
            reportState({ event: 'collection-exported', ok: ok, name: name })
            if (ok) {
              // 自动展开当前清单 + 填充 CSV 预览（预览与展开解耦，导出即所见）
              collectionStore.set({ detailName: name, detail: null, csvPreview: null })
              jsonGet('/cr-paper/pipe/web/collections/' + encodeURIComponent(name)).then(function (r) {
                collectionStore.set({ detail: r || null })
                reportState({ event: 'collection-detail', name: name, items: ((r && r.items) || []).length })
              }).catch(function () { })
              previewCollectionCsv(name)
            }
          }).catch(function (e) {
            collectionStore.set({ busy: false, msg: '导出异常: ' + String(e).slice(0, 120) })
            reportState({ event: 'collection-error', error: String(e).slice(0, 200) })
          })
        }
        // CSV 预览：host 只读 exports/ 文件 → 解析 CSV（去 BOM）→ 渲染表格
        function previewCollectionCsv(name) {
          var safeName = String(name || '').replace(/\.csv$/i, '') + '.csv'
          collectionStore.set({ csvLoading: true, csvError: '', csvPreview: null })
          fetch('/cr-paper/export-file?name=' + encodeURIComponent(safeName), { cache: 'no-store' }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status)
            return r.text()
          }).then(function (text) {
            // 去 UTF-8 BOM + 按行拆 + 简单 CSV 解析（引号内逗号不拆——收藏 CSV 由管线固定列写入，字段内无逗号场景为主）
            var clean = String(text || '').replace(/^\uFEFF/, '')
            var lines = clean.split(/\r?\n/).filter(function (l) { return l.trim() !== '' })
            var rows = lines.map(function (l) {
              var cells = []
              var cur = ''; var inQ = false
              for (var i = 0; i < l.length; i++) {
                var ch = l[i]
                if (inQ) {
                  if (ch === '"') {
                    if (l[i + 1] === '"') { cur += '"'; i++ } else inQ = false
                  } else cur += ch
                } else {
                  if (ch === '"') inQ = true
                  else if (ch === ',') { cells.push(cur); cur = '' }
                  else cur += ch
                }
              }
              cells.push(cur)
              return cells
            })
            collectionStore.set({ csvLoading: false, csvPreview: rows, csvError: '' })
            reportState({ event: 'collection-csv-preview', name: name, rows: rows.length })
          }).catch(function (e) {
            collectionStore.set({ csvLoading: false, csvPreview: null, csvError: 'CSV 预览失败: ' + String(e && e.message || e).slice(0, 120) })
            reportState({ event: 'collection-csv-preview-error', name: name, error: String(e && e.message || e).slice(0, 160) })
          })
        }
        // 本地检索结果登记引用（复用 bibliography/add；page 未知 → null）
        function registerSearchResult(item) {
          if (!item || !item.doc_hash || !store.paperId) return
          jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/bibliography/add', {
            doc_id: item.doc_hash, page: null, snippet: String(item.text || '').slice(0, 500),
          }, 'POST', 8000).then(function (r) {
            var ok = !!(r && (r.ok || (r.item && r.item.seq)))
            store.set({ status: ok ? ('已登记引用' + (r.seq ? ' [' + r.seq + ']' : '') + (r.ok === false ? '（已复用）' : '')) : ('登记失败: ' + (r && r.error || '未知')) })
            reportState({ event: 'search-register', ok: ok, seq: (r && (r.seq || (r.item && r.item.seq))) || null, reused: r && r.ok === false })
            if (ok) refreshBiblio()
          }).catch(function (e) {
            store.set({ status: '登记异常: ' + String(e && e.message || e).slice(0, 120) })
            reportState({ event: 'search-register-error', error: String(e && e.message || e).slice(0, 200) })
          })
        }
        // 加载输入资料列表（papers/{id} 的 inputs_log；paperId 变化时联动）
        // 2026-08-17 会话 11 修复：原读 r.inputs（接口无此字段，恒 undefined → 面板一直"暂无输入资料"）；
        // 输入资料真实来源 = r.inputs_log（filemap 不含 inputs/ 键）
        function loadInputs() {
          if (!store.paperId) { inputsStore.set({ list: [], ingMap: {}, selected: null, preview: '', previewBlob: '', previewType: '' }); return }
          inputsStore.set({ loading: true })
          jsonGet('/cr-paper/paper?id=' + encodeURIComponent(store.paperId)).then(function (r) {
            var list = ((r && r.inputs_log) || []).map(function (i) { return { name: i.name, updated_at: i.ts || '' } })
            inputsStore.set({ list: list, loading: false })
            reportState({ event: 'inputs-loaded', paperId: store.paperId, count: list.length, names: list.map(function (i) { return i.name }) })
            refreshIngStatus()
          }).catch(function (e) {
            inputsStore.set({ loading: false, error: '资料列表加载失败: ' + String(e).slice(0, 120) })
            reportState({ event: 'inputs-error', error: String(e).slice(0, 200) })
          })
        }
        // 打开输入资料预览（按扩展名分支：文本 fetch 展示 / PDF 复用 T13 / 图片 blob <img>）
        function openInputPreview(item) {
          if (!item || !item.name) return
          var ext = (item.name.split('.').pop() || '').toLowerCase()
          var textExts = ['md', 'txt', 'csv', 'json']
          var imgExts = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']
          inputsStore.set({ selected: item, preview: '', previewBlob: '', error: '' })
          var url = '/cr-paper/input-file?id=' + encodeURIComponent(store.paperId) + '&name=' + encodeURIComponent(item.name)
          if (ext === 'pdf') {
            // PDF 复用 T13 PdfPreview（input 来源模式）：设 pdfStore + 切 pdf 视图
            inputsStore.set({ previewType: 'pdf' })
            if (typeof pdfStore !== 'undefined' && pdfStore) {
              pdfStore.set({ docId: store.paperId, source: 'input', name: item.name, page: 1, captureSlot: null, rev: pdfStore.rev + 1 })
              setDetailsView('workbench')
              openWorkbench()
            }
            reportState({ event: 'input-preview', name: item.name, type: 'pdf' })
            return
          }
          if (imgExts.indexOf(ext) >= 0) {
            fetch(url, { cache: 'no-store' }).then(function (r) { return r.blob() }).then(function (b) {
              inputsStore.set({ previewType: 'image', previewBlob: URL.createObjectURL(b) })
              reportState({ event: 'input-preview', name: item.name, type: 'image', bytes: b.size })
            }).catch(function (e) {
              inputsStore.set({ previewType: 'other', error: '图片加载失败: ' + String(e).slice(0, 100) })
              reportState({ event: 'input-preview-error', name: item.name, error: String(e).slice(0, 120) })
            })
            return
          }
          if (textExts.indexOf(ext) >= 0) {
            fetch(url, { cache: 'no-store' }).then(function (r) {
              if (!r.ok) throw new Error('HTTP ' + r.status)
              return r.text()
            }).then(function (t) {
              inputsStore.set({ previewType: 'text', preview: t.length > 512 * 1024 ? t.slice(0, 512 * 1024) + '\n\n…（内容超 512KB 已截断）' : t })
              reportState({ event: 'input-preview', name: item.name, type: 'text', chars: t.length })
            }).catch(function (e) {
              inputsStore.set({ previewType: 'other', error: '文本加载失败: ' + String(e).slice(0, 100) })
              reportState({ event: 'input-preview-error', name: item.name, error: String(e).slice(0, 120) })
            })
            return
          }
          // 其他（docx/xlsx 等）：提示用系统程序打开
          inputsStore.set({ previewType: 'other', error: '该类型（.' + ext + '）暂不支持内联预览，请用系统程序打开。' })
          reportState({ event: 'input-preview', name: item.name, type: 'other', ext: ext })
        }
        // 删除输入资料（回收站语义 _trash；confirm 由 UI 层弹，此处执行 + 刷新）
        function deleteInput(item) {
          if (!item || !item.name || inputsStore.busy) return
          inputsStore.set({ busy: true, error: '' })
          jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/inputs/' + encodeURIComponent(item.name), null, 'DELETE').then(function (r) {
            inputsStore.set({ busy: false })
            if (r && r.ok) {
              inputsStore.set({ selected: null, preview: '', previewBlob: '', previewType: '' })
              loadInputs()
              reportState({ event: 'input-deleted', name: item.name, trashedTo: r.trashed_to || '' })
            } else {
              inputsStore.set({ error: '删除失败: ' + ((r && r.error) || '未知') })
              reportState({ event: 'input-delete-error', name: item.name, error: ((r && r.error) || '').slice(0, 120) })
            }
          }).catch(function (e) {
            inputsStore.set({ busy: false, error: '删除异常: ' + String(e).slice(0, 100) })
            reportState({ event: 'input-delete-error', name: item.name, error: String(e).slice(0, 120) })
          })
        }
        // 入库状态匹配归一（监控×输入资料联动）：剥 [损坏] 前缀 + _14位时间戳 后比对
        function _normIngestName(name) {
          return String(name || '').replace(/^\[损坏\]\s*/, '').replace(/_\d{14}(?=\.[^.]+$)/, '').trim().toLowerCase()
        }
        // 刷新输入资料入库状态：/status pending+processing → ⏳入库中；/pipe/docs source_file → ✅已入库
        function refreshIngStatus() {
          if (!store.paperId) return
          jsonGet('/cr-paper/status').then(function (st) {
            jsonGet('/cr-paper/pipe/docs').then(function (d) {
              var docsList = (d && (d.docs || d.items)) || (Array.isArray(d) ? d : [])
              var inQ = ((st && (st.pending || [])).concat((st && (st.processing || []))))
              var map = {}
              ;(inputsStore.list || []).forEach(function (it) {
                var n = _normIngestName(it.name)
                if (!n) return
                if (docsList.some(function (x) { return _normIngestName(x.source_file) === n })) map[it.name] = 'done'
                else if (inQ.some(function (f) { return _normIngestName(f) === n })) map[it.name] = 'ing'
              })
              inputsStore.set({ ingMap: map })
              reportState({ event: 'input-ing-status', done: Object.keys(map).filter(function (k) { return map[k] === 'done' }).length, ing: Object.keys(map).filter(function (k) { return map[k] === 'ing' }).length })
            }).catch(function () {})
          }).catch(function () {})
        }
        // 索引触发（POST /index；成功/进行中/失败反馈）
        function indexInput(item) {
          if (!item || !item.name || inputsStore.busy) return
          inputsStore.set({ busy: true, error: '' })
          jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/inputs/' + encodeURIComponent(item.name) + '/index', {}).then(function (r) {
            inputsStore.set({ busy: false })
            // 2026-08-17 修复：管线拒绝返回 {ok:false, reason}（无 error 字段）→ 原逻辑误报"已触发索引"
            if (r && (r.error || r.ok === false)) {
              inputsStore.set({ error: '索引失败: ' + (r.error || r.reason || '拒绝') })
              reportState({ event: 'input-indexed', name: item.name, ok: false, error: String(r.error || r.reason || '').slice(0, 120) })
            } else {
              inputsStore.set({ error: '已触发索引（处理结果见入库监控）' })
              reportState({ event: 'input-indexed', name: item.name, ok: true })
              refreshIngStatus()
            }
          }).catch(function (e) {
            inputsStore.set({ busy: false, error: '索引触发异常: ' + String(e).slice(0, 100) })
            reportState({ event: 'input-index-error', name: item.name, error: String(e).slice(0, 120) })
          })
        }
        // 加载草稿（领域约束：每项目一篇论文 = 单一草稿逻辑名；管线 paper_detail 权威）
        function loadDrafts() {
          if (!store.paperId) { draftStore.set({ drafts: [], current: '', content: '' }); return }
          jsonGet('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId)).then(function (r) {
            var list = (r && r.drafts) || []
            draftStore.set({ drafts: list })
            if (list.length) {
              // 单论文形态：取第一篇（filemap 唯一 drafts/ 键；多版本 = 时间线语义）
              var first = list[0].name
              if (!draftStore.current) draftStore.set({ current: first })
              loadCurrentDraft()
            } else {
              draftStore.set({ current: '', content: '', version: 0, versions: [] })
            }
            reportState({ event: 'drafts-loaded', paperId: store.paperId, count: list.length, names: list.map(function (d) { return d.name }) })
          }).catch(function (e) {
            store.set({ status: '草稿列表加载失败: ' + String(e).slice(0, 120) })
            reportState({ event: 'drafts-error', error: String(e).slice(0, 200) })
          })
        }
        function loadCurrentDraft() {
          if (!store.paperId || !draftStore.current) return
          jsonGet('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/drafts/' + encodeURIComponent(draftStore.current)).then(function (r) {
            if (r && r.error) { store.set({ status: '草稿读取失败: ' + (r.error || '') }); return }
            draftStore.set({ content: (r && r.content) || '', version: (r && r.version) || 0, dirty: false })
            loadVersions()
            reportState({ event: 'draft-loaded', draft: draftStore.current, len: ((r && r.content) || '').length })
          }).catch(function (e) {
            store.set({ status: '草稿读取异常: ' + String(e).slice(0, 120) })
            reportState({ event: 'draft-error', error: String(e).slice(0, 200) })
          })
        }
        function loadVersions() {
          if (!store.paperId || !draftStore.current) return
          jsonGet('/cr-paper/versions?id=' + encodeURIComponent(store.paperId)).then(function (r) {
            var all = (r && r.entries) || []
            var cur = draftStore.current
            var filtered = all.filter(function (v) { return !cur || v.draft === cur })
            // 倒序显示：最新版本在最上（管线返回正序 v1→vN）
            filtered.reverse()
            draftStore.set({ versions: filtered.slice(0, 50) })
          }).catch(function (e) { reportState({ event: 'versions-error', error: String(e).slice(0, 200) }) })
        }
        // 保存草稿：管线自动生成保存备注 → 触发建议轮询（T4 保存钩子）
        function saveDraft() {
          if (!store.paperId || !draftStore.current || draftStore.saving) return
          draftStore.set({ saving: true })
          jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/drafts/' + encodeURIComponent(draftStore.current), { content: draftStore.content, note: '', action: 'edit' }).then(function (r) {
            draftStore.set({ saving: false })
            if (r && r.error) { store.set({ status: '保存失败: ' + r.error }); reportState({ event: 'draft-save-error', error: String(r.error).slice(0, 200) }); return }
            draftStore.set({ version: (r && r.version) || draftStore.version, dirty: false })
            store.set({ status: '已保存 v' + ((r && r.version) || '') + (r && r.docx ? ' · 已生成 DOCX' : ' · DOCX 跳过（pandoc 未配置？）') })
            loadVersions()
            reportState({ event: 'draft-saved', draft: draftStore.current, version: (r && r.version) || 0, docx: !!(r && r.docx) })
            // T4：保存触发后台扫描 → 轮询建议面板（后台异步，不拖慢保存响应）
            void pollSuggestions()
          }).catch(function (e) {
            draftStore.set({ saving: false })
            store.set({ status: '保存异常: ' + String(e).slice(0, 120) })
            reportState({ event: 'draft-save-error', error: String(e).slice(0, 200) })
          })
        }
        // 上传草稿替换（docx/md → 覆盖当前草稿，复用同一份草稿/引用链，形成 v1→上传版本证据链）
        // 链路：选文件 → host /cr-paper/draft-upload（落盘 → 管线 import_docx，draft_name=当前草稿）→ 刷新
        function uploadDraft() {
          if (!store.paperId) { store.set({ status: '请先选择项目再上传草稿' }); return }
          var input = document.createElement('input')
          input.type = 'file'
          input.accept = '.docx,.md,.markdown,.txt'
          input.onchange = function () {
            var f = input.files && input.files[0]
            if (!f) return
            store.set({ status: '正在上传草稿（' + f.name + '）…' })
            reportState({ event: 'draft-upload-start', name: f.name, size: f.size })
            // 直接用文件字节作为 body（避免 multipart 包装导致 host 误写整段 boundary），文件名经 fname 参数传
            fetch('/cr-paper/draft-upload?id=' + encodeURIComponent(store.paperId) + '&draft=' + encodeURIComponent(draftStore.current || '草稿.md') + '&fname=' + encodeURIComponent(f.name), { method: 'POST', body: f, headers: { 'Content-Type': 'application/octet-stream' }, cache: 'no-store' })
              .then(function (r) { return r.json().catch(function () { return { error: '响应解析失败' } }) })
              .then(function (d) {
                if (d && d.ok) {
                  store.set({ status: '已导入草稿 v' + (d.version || '') + '（' + (d.draft_name || '') + '）' })
                  reportState({ event: 'draft-upload-done', draft: d.draft_name, version: d.version || 0 })
                  // 上传即覆盖当前草稿内容：刷新
                  draftStore.set({ current: d.draft_name || draftStore.current })
                  loadCurrentDraft()
                  loadVersions()
                } else {
                  store.set({ status: '上传失败: ' + ((d && d.error) || ((d && d.reason) || '未知')) })
                  reportState({ event: 'draft-upload-error', error: String((d && (d.error || d.reason)) || '').slice(0, 200) })
                }
              })
              .catch(function (e) { store.set({ status: '上传异常: ' + String(e).slice(0, 120) }); reportState({ event: 'draft-upload-error', error: String(e).slice(0, 200) }) })
          }
          input.click()
        }
        // 回滚到指定版本
        function rollbackDraft(v) {
          if (!store.paperId || !draftStore.current) return
          if (!window.confirm('回滚到 v' + v + '？当前 v' + draftStore.version + ' 会自动备份为下一版。')) return
          jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/drafts/' + encodeURIComponent(draftStore.current) + '/rollback', { version: v }).then(function (r) {
            if (r && r.error) { store.set({ status: '回滚失败: ' + r.error }); return }
            draftStore.set({ content: (r && r.content) || '', version: (r && r.version) || 0, dirty: false })
            store.set({ status: '已回滚到 v' + v + ' · 当前 v' + ((r && r.version) || '') })
            loadVersions()
            reportState({ event: 'draft-rollback', to: v, now: (r && r.version) || 0 })
          }).catch(function (e) { store.set({ status: '回滚异常: ' + String(e).slice(0, 120) }); reportState({ event: 'rollback-error', error: String(e).slice(0, 200) }) })
        }
        // T4 建议轮询：保存后后台扫描通常数秒完成；最多 20s 轮询，零建议不显示不打扰
        function pollSuggestions() {
          if (!store.paperId || draftStore.suggest.polling) return
          draftStore.set({ suggest: Object.assign({}, draftStore.suggest, { polling: true }) })
          var deadline = Date.now() + 20000
          var timer = null
          function tick() {
            if (Date.now() > deadline) {
              draftStore.set({ suggest: Object.assign({}, draftStore.suggest, { polling: false }) })
              reportState({ event: 'suggest-poll-timeout', paperId: store.paperId })
              return
            }
            jsonGet('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/citesuggestions').then(function (r) {
              var items = (r && r.items) || []
              if (items.length) {
                draftStore.set({ suggest: { items: items, visible: true, polling: false, busy: false } })
                reportState({ event: 'suggest-found', count: items.length })
              } else {
                timer = setTimeout(tick, 1200)
              }
            }).catch(function (e) {
              timer = setTimeout(tick, 1200)
              reportState({ event: 'suggest-poll-error', error: String(e).slice(0, 200) })
            })
          }
          tick()
        }
        // 确认建议：登记清单（复用 D9 语义，去重复用编号）+ insert_mark 段尾插 [N] + 移除该条
        function confirmSuggestion(it) {
          if (!store.paperId || draftStore.suggest.busy) return
          draftStore.set({ suggest: Object.assign({}, draftStore.suggest, { busy: true }) })
          jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/bibliography/add', { doc_id: it.doc_id, snippet: String(it.text || '').slice(0, 500) }).then(function (r) {
            var seq = null
            var reused = false
            if (r && r.ok === false) { seq = typeof r.seq === 'number' ? r.seq : null; reused = seq !== null }
            else if (r && r.item && typeof r.item.seq === 'number') { seq = r.item.seq }
            if (seq === null) {
              draftStore.set({ suggest: Object.assign({}, draftStore.suggest, { busy: false }) })
              store.set({ status: '登记失败（' + ((r && r.error) || '未知') + '），建议已保留' })
              return
            }
            jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/drafts/' + encodeURIComponent(it.draft || draftStore.current) + '/insert_mark', { anchor: it.anchor, doc_id: it.doc_id }).then(function (mk) {
              store.set({ status: reused ? '已复用清单编号 [' + seq + '] 并插入段尾标记' : '已登记引用 [' + seq + '] 并插入段尾标记' })
              // 移除已处理建议
              var remain = draftStore.suggest.items.filter(function (x) { return x.idx !== it.idx })
              draftStore.set({ suggest: { items: remain, visible: remain.length > 0, polling: false, busy: false } })
              if (mk && mk.data && mk.data.version) draftStore.set({ version: mk.data.version })
              refreshBiblio()
              loadVersions()
              reportState({ event: 'suggest-confirmed', seq, reused })
            }).catch(function (e) {
              draftStore.set({ suggest: Object.assign({}, draftStore.suggest, { busy: false }) })
              store.set({ status: '已登记 [' + seq + ']，但插标失败: ' + String(e).slice(0, 120) })
              reportState({ event: 'suggest-insert-error', error: String(e).slice(0, 200) })
            })
          }).catch(function (e) {
            draftStore.set({ suggest: Object.assign({}, draftStore.suggest, { busy: false }) })
            store.set({ status: '登记异常: ' + String(e).slice(0, 120) })
            reportState({ event: 'suggest-confirm-error', error: String(e).slice(0, 200) })
          })
        }
        // 忽略建议：dismiss（不登记不插标）
        function dismissSuggestion(it) {
          if (!store.paperId) return
          jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/citesuggestions/dismiss', { idx: it.idx }).then(function () {
            var remain = draftStore.suggest.items.filter(function (x) { return x.idx !== it.idx })
            draftStore.set({ suggest: { items: remain, visible: remain.length > 0, polling: false, busy: draftStore.suggest.busy } })
            reportState({ event: 'suggest-dismissed', idx: it.idx, remain: remain.length })
          }).catch(function (e) { reportState({ event: 'suggest-dismiss-error', error: String(e).slice(0, 200) }) })
        }
        // 项目切换联动：同时刷新草稿与建议面板
        function onSelectPaper(id) {
          store.set({ paperId: id })
          // 重置草稿当前态：否则 loadDrafts 的 `if (!draftStore.current)` 因旧值残留而不更新，
          // 导致切换项目后草稿编辑器停留旧项目草稿、保存报「映射不存在」（F9）。
          draftStore.set({ suggest: { items: [], visible: false, polling: false, busy: false }, current: '', content: '', version: 0, versions: [], timelineOpen: false, dirty: false })
          refreshBiblio()
          loadDrafts()
        }
        // ── T12 布局：可拖拽分栏（老库 PanelGroup 纯前端 mousemove 移植，无 DSH API 依赖）──
        // 布局状态：draftShare 草稿区宽占主体比例；tabShare 文献工作台子 Tab 容器占其区高度比例；activeTab 当前子 Tab
        // 持久化：localStorage 'cr-tools.layout.workbench'（F5 后恢复，对齐老库 pi-mono.layout.* 语义）
        var LAYOUT_KEY = 'cr-tools.layout.workbench'
        var WORKBENCH_TABS = [
          { id: 'citations', label: '📚 引用链' },
          { id: 'digest', label: '📖 速览卡' },
          { id: 'search', label: '🔍 检索' },
          { id: 'inputs', label: '📁 输入资料' },
        ]
        var layoutStore = {
          draftShare: 0.55,     // 默认 草稿 55% | 文献 45%
          tabShare: 0.55,       // 文献工作台区：子 Tab 容器 55% | PDF 预览区 45%
          activeTab: 'citations',
          listeners: [],
          get: function () { return this },
          set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
          subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
        }
        function useLayoutStore() {
          var t = React.useState(0)
          React.useEffect(function () { return layoutStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
          return layoutStore
        }
        // 恢复持久化布局（只读一次；非法/缺失回落默认）
        function loadLayout() {
          try {
            var raw = localStorage.getItem(LAYOUT_KEY)
            if (raw) {
              var o = JSON.parse(raw)
              if (o && typeof o.draftShare === 'number' && typeof o.tabShare === 'number') {
                layoutStore.draftShare = o.draftShare
                layoutStore.tabShare = o.tabShare
              }
            }
          } catch (e) { }
        }
        // 水平分隔条拖拽（草稿 | 文献工作台）：mousemove 改 draftShare，边界钳制 0.25-0.75
        function startLibDrag(e) {
          e.preventDefault()
          if (!e.currentTarget) return
          var container = e.currentTarget.parentElement
          if (!container) return
          var rect = container.getBoundingClientRect()
          var startX = e.clientX
          var startShare = layoutStore.draftShare
          function move(ev) {
            var delta = (ev.clientX - startX) / rect.width
            var share = Math.min(0.75, Math.max(0.25, startShare + delta))
            layoutStore.set({ draftShare: share })
          }
          function up() {
            document.removeEventListener('mousemove', move)
            document.removeEventListener('mouseup', up)
            try { localStorage.setItem(LAYOUT_KEY, JSON.stringify({ draftShare: layoutStore.draftShare, tabShare: layoutStore.tabShare })) } catch (err) { }
            reportState({ event: 'layout-change', draftShare: Math.round(layoutStore.draftShare * 100), tabShare: Math.round(layoutStore.tabShare * 100) })
          }
          document.addEventListener('mousemove', move)
          document.addEventListener('mouseup', up)
        }
        // 垂直分隔条拖拽（文献工作台内：子 Tab 容器 | PDF 预览区）：mousemove 改 tabShare，边界钳制 0.3-0.8
        function startTabDrag(e) {
          e.preventDefault()
          if (!e.currentTarget) return
          var container = e.currentTarget.parentElement
          if (!container) return
          var rect = container.getBoundingClientRect()
          var startY = e.clientY
          var startShare = layoutStore.tabShare
          function move(ev) {
            var delta = (ev.clientY - startY) / rect.height
            var share = Math.min(0.8, Math.max(0.3, startShare + delta))
            layoutStore.set({ tabShare: share })
          }
          function up() {
            document.removeEventListener('mousemove', move)
            document.removeEventListener('mouseup', up)
            try { localStorage.setItem(LAYOUT_KEY, JSON.stringify({ draftShare: layoutStore.draftShare, tabShare: layoutStore.tabShare })) } catch (err) { }
            reportState({ event: 'layout-change', draftShare: Math.round(layoutStore.draftShare * 100), tabShare: Math.round(layoutStore.tabShare * 100) })
          }
          document.addEventListener('mousemove', move)
          document.addEventListener('mouseup', up)
        }


    function s_itemsForTitle() {
      try { return (typeof store !== 'undefined' && store) ? (store.items || []) : [] } catch (e) { return [] }
    }
    function handleDraftPaste(e, currentContent) {
      var pasted = ''
      try { pasted = (e.clipboardData && e.clipboardData.getData('text')) || '' } catch (err) { }
      if (!pasted) return false
      var cap = getCopyCapture()
      var hit = matchCopyCapture(cap, pasted, Date.now())
      if (!hit) return false
      e.preventDefault()
      // input 来源：docId 是 paperId + name，登记需管线 doc_id——先查 /docs 反查（文件名前缀）
      if (cap.source === 'input') {
        // 异步反查 doc_id；查到则用，查不到降级为仅粘贴（不打断）
        resolveInputDocId(cap.docId, cap.name || '', function (docId) {
          if (docId) {
            citeConfirmStore.set({ visible: true, capture: cap, pasted: pasted, busy: false, error: '', docId: docId })
            reportState({ event: 'paste-matched', docId: docId, page: cap.page, source: 'input' })
          } else {
            reportState({ event: 'paste-skipped', reason: 'input-doc-not-found', name: cap.name || '' })
          }
        })
        return true
      }
      citeConfirmStore.set({ visible: true, capture: cap, pasted: pasted, busy: false, error: '', docId: cap.docId })
      reportState({ event: 'paste-matched', docId: cap.docId, page: cap.page, source: 'doc' })
      return true
    }
    // input 来源反查管线 doc_id（/docs 列表按 source_file 前缀匹配）
    var inputDocCache = {}
    function resolveInputDocId(paperId, fileName, cb) {
      var key = paperId + ':' + fileName
      if (inputDocCache[key]) { cb(inputDocCache[key]); return }
      jsonGet('/cr-paper/pipe/docs').then(function (r) {
        var list = Array.isArray(r) ? r : ((r && r.docs) || [])
        var base = String(fileName).replace(/\.pdf$/i, '')
        var hit = null
        for (var i = 0; i < list.length; i++) {
          var sf = String(list[i].source_file || '')
          var sb = sf.replace(/\.pdf$/i, '').replace(/^\[T\]/, '')
          if (sb === base || sb.indexOf(base + '_') === 0) { hit = list[i].doc_id; break }
        }
        if (hit) inputDocCache[key] = hit
        cb(hit)
      }).catch(function () { cb(null) })
    }
    // 确认引用：登记 bibliography/add + 插 [N] 段尾
    function confirmCite() {
      reportState({ event: 'confirm-cite-called' })
      var st = citeConfirmStore
      if (!st.visible || !st.docId || st.busy) return
      st.set({ busy: true, error: '' })
      reportState({ event: 'confirm-busy-set', docId: st.docId })
      var body
      try {
        body = { doc_id: st.docId, page: st.capture ? st.capture.page : null, snippet: st.capture ? st.capture.text.slice(0, 500) : null }
        reportState({ event: 'confirm-body-ok', docId: st.docId, snippetLen: (body.snippet || '').length })
      } catch (syncErr) {
        st.set({ visible: false, busy: false, capture: null, pasted: '', docId: '', error: '' })
        reportState({ event: 'paste-confirm-error', error: '同步异常: ' + String(syncErr && syncErr.message || syncErr).slice(0, 120), fallback: 'no-request' })
        return
      }
      reportState({ event: 'paste-request-sent', paperId: store.paperId, docId: st.docId })
      jsonPost('/cr-paper/pipe/papers/' + encodeURIComponent(store.paperId) + '/bibliography/add', body, 'POST', 8000).then(function (r) {
        reportState({ event: 'paste-response-received', ok: !!(r && r.ok), seq: (r && (r.seq || (r.item && r.item.seq))) || null })
        if (r && r.ok && r.item) {
          var seq = r.item.seq
          // 插 [N] 段尾：追加到草稿内容末尾（M2 规范：段尾插入引用标记）
          var cur = draftStore.content
          var insert = cur.length && !cur.endsWith('\n') ? '\n' : ''
          var newContent = cur + insert + st.pasted + '[' + seq + ']'
          draftStore.set({ content: newContent, dirty: true })
          reportState({ event: 'paste-confirmed', docId: st.docId, seq: seq, reused: false })
        } else if (r && r.ok === false && r.seq) {
          // 已登记 → 复用编号
          var seq2 = r.seq
          var cur2 = draftStore.content
          var insert2 = cur2.length && !cur2.endsWith('\n') ? '\n' : ''
          draftStore.set({ content: cur2 + insert2 + st.pasted + '[' + seq2 + ']', dirty: true })
          reportState({ event: 'paste-confirmed', docId: st.docId, seq: seq2, reused: true })
        } else {
          st.set({ busy: false, error: (r && r.error) || '登记失败' })
          reportState({ event: 'paste-confirm-error', error: ((r && r.error) || '').slice(0, 120) })
          return
        }
        st.set({ visible: false, busy: false, capture: null, pasted: '', docId: '', error: '' })
        reportState({ event: 'cite-registered', ok: true })
      }).catch(function (e) {
        // 缺陷 A 加固：异常/超时 → 关浮卡 + 落原文 + 上报（不丢用户文本）
        var cur = draftStore.content
        var ins = cur.length && !cur.endsWith('\n') ? '\n' : ''
        draftStore.set({ content: cur + ins + st.pasted, dirty: true })
        st.set({ visible: false, busy: false, capture: null, pasted: '', docId: '', error: '' })
        var isAbort = e && (e.name === 'AbortError' || /abort/i.test(String(e.message || e)))
        reportState({ event: 'paste-confirm-error', error: (isAbort ? '登记超时' : String(e && e.message || e)).slice(0, 120), fallback: 'original-text', timeout: !!isAbort })
      })
    }
    // 仅粘贴：原文落草稿，不登记
    function skipCite() {
      var st = citeConfirmStore
      var cur = draftStore.content
      var ins = cur.length && !cur.endsWith('\n') ? '\n' : ''
      draftStore.set({ content: cur + ins + st.pasted, dirty: true })
      st.set({ visible: false, busy: false, capture: null, pasted: '', docId: '', error: '' })
      reportState({ event: 'paste-skipped', reason: 'user-choice' })
    }

        // ── 面板文献工作台视图组件（T15 增补：投递到右侧面板，取代占位）──
        // 复用同一数据层（store/inputsStore/loadPapers/loadInputs 等在 apply 顶层，双挂载点共享）
        function CitationsPanelBody() {
          var s = useStore()
          return React.createElement('div', { key: 'list' }, [
            React.createElement('div', { key: 't', style: { fontWeight: 600, marginBottom: '8px' } }, '📑 引用清单（' + s.items.length + ' 条）'),
            !s.papers.length && !s.loading ? React.createElement('div', { key: 'e', style: { color: '#5f6b63', padding: '20px 0', textAlign: 'center' } }, '无论文项目（管线数据根为空或服务未启动）') : null,
            s.papers.length && !s.items.length && !s.loading ? React.createElement('div', { key: 'em', style: { color: '#5f6b63', padding: '20px 0', textAlign: 'center' } }, '清单为空——引用登记后在此显示') : null,
            s.items.map(function (item) {
              return React.createElement('div', {
                key: 'it' + item.seq,
                style: { display: 'flex', gap: '10px', padding: '8px 10px', border: '1px solid #e0e0e0', borderRadius: '6px', marginBottom: '6px', background: '#fff', alignItems: 'center' },
              }, [
                React.createElement('span', { key: 'seq', style: { fontWeight: 600, color: '#4a90d9', flexShrink: 0 } }, '[' + item.seq + ']'),
                React.createElement('div', { key: 'body', style: { flex: 1, minWidth: 0 } }, [
                  React.createElement('div', { key: 'tx', style: { wordBreak: 'break-all' } }, item.formatted || '(无渲染)'),
                  React.createElement('div', { key: 'meta', style: { fontSize: ff(11), color: '#55624d', marginTop: '2px' } }, [
                    item.page && item.snippet ? React.createElement('span', { key: 'v', style: { color: '#1565c0', marginRight: '8px' } }, '📖 已读原文') : null,
                    item.page ? React.createElement('button', { key: 'pg', onClick: function () { jumpToPage(item.doc_id, item.page) }, style: { color: '#4a90d9', textDecoration: 'underline', background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: ff(11) } }, '第 ' + item.page + ' 页 ↗') : null,
                  ]),
                ]),
                React.createElement('div', { key: 'act', style: { display: 'flex', flexDirection: 'column', gap: '4px', flexShrink: 0 } }, [
                  React.createElement('button', { key: 'ins', onClick: function () { insertMarkToEditor(item.seq) }, style: { fontSize: ff(11), padding: '2px 8px', border: '1px solid #ccc', borderRadius: '4px', background: '#fff', cursor: 'pointer' } }, '插入草稿'),
                  React.createElement('button', { key: 'ver', onClick: function () { verifyItem(item.seq) }, style: { fontSize: ff(11), padding: '2px 8px', border: '1px solid #ccc', borderRadius: '4px', background: '#fff', cursor: 'pointer' } }, '核验'),
                ]),
              ])
            }),
          ])
        }
        function InputsPanelBody() {
          var is = useInputsStore()
          // 监控×输入资料联动：入库状态 10s 轮询（/status + /pipe/docs → ingMap）
          React.useEffect(function () {
            refreshIngStatus()
            var iv = setInterval(refreshIngStatus, 10000)
            return function () { clearInterval(iv) }
          }, [store.paperId])
          function iconFor(name) {
            var ext = (String(name).split('.').pop() || '').toLowerCase()
            if (['md', 'txt', 'json'].indexOf(ext) >= 0) return '📄'
            if (ext === 'pdf') return '📕'
            if (['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'].indexOf(ext) >= 0) return '🖼️'
            if (['xlsx', 'xls', 'csv'].indexOf(ext) >= 0) return '📊'
            if (ext === 'docx' || ext === 'doc') return '📃'
            return '📁'
          }
          function fmtSize(n) {
            if (!n && n !== 0) return ''
            if (n < 1024) return n + ' B'
            if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
            return (n / 1024 / 1024).toFixed(1) + ' MB'
          }
          var listItems = is.list.map(function (item) {
            var selected = is.selected && is.selected.name === item.name
            var ing = is.ingMap && is.ingMap[item.name]
            return React.createElement('div', {
              key: 'in' + item.name,
              onClick: function () { openInputPreview(item) },
              style: {
                display: 'flex', alignItems: 'center', gap: '8px', padding: '7px 10px',
                border: '1px solid ' + (selected ? '#4a90d9' : '#e0e0e0'),
                borderRadius: '6px', marginBottom: '4px', cursor: 'pointer',
                background: selected ? '#eaf2fb' : '#fff', fontSize: ff(12),
              },
            }, [
              React.createElement('span', { key: 'ic', style: { fontSize: ff(15), flexShrink: 0 } }, iconFor(item.name)),
              React.createElement('span', { key: 'nm', style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, item.name),
              ing === 'done' ? React.createElement('span', { key: 'ingd', title: '已入库（可在速览卡/检索使用）', style: { fontSize: ff(10), color: '#2e7d32', background: '#e8f5e9', border: '1px solid #a5d6a7', borderRadius: '9px', padding: '0 6px', flexShrink: 0 } }, '✅ 已入库') : null,
              ing === 'ing' ? React.createElement('span', { key: 'ingi', title: '正在后台入库（进度见入库监控）', style: { fontSize: ff(10), color: '#4a90d9', background: '#e8f0fe', border: '1px solid #b8d4f0', borderRadius: '9px', padding: '0 6px', flexShrink: 0 } }, '⏳ 入库中') : null,
              React.createElement('span', { key: 'sz', style: { fontSize: ff(10), color: '#5f6b63', flexShrink: 0 } }, fmtSize(item.size)),
            ])
          })
          var previewEl = null
          if (is.previewType === 'text') {
            previewEl = React.createElement('pre', { key: 'pv', style: { margin: 0, padding: '10px', fontSize: ff(12), lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: '300px', overflowY: 'auto', background: '#fafbfc', borderRadius: '6px', border: '1px solid #e0e0e0' } }, is.preview)
          } else if (is.previewType === 'image') {
            previewEl = React.createElement('img', { key: 'pv', src: is.previewBlob, style: { maxWidth: '100%', maxHeight: '300px', objectFit: 'contain', display: 'block', margin: '0 auto', borderRadius: '6px', border: '1px solid #e0e0e0' } })
          } else if (is.previewType === 'pdf') {
            previewEl = React.createElement('div', { key: 'pv', style: { color: '#3a453f', fontSize: ff(12), padding: '10px', background: '#fafbfc', borderRadius: '6px', border: '1px solid #e0e0e0' } }, '📕 PDF 预览：已切换到「📄 PDF 预览」视图。')
          } else if (is.selected && is.error) {
            previewEl = React.createElement('div', { key: 'pv', style: { color: '#55624d', fontSize: ff(12), padding: '10px', lineHeight: 1.7 } }, is.error)
          }
          var actions = null
          if (is.selected) {
            var btns = []
            if (is.previewType === 'pdf') {
              btns.push(React.createElement('button', { key: 'openpdf', onClick: function () { setDetailsView('workbench'); openWorkbench(); reportState({ event: 'input-open-pdf', name: is.selected.name }) }, style: Object.assign({}, btnStyle, { color: '#4a90d9', borderColor: '#4a90d9' }) }, '打开 PDF'))
            }
            btns.push(React.createElement('button', { key: 'idx', onClick: function () { indexInput(is.selected) }, disabled: is.busy, style: btnStyle }, '🔍 索引'))
            btns.push(React.createElement('button', { key: 'del', onClick: function () { if (window.confirm('确定删除输入资料「' + is.selected.name + '」？将移入回收站（_trash），可从系统删除恢复。')) deleteInput(is.selected) }, disabled: is.busy, style: Object.assign({}, btnStyle, { color: '#c62828', borderColor: '#c62828' }) }, '🗑️ 删除'))
            actions = React.createElement('div', { key: 'act', style: { display: 'flex', gap: '6px', marginTop: '8px', flexWrap: 'wrap' } }, btns)
          }
          return React.createElement('div', { key: 'inputs', style: { display: 'flex', flexDirection: 'column', gap: '8px' } }, [
            React.createElement('div', { key: 'h', style: { fontWeight: 600, fontSize: ff(12), color: '#333' } }, '📁 输入资料（' + is.list.length + ' 项）' + (is.loading ? ' …' : '')),
            is.error ? React.createElement('div', { key: 'err', style: { color: (String(is.error).indexOf('失败') >= 0 || String(is.error).indexOf('拒绝') >= 0) ? '#c62828' : '#2e7d32', fontSize: ff(11), marginTop: '2px' } }, is.error) : null,
            is.list.length === 0 && !is.loading ? React.createElement('div', { key: 'e', style: { color: '#5f6b63', fontSize: ff(12), padding: '16px 0', textAlign: 'center' } }, '暂无输入资料（docx/PDF/图片等，随论文项目携带）') : null,
            React.createElement('div', { key: 'lst', style: { maxHeight: '200px', overflowY: 'auto' } }, listItems),
            is.selected ? React.createElement('div', { key: 'sel', style: { fontSize: ff(11), color: '#3a453f', padding: '2px 2px 0' } }, '已选：' + is.selected.name + '（' + fmtSize(is.selected.size) + '）') : null,
            previewEl,
            actions,
          ])
        }

        // ── T14 速览卡面板（文档下拉 → digest 卡/生成进度轮询/错误态）──
        function DigestPanelBody() {
          var d = useDigestStore()
          var mountedRef = React.useRef(false)
          React.useEffect(function () {
            if (!mountedRef.current) { mountedRef.current = true; loadDocList() }
          }, [])
          var opts = d.docs.map(function (doc) {
            return React.createElement('option', { key: doc.doc_id, value: doc.doc_id },
              (doc.title || doc.source_file || doc.doc_id) + (doc.cited ? ' [已引用]' : ''))
          })
          var body = null
          if (d.status === 'loading') {
            body = React.createElement('div', { key: 'b', style: { color: '#3a453f', fontSize: ff(12), padding: '20px 8px', textAlign: 'center' } }, '正在读取速览卡…')
          } else if (d.status === 'processing') {
            body = React.createElement('div', { key: 'b', style: { color: '#3a453f', fontSize: ff(12), padding: '20px 8px', textAlign: 'center', lineHeight: 1.9 } }, [
              React.createElement('div', { key: 't' }, '✨ 速览卡生成中（' + d.pollCount + 's）…'),
              React.createElement('div', { key: 'bar', style: { margin: '8px auto 0', width: '140px', height: '6px', background: '#e0e0e0', borderRadius: '3px', overflow: 'hidden' } },
                React.createElement('div', { key: 'fill', style: { width: '100%', height: '100%', background: '#4a90d9' } })),
            ])
          } else if (d.status === 'error') {
            body = React.createElement('div', { key: 'b', style: { color: '#c62828', fontSize: ff(12), padding: '14px 8px', lineHeight: 1.7 } }, [
              d.error,
              React.createElement('button', { key: 'rt', onClick: function () { openDigest(d.docId, d.docLabel, false) }, style: Object.assign({}, btnStyle, { marginTop: '8px' }) }, '重试'),
            ])
          } else if (d.status === 'done' && d.card) {
            var c = d.card
            body = React.createElement('div', { key: 'b', style: { display: 'flex', flexDirection: 'column', gap: '8px', padding: '2px 2px 8px' } }, [
              React.createElement('div', { key: 'tt', style: { fontWeight: 700, fontSize: ff(13), color: '#1a1a1a', lineHeight: 1.5 } }, c.title || d.docLabel),
              (c.core_points && c.core_points.length) ? React.createElement('div', { key: 'cp' }, [
                React.createElement('div', { key: 'cp-t', style: { fontWeight: 600, fontSize: ff(12), marginBottom: '4px' } }, '💡 核心要点'),
                c.core_points.map(function (p, i) {
                  return React.createElement('div', { key: 'p' + i, style: { fontSize: ff(12), lineHeight: 1.6, color: '#333', padding: '3px 0' } }, '· ' + p)
                }),
              ]) : null,
              (c.key_concepts && c.key_concepts.length) ? React.createElement('div', { key: 'kc', style: { display: 'flex', flexWrap: 'wrap', gap: '4px' } }, [
                React.createElement('span', { key: 'kc-t', style: { fontSize: ff(11), color: '#55624d', alignSelf: 'center', marginRight: '2px' } }, '关键词：'),
                c.key_concepts.map(function (k, i) {
                  return React.createElement('span', { key: 'k' + i, style: { fontSize: ff(11), padding: '2px 8px', background: '#eef4fb', border: '1px solid #cfe0f5', borderRadius: '10px', color: '#2c5f8a' } }, k)
                }),
              ]) : null,
              (c.sections && c.sections.length) ? React.createElement('div', { key: 'sec' }, c.sections.map(function (s, i) {
                // 章节头部：▸ 标题 + 可点击页码（老库 digestGoto：跳转 PDF 预览对应页）
                var headChildren = [s.heading || ('§' + (i + 1))]
                if (s.pages && s.pages.length) {
                  headChildren.push(React.createElement('button', {
                    key: 'pg', onClick: function () { jumpToPage(d.docId, s.pages[0]) },
                    title: '预览跳到原文第 ' + s.pages[0] + ' 页',
                    style: { color: '#4a90d9', textDecoration: 'underline dotted', background: 'none', border: 'none', cursor: 'pointer', fontSize: ff(11), padding: 0, marginLeft: '6px' },
                  }, '第 ' + s.pages[0] + ' 页 ↗'))
                }
                return React.createElement('div', { key: 's' + i, style: { marginTop: '4px' } }, [
                  React.createElement('div', { key: 'sh', style: { fontWeight: 600, fontSize: ff(12), color: '#4a90d9', marginBottom: '2px' } }, headChildren),
                  (s.points || []).map(function (p, j) {
                    return React.createElement('div', { key: 'sp' + j, style: { fontSize: ff(12), lineHeight: 1.6, color: '#444', padding: '1px 0 1px 10px', borderLeft: '2px solid #e0e0e0', marginBottom: '2px' } }, p)
                  }),
                ])
              })) : null,
              (c.relevance && c.relevance !== '结合点建议：后续版本基于论文草稿提供（需 kind=paper 项目）。') ? React.createElement('div', { key: 'rl', style: { fontSize: ff(11), color: '#3a453f', background: '#fafbfc', border: '1px solid #e0e0e0', borderRadius: '6px', padding: '8px 10px' } }, c.relevance) : null,
            ])
          } else {
            body = React.createElement('div', { key: 'b', style: { color: '#5f6b63', fontSize: ff(12), padding: '20px 8px', textAlign: 'center' } }, '选择文档查看速览卡（AI 提炼的要点/概念）')
          }
          return React.createElement('div', { key: 'digest', style: { display: 'flex', flexDirection: 'column', gap: '8px', padding: '4px 2px', height: '100%', minHeight: 0, boxSizing: 'border-box' } }, [
            React.createElement('div', { key: 'ctl', style: { display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', flexShrink: 0 } }, [
              React.createElement('select', {
                key: 'sel', value: d.docId, disabled: d.docsLoading,
                onChange: function (e) { var v = e.target.value; if (v) { var hit = null; for (var i = 0; i < d.docs.length; i++) { if (d.docs[i].doc_id === v) { hit = d.docs[i]; break } } openDigest(v, (hit && (hit.title || hit.source_file)) || v, false) } },
                style: { flex: 1, minWidth: 0, padding: '3px 6px', border: '1px solid #ccc', borderRadius: '4px', fontSize: ff(12) },
              }, [React.createElement('option', { key: 'ph', value: '' }, d.docsLoading ? '加载中…' : (d.docs.length ? '选择文档…' : '暂无文档')), opts]),
              React.createElement('button', { key: 'rf', onClick: loadDocList, disabled: d.docsLoading, style: btnStyle, title: '刷新文档列表' }, '↻'),
              React.createElement('button', { key: 're', onClick: function () { openDigest(d.docId, d.docLabel, true) }, disabled: !d.docId || d.status === 'processing', style: Object.assign({}, btnStyle, { color: '#4a90d9', borderColor: '#4a90d9' }) }, '重新生成'),
            ]),
            // 卡片区独立滚动（下拉行冻结为表头）
            React.createElement('div', { key: 'scroll', style: { flex: 1, minHeight: 0, overflowY: 'auto' } }, body),
          ])
        }

        // ── T14 检索面板（本地/联网/收藏 三子标签）──
        function SearchPanelBody() {
          var ss = useSearchStore()
          var subTabs = [
            { id: 'local', label: '🔍 本地' },
            { id: 'web', label: '🌐 联网' },
            { id: 'collections', label: '☆ 收藏' },
          ]
          var subBtns = subTabs.map(function (tb) {
            var active = (ss.mode === tb.id) || (tb.id === 'collections' && ss.mode === 'collections')
            return React.createElement('button', {
              key: 'st' + tb.id, onClick: function () { searchStore.set({ mode: tb.id }) },
              style: {
                padding: '3px 10px', border: 'none', borderBottom: active ? '2px solid #4a90d9' : '2px solid transparent',
                cursor: 'pointer', fontSize: ff(12), background: 'none', color: active ? '#4a90d9' : '#666', fontWeight: active ? 600 : 400,
              },
            }, tb.label)
          })
          var sub = null
          if (ss.mode === 'local') sub = React.createElement(LocalSearchBody, { key: 'loc' })
          else if (ss.mode === 'web') sub = React.createElement(WebSearchBody, { key: 'web' })
          else sub = React.createElement(CollectionBody, { key: 'col' })
          return React.createElement('div', { key: 'search', style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 } }, [
            React.createElement('div', { key: 'stab', style: { display: 'flex', gap: '2px', borderBottom: '1px solid #e0e0e0', flexShrink: 0 } }, subBtns),
            // 子体内部自行滚动（查询行/筛选 chips 冻结为表头，结果列表独立滚动）
            React.createElement('div', { key: 'sbody', style: { flex: 1, minHeight: 0, overflow: 'hidden', padding: '8px 4px', boxSizing: 'border-box' } }, sub),
          ])
        }
        // 本地检索体（对齐老库：相似度/本项目资料徽标 + 打开预览/登记引用）
        function LocalSearchBody() {
          var ss = useSearchStore()
          var s = useStore()
          return React.createElement('div', { key: 'lb', style: { display: 'flex', flexDirection: 'column', gap: '8px', height: '100%', minHeight: 0 } }, [
            // 控制区冻结（查询行 + 联合提示 + 错误提示）
            React.createElement('div', { key: 'ctl', style: { flexShrink: 0, display: 'flex', flexDirection: 'column', gap: '8px' } }, [
              React.createElement('div', { key: 'q', style: { display: 'flex', gap: '6px' } }, [
                React.createElement('input', {
                  key: 'in', value: ss.localQuery, placeholder: '输入检索词，如：鲁迅 抗战 独立行动',
                  onChange: function (e) { searchStore.set({ localQuery: e.target.value }) },
                  onKeyDown: function (e) { if (e.key === 'Enter') runLocalSearch() },
                  style: { flex: 1, minWidth: 0, padding: '5px 8px', border: '1px solid #ccc', borderRadius: '4px', fontSize: ff(12) },
                }),
                React.createElement('button', { key: 'go', onClick: runLocalSearch, disabled: ss.localLoading, style: Object.assign({}, btnStyle, { color: '#fff', background: '#4a90d9', borderColor: '#4a90d9' }) }, ss.localLoading ? '检索中…' : '检索'),
              ]),
              ss.localQueryEn ? React.createElement('div', { key: 'en', style: { fontSize: ff(11), color: '#4a90d9' } }, '已联合英译词「' + ss.localQueryEn + '」检索（中文原词 + 英译词双路合并）') : null,
              ss.localError ? React.createElement('div', { key: 'err', style: { color: '#c62828', fontSize: ff(12), lineHeight: 1.7 } }, ss.localError) : null,
            ]),
            // 结果列表独立滚动
            React.createElement('div', { key: 'lst', style: { flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px' } }, [
              ss.localResults.length === 0 && !ss.localLoading && !ss.localError ? React.createElement('div', { key: 'e', style: { color: '#5f6b63', fontSize: ff(12), padding: '14px 0', textAlign: 'center' } }, '输入关键词检索本地知识库；结果可「打开预览」回原文核对。') : null,
              ss.localResults.map(function (r, i) {
                var meta = (r.doc_meta || {})
                return React.createElement('div', { key: 'lr' + i, style: { border: '1px solid #e0e0e0', borderRadius: '6px', padding: '8px 10px', background: '#fff' } }, [
                  React.createElement('div', { key: 'l1', style: { display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' } }, [
                    React.createElement('span', { key: 'sc', style: { fontSize: ff(11), color: '#1565c0', fontWeight: 600, flexShrink: 0 } }, String((r.score || 0).toFixed(3))),
                    r.is_project_input ? React.createElement('span', { key: 'own', style: { fontSize: ff(10), padding: '1px 6px', background: '#e8f5e9', color: '#2e7d32', borderRadius: '8px', flexShrink: 0 } }, '📁 本项目资料') : null,
                    React.createElement('span', { key: 'src', style: { fontSize: ff(11), color: '#55624d', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'right' } }, meta.title || meta.source_file || r.doc_hash),
                  ]),
                  React.createElement('div', { key: 'tx', style: { fontSize: ff(12), lineHeight: 1.6, color: '#333', wordBreak: 'break-all', marginBottom: '4px' } }, r.text),
                  React.createElement('div', { key: 'act', style: { display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' } }, [
                    React.createElement('button', { key: 'open', onClick: function () { jumpToPage(r.doc_hash, meta.page_estimate || 1) }, style: Object.assign({}, btnStyle, { fontSize: ff(11), padding: '2px 8px' }) }, '打开预览'),
                    React.createElement('button', { key: 'reg', onClick: function () { registerSearchResult(r) }, style: Object.assign({}, btnStyle, { color: '#4a90d9', borderColor: '#4a90d9', fontSize: ff(11), padding: '2px 8px' }) }, '登记引用'),
                    React.createElement('span', { key: 'st', style: { fontSize: ff(11), color: '#55624d' } }, s.status || ''),
                  ]),
                ])
              }),
            ]),
          ])
        }
        // 联网检索体（对齐老库：中文/国际双标签 + 来源筛选 chips + 勾选当前筛选 + OA 下载入库 + 翻译可见可改）
        function WebSearchBody() {
          var ss = useSearchStore()
          var shown = shownWebResults()
          var checkedCount = Object.keys(ss.checked).length
          var oaCount = 0
          for (var i = 0; i < shown.length; i++) { if (shown[i].oa_url) oaCount++ }
          // 中文/国际双子标签（对齐老库 webChannel 默认 zh）
          var chanBtns = [
            { v: 'zh', l: '🇨🇳 中文文献' }, { v: 'intl', l: '🌍 国际文献' },
          ].map(function (c) {
            var active = ss.channel === c.v
            return React.createElement('button', {
              key: 'ch' + c.v, onClick: function () { if (ss.channel !== c.v) searchStore.set({ channel: c.v }) },
              style: {
                flex: 1, border: 'none', padding: '4px 4px', fontSize: ff(11), cursor: 'pointer',
                borderBottom: active ? '2px solid #4a90d9' : '2px solid transparent',
                background: 'none', color: active ? '#4a90d9' : '#777', fontWeight: active ? 600 : 400,
              },
            }, c.l)
          })
          // 来源筛选 chips（对齐老库 srcFilter：off 显示删除线）
          var srcChips = null
          if (ss.webSources.length > 1) {
            srcChips = React.createElement('div', { key: 'filters', style: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '4px' } }, [
              React.createElement('span', { key: 'lbl', style: { fontSize: ff(10), color: '#5f6b63' } }, '来源：'),
              ss.webSources.map(function (s) {
                var on = ss.srcFilter[s] !== false
                return React.createElement('button', {
                  key: 'f' + s, onClick: function () { var nf = Object.assign({}, ss.srcFilter); nf[s] = !on; searchStore.set({ srcFilter: nf }) },
                  style: {
                    fontSize: ff(10), padding: '1px 7px', borderRadius: '9px', cursor: 'pointer',
                    border: '1px solid ' + (on ? '#4a90d9' : '#ccc'), background: on ? '#e8f0fe' : '#fff',
                    color: on ? '#4a90d9' : '#999', textDecoration: on ? 'none' : 'line-through',
                  },
                }, s)
              }),
            ])
          }
          return React.createElement('div', { key: 'wb', style: { display: 'flex', flexDirection: 'column', gap: '8px', height: '100%', minHeight: 0 } }, [
            // 控制区冻结（子标签/查询/翻译/筛选/收藏操作——滚动结果时保持表头）
            React.createElement('div', { key: 'ctl', style: { flexShrink: 0, display: 'flex', flexDirection: 'column', gap: '8px' } }, [
              React.createElement('div', { key: 'sub', style: { display: 'flex', borderBottom: '1px solid #e0e0e0' } }, chanBtns),
              React.createElement('div', { key: 'q', style: { display: 'flex', gap: '6px' } }, [
                React.createElement('input', {
                  key: 'in', value: ss.webQuery, placeholder: '输入研究主题 / 关键词（中文自动英译，可改后重搜）',
                  onChange: function (e) { searchStore.set({ webQuery: e.target.value }) },
                  onKeyDown: function (e) { if (e.key === 'Enter') runWebSearch() },
                  style: { flex: 1, minWidth: 0, padding: '5px 8px', border: '1px solid #ccc', borderRadius: '4px', fontSize: ff(12) },
                }),
                React.createElement('button', { key: 'go', onClick: runWebSearch, disabled: ss.webLoading, style: Object.assign({}, btnStyle, { color: '#fff', background: '#4a90d9', borderColor: '#4a90d9' }) }, ss.webLoading ? '检索中…' : '检索'),
              ]),
              ss.webDisclaimer ? React.createElement('div', { key: 'disc', style: { fontSize: ff(11), color: '#777', background: '#f5f7fa', borderRadius: '4px', padding: '4px 8px' } }, 'ℹ️ ' + ss.webDisclaimer) : null,
              // 翻译可见可改行（对齐老库 T5 A/C 2：改后点重搜按改后词检索）
              (ss.editedEn && ss.lastWebQuery) ? React.createElement('div', { key: 'trans', style: { display: 'flex', alignItems: 'center', gap: '5px' } }, [
                React.createElement('span', { key: 'lbl', style: { fontSize: ff(11), color: '#777', whiteSpace: 'nowrap' } }, '已翻译为' + (ss.translationMethod === 'cloud' ? '（云端）' : ss.translationMethod === 'local' ? '（本地）' : '') + '：'),
                React.createElement('input', {
                  key: 'in', value: ss.editedEn,
                  onChange: function (e) { searchStore.set({ editedEn: e.target.value }) },
                  onKeyDown: function (e) { if (e.key === 'Enter') runWebSearch() },
                  style: { flex: 1, minWidth: 0, fontSize: ff(11), border: '1px solid #ccc', borderRadius: '4px', padding: '2px 6px' },
                }),
                React.createElement('button', { key: 'go', onClick: runWebSearch, style: Object.assign({}, btnStyle, { fontSize: ff(11), padding: '1px 7px' }) }, '重搜'),
              ]) : null,
              ss.translationHint ? React.createElement('div', { key: 'th', style: { fontSize: ff(11), color: '#b3541e' } }, '⚠️ ' + ss.translationHint) : null,
              ss.webError ? React.createElement('div', { key: 'err', style: { color: '#c62828', fontSize: ff(12), lineHeight: 1.7 } }, ss.webError) : null,
              srcChips,
              // 勾选当前筛选 + ☆ 收藏勾选（对齐老库 Q3 pickAllShown/collectPicked）
              (ss.webResults.length || checkedCount > 0) ? React.createElement('div', { key: 'pick', style: { display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', background: '#f5f9ff', border: '1px solid #cfe0f5', borderRadius: '6px', padding: '6px 10px' } }, [
                React.createElement('button', { key: 'all', onClick: pickAllShown, disabled: !shown.length, style: Object.assign({}, btnStyle, { fontSize: ff(11), padding: '2px 8px' }) }, '勾选当前筛选'),
                React.createElement('button', { key: 'save', onClick: saveCollection, disabled: ss.saving || !checkedCount, style: Object.assign({}, btnStyle, { color: '#fff', background: '#2e7d32', borderColor: '#2e7d32', fontSize: ff(11), padding: '2px 10px' }) }, ss.saving ? '收藏中…' : '☆ 收藏勾选（' + checkedCount + '）'),
                ss.saveMsg ? React.createElement('span', { key: 'msg', style: { fontSize: ff(11), color: '#3a453f', flex: 1 } }, ss.saveMsg) : null,
              ]) : null,
            ]),
            // 结果列表独立滚动
            React.createElement('div', { key: 'lst', style: { flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px' } }, [
              ss.webResults.length === 0 && !ss.webLoading && !ss.webError ? React.createElement('div', { key: 'e', style: { color: '#5f6b63', fontSize: ff(12), padding: '14px 0', textAlign: 'center' } }, ss.webHint || '输入关键词检索联网文献；中文关键词自动英译（可改后重搜），结果带来源标签。') : null,
              shown.map(function (r, i) {
                var key = checkedKey(r)
                var checked = !!ss.checked[key]
                var oaSt = ss.oaState[key] || ''
                return React.createElement('div', { key: 'wr' + i, style: { border: '1px solid ' + (checked ? '#4a90d9' : '#e0e0e0'), borderRadius: '6px', padding: '8px 10px', background: checked ? '#f0f6ff' : '#fff' } }, [
                  React.createElement('div', { key: 'l1', style: { display: 'flex', gap: '6px', alignItems: 'flex-start' } }, [
                    React.createElement('input', {
                      key: 'ck', type: 'checkbox', checked: checked,
                      onChange: function () { toggleChecked(key, r) },
                      title: '勾选纳入收藏清单（供自行下载渠道使用）',
                      style: { marginTop: '2px', flexShrink: 0 },
                    }),
                    React.createElement('div', { key: 'bd', style: { flex: 1, minWidth: 0 } }, [
                      React.createElement('div', { key: 'tt', style: { display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' } }, [
                        React.createElement('span', { key: 'src', style: { fontSize: ff(10), color: '#6a4fb3', background: '#f0eafc', border: '1px solid #d3c2f0', borderRadius: '3px', padding: '0 5px', flexShrink: 0 } }, r.source || ''),
                        r.oa_url ? React.createElement('span', { key: 'oa', style: { fontSize: ff(10), fontWeight: 700, color: '#b3541e', background: '#fdf0e4', border: '1px solid #f0cda8', borderRadius: '3px', padding: '0 5px', flexShrink: 0 } }, 'OA') : null,
                        React.createElement('span', { key: 't', style: { fontSize: ff(12), fontWeight: 600, color: '#1a1a1a', lineHeight: 1.5 } }, r.title || '（无标题）'),
                      ]),
                      React.createElement('div', { key: 'meta', style: { fontSize: ff(11), color: '#3a453f', marginTop: '2px' } }, [
                        (r.authors && r.authors.length) ? React.createElement('span', { key: 'au' }, r.authors.slice(0, 5).join(', ')) : null,
                        r.journal ? React.createElement('span', { key: 'jn' }, (r.authors && r.authors.length ? ' · ' : '') + r.journal) : null,
                        React.createElement('span', { key: 'yr' }, ' · ' + (r.year || '?')),
                        r.doi ? React.createElement('span', { key: 'doi' }, ' · DOI: ' + r.doi) : null,
                        r.url ? React.createElement('a', { key: 'u', href: r.url, target: '_blank', rel: 'noopener noreferrer', style: { color: '#1565c0', marginLeft: '6px' } }, '详情页 ↗') : null,
                        r.oa_url ? React.createElement('a', { key: 'oa', href: r.oa_url, target: '_blank', rel: 'noopener noreferrer', style: { color: '#2e7d32', marginLeft: '6px' } }, 'OA 全文 ↗') : null,
                      ]),
                      r.abstract ? React.createElement('div', { key: 'ab', style: { fontSize: ff(12), color: '#444', lineHeight: 1.6, marginTop: '4px', wordBreak: 'break-all' } }, r.abstract) : null,
                      (r.note) ? React.createElement('div', { key: 'note', style: { fontSize: ff(11), color: '#b3541e', background: '#fdf6ec', borderRadius: '3px', padding: '2px 6px', marginTop: '4px' } }, '⚠️ ' + r.note) : null,
                      // OA 命中才显示「下载入库」（对齐老库 T8：仅 oa_url 有入口）
                      r.oa_url ? React.createElement('div', { key: 'oaact', style: { display: 'flex', gap: '6px', alignItems: 'center', marginTop: '5px' } }, [
                        React.createElement('button', {
                          key: 'dl', onClick: function () { oaIngest(r) },
                          disabled: oaSt === '下载中…' || oaSt.indexOf('✅') === 0 || oaSt.indexOf('✓') === 0,
                          title: '下载 OA 全文 PDF 并入本地知识库（数秒后本地语义检索可命中）',
                          style: Object.assign({}, btnStyle, { fontSize: ff(11), padding: '2px 8px', color: '#1565c0', borderColor: '#1565c0' }),
                        }, '⬇ 下载入库'),
                        oaSt ? React.createElement('span', { key: 'st', style: { fontSize: ff(10), color: '#777' } }, oaSt) : null,
                      ]) : null,
                    ]),
                  ]),
                ])
              }),
            ]),
          ])
        }
        // 收藏清单体（对齐老库：展开查看 + 复制引用信息 + 导出 CSV + 删除）
        function CollectionBody() {
          var cs = useCollectionStore()
          var mountedRef = React.useRef(false)
          React.useEffect(function () {
            if (!mountedRef.current) { mountedRef.current = true; loadCollections() }
          }, [])
          return React.createElement('div', { key: 'cb', style: { display: 'flex', flexDirection: 'column', gap: '6px', height: '100%', minHeight: 0, overflowY: 'auto', boxSizing: 'border-box' } }, [
            React.createElement('div', { key: 'h', style: { display: 'flex', alignItems: 'center', gap: '8px' } }, [
              React.createElement('span', { key: 't', style: { fontSize: ff(12), fontWeight: 600, color: '#333' } }, '☆ 收藏清单（' + cs.list.length + '）' + (cs.loading ? ' …' : '')),
              React.createElement('button', { key: 'rf', onClick: loadCollections, style: Object.assign({}, btnStyle, { marginLeft: 'auto', fontSize: ff(11), padding: '2px 8px' }) }, '↻ 刷新'),
            ]),
            cs.error ? React.createElement('div', { key: 'err', style: { color: '#c62828', fontSize: ff(11) } }, cs.error) : null,
            cs.msg ? React.createElement('div', { key: 'msg', style: { fontSize: ff(11), color: '#2e7d32' } }, cs.msg) : null,
            cs.list.length === 0 && !cs.loading ? React.createElement('div', { key: 'e', style: { color: '#5f6b63', fontSize: ff(12), padding: '14px 0', textAlign: 'center' } }, '暂无收藏清单。在「🌐 联网检索」结果中勾选条目 →「☆ 收藏勾选」即生成清单（名称带检索关键词）。') : null,
            cs.list.map(function (c) {
              var open = cs.detailName === c.name
              // CSV 预览区：与展开解耦——只要当前清单有预览数据即显示（导出后自动填充）
              var csvSection = null
              if (cs.csvPreview && cs.detailName === c.name && cs.csvPreview.length) {
                csvSection = React.createElement('div', { key: 'csv', style: { marginTop: '6px', borderTop: '1px dashed #d0d0d0', paddingTop: '6px' } }, [
                  React.createElement('div', { key: 'csvtbl', style: { overflowX: 'auto', border: '1px solid #e0e0e0', borderRadius: '6px', maxHeight: '220px', overflowY: 'auto' } },
                    React.createElement('table', { key: 'tb', style: { borderCollapse: 'collapse', fontSize: ff(11), width: '100%', background: '#fff' } }, [
                      React.createElement('thead', { key: 'th' }, React.createElement('tr', { key: 'tr' },
                        (cs.csvPreview[0] || []).map(function (h, j) {
                          return React.createElement('th', { key: 'h' + j, style: { border: '1px solid #ddd', padding: '4px 8px', background: '#f5f7fa', fontWeight: 600, textAlign: 'left', whiteSpace: 'nowrap' } }, h)
                        }))),
                      React.createElement('tbody', { key: 'tb' },
                        cs.csvPreview.slice(1).map(function (row, ri) {
                          return React.createElement('tr', { key: 'r' + ri },
                            row.map(function (cell, ci) {
                              return React.createElement('td', { key: 'c' + ci, style: { border: '1px solid #eee', padding: '3px 8px', verticalAlign: 'top', wordBreak: 'break-all', maxWidth: '240px' } }, cell)
                            }))
                        })),
                    ])),
                  React.createElement('div', { key: 'close', style: { textAlign: 'right', marginTop: '4px' } },
                    React.createElement('button', {
                      key: 'x', onClick: function () { collectionStore.set({ csvPreview: null }) },
                      style: Object.assign({}, btnStyle, { fontSize: ff(11), padding: '1px 7px', color: '#55624d' }),
                    }, '收起预览')),
                ])
              }
              // 条目列表（展开详情时显示）
              var detailEl = null
              if (open && cs.detail) {
                var items = cs.detail.items || []
                detailEl = React.createElement('div', { key: 'dt', style: { marginTop: '6px', borderTop: '1px dashed #d0d0d0', paddingTop: '6px' } }, [
                  React.createElement('div', { key: 'items', style: { fontWeight: 600, fontSize: ff(11), color: '#3a453f', margin: '2px 0 4px' } }, '清单条目（' + items.length + '）'),
                  items.length === 0 ? React.createElement('div', { key: 'e', style: { fontSize: ff(11), color: '#5f6b63' } }, '（无条目）') : null,
                  items.map(function (it, j) {
                    return React.createElement('div', { key: 'i' + j, style: { border: '1px solid #e0e0e0', borderRadius: '6px', padding: '6px 8px', marginBottom: '6px' } }, [
                      React.createElement('div', { key: 'tt', style: { display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' } }, [
                        it.source ? React.createElement('span', { key: 'src', style: { fontSize: ff(10), color: '#6a4fb3', background: '#f0eafc', border: '1px solid #d3c2f0', borderRadius: '3px', padding: '0 5px', flexShrink: 0 } }, it.source) : null,
                        it.oa_url ? React.createElement('span', { key: 'oa', style: { fontSize: ff(10), fontWeight: 700, color: '#b3541e', background: '#fdf0e4', border: '1px solid #f0cda8', borderRadius: '3px', padding: '0 5px', flexShrink: 0 } }, 'OA') : null,
                        React.createElement('span', { key: 't', style: { fontSize: ff(12), fontWeight: 600 } }, it.title || '（无标题）'),
                      ]),
                      React.createElement('div', { key: 'meta', style: { fontSize: ff(11), color: '#55624d', marginTop: '2px' } }, [
                        (it.authors || []).slice(0, 5).join(', '),
                        it.journal ? ' · ' + it.journal : '',
                        ' · ' + (it.year || '?'),
                        it.doi ? ' · DOI: ' + it.doi : '',
                        it.url ? React.createElement('a', { key: 'u', href: it.url, target: '_blank', rel: 'noopener noreferrer', style: { color: '#1565c0', marginLeft: '6px' } }, '详情页 ↗') : null,
                      ]),
                      React.createElement('div', { key: 'act', style: { display: 'flex', gap: '4px', marginTop: '4px' } }, [
                        React.createElement('button', {
                          key: 'cp', onClick: function () { copyCiteInfo(it) },
                          title: '复制标题/作者/期刊/年份/DOI/链接，粘贴到知网等渠道检索下载',
                          style: Object.assign({}, btnStyle, { fontSize: ff(11), padding: '1px 7px' }),
                        }, '📋 复制引用信息'),
                      ]),
                    ])
                  }),
                ])
              }
              return React.createElement('div', { key: 'c' + c.name, style: { border: '1px solid #e0e0e0', borderRadius: '6px', padding: '7px 10px', background: '#fff' } }, [
                React.createElement('div', { key: 'l1', style: { display: 'flex', alignItems: 'center', gap: '6px' } }, [
                  React.createElement('button', {
                    key: 'nm', onClick: function () { var willOpen = collectionStore.detailName !== c.name; toggleCollectionDetail(c.name); if (willOpen) previewCollectionCsv(c.name) },
                    title: '检索渠道：' + (c.channel === 'zh' ? '中文文献' : c.channel === 'intl' ? '国际文献' : c.channel) + ' · ' + (c.created_at || ''),
                    style: { flex: 1, minWidth: 0, textAlign: 'left', border: 'none', background: 'none', fontWeight: 600, fontSize: ff(12), cursor: 'pointer', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', padding: '2px 0', color: '#333' },
                  }, (open ? '▾ ' : '▸ ') + c.name + '（' + c.count + ' 条）'),
                  React.createElement('span', { key: 'ct', style: { fontSize: ff(11), color: '#55624d', flexShrink: 0 } }, String(c.created_at || '').slice(0, 10)),
                ]),
                React.createElement('div', { key: 'l3', style: { display: 'flex', gap: '6px', marginTop: '4px', flexWrap: 'wrap' } }, [
                  React.createElement('button', {
                    key: 'vw', onClick: function () { toggleCollectionDetail(c.name); if (!open) previewCollectionCsv(c.name) },
                    style: Object.assign({}, btnStyle, { fontSize: ff(11), padding: '2px 8px', color: '#4a90d9', borderColor: '#4a90d9' }),
                  }, open ? '收起' : '查看'),
                  React.createElement('button', { key: 'ex', onClick: function () { exportCollection(c.name) }, disabled: cs.busy, style: Object.assign({}, btnStyle, { fontSize: ff(11), padding: '2px 8px', color: '#2e7d32', borderColor: '#2e7d32' }) }, '⬇ CSV'),
                  React.createElement('button', { key: 'del', onClick: function () { deleteCollection(c.name) }, disabled: cs.busy, style: Object.assign({}, btnStyle, { fontSize: ff(11), padding: '2px 8px', color: '#c62828', borderColor: '#c62828' }) }, '🗑'),
                ]),
                csvSection,
                detailEl,
              ])
            }),
          ])
        }

        // ── 入库监控体（/status 5s 轮询：阶段进度条 + 队列徽标 + 待处理列表；对齐老库 webui status.js）──
        function MonitorBody() {
          var m = useMonitorStore()
          var mountedRef = React.useRef(false)
          // 批量提交文献（本地状态：已选文件/上传中/结果）
          var pickRef = React.useRef([])
          var [upTick, setUpTick] = React.useState(0)
          var [upResult, setUpResult] = React.useState(null)
          var [uploading, setUploading] = React.useState(false)
          var fileInputRef = React.useRef(null)
          React.useEffect(function () {
            if (!mountedRef.current) { mountedRef.current = true; loadStatus() }
            var iv = setInterval(loadStatus, 5000)
            return function () { clearInterval(iv) }
          }, [])
          // 提交文件到管线（/cr-paper/upload → 00_待处理 → M1-M4 后台入库）
          function submitUpload(files) {
            if (!files || !files.length) return
            var arr = Array.prototype.slice.call(files)
            pickRef.current = arr
            setUpTick(function (n) { return n + 1 })
            setUploading(true)
            setUpResult(null)
            reportState({ event: 'upload-start', count: arr.length, names: arr.map(function (f) { return f.name }) })
            var fd = new FormData()
            for (var i = 0; i < arr.length; i++) fd.append('files', arr[i], arr[i].name)
            fd.append('translate', 'false')
            fetch('/cr-paper/upload', { method: 'POST', body: fd }).then(function (r) { return r.json() }).then(function (j) {
              setUpResult(j)
              setUploading(false)
              pickRef.current = []
              setUpTick(function (n) { return n + 1 })
              reportState({ event: 'upload-done', accepted: ((j && j.accepted) || []).length, rejected: ((j && j.rejected) || []).length, warnings: ((j && j.warnings) || []).length })
              setTimeout(loadStatus, 1500)
            }).catch(function (e) {
              setUpResult({ error: '上传失败: ' + String(e && e.message || e) })
              setUploading(false)
              reportState({ event: 'upload-error', error: String(e && e.message || e).slice(0, 200) })
            })
          }
          var d = m.data
          var stagesDef = [
            { id: 'm1', label: '① 识别', color: '#4a90d9' },
            { id: 'm2', label: '② OCR', color: '#7b1fa2' },
            { id: 'm3', label: '③ 翻译', color: '#00897b' },
            { id: 'm4', label: '④ 索引', color: '#e65100' },
          ]
          var queuesDef = ['00_待处理', '01_OCR队列', '02_翻译队列', '03_知识库原文', '99_异常']
          var qs = (d && d.queues) || {}
          function fmtUptime(s) {
            s = Number(s || 0)
            if (s < 60) return s + ' 秒'
            if (s < 3600) return Math.floor(s / 60) + ' 分'
            return Math.floor(s / 3600) + ' 小时 ' + Math.floor((s % 3600) / 60) + ' 分'
          }
          var stageEls = stagesDef.map(function (sd) {
            var st = (d && d.stages && d.stages[sd.id]) || null
            var pct = 0
            var detail = '空闲'
            if (st && st.total) { pct = Math.round((st.done / st.total) * 100); detail = st.done + '/' + st.total + (st.doc_id ? ' · ' + String(st.doc_id).slice(0, 26) : '') }
            else if (st) { detail = st.doc_id ? '处理 ' + String(st.doc_id).slice(0, 26) : '处理中' }
            return React.createElement('div', { key: sd.id, style: { marginBottom: '6px' } }, [
              React.createElement('div', { key: 'l', style: { display: 'flex', justifyContent: 'space-between', fontSize: ff(11), color: '#3a453f', marginBottom: '2px' } }, [
                React.createElement('span', { key: 'n', style: { fontWeight: 600, color: sd.color } }, sd.label),
                React.createElement('span', { key: 'd', style: { color: '#55624d', maxWidth: '60%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, detail),
              ]),
              React.createElement('div', { key: 'bar', style: { height: '6px', background: '#e8e8e8', borderRadius: '3px', overflow: 'hidden' } },
                React.createElement('div', { key: 'fill', style: { width: (st && st.total) ? pct + '%' : '0%', height: '100%', background: sd.color, transition: 'width 0.4s' } })),
            ])
          })
          var queueEls = queuesDef.map(function (q) {
            var n = Number(qs[q] || 0)
            var warn = q === '99_异常' && n > 0
            var active = n > 0 && q !== '99_异常'
            return React.createElement('span', {
              key: q, title: q,
              style: {
                fontSize: ff(11), padding: '2px 9px', borderRadius: '10px',
                border: '1px solid ' + (warn ? '#c62828' : active ? '#4a90d9' : '#ccc'),
                background: warn ? '#fdecea' : active ? '#e8f0fe' : '#f5f5f5',
                color: warn ? '#c62828' : active ? '#4a90d9' : '#999', whiteSpace: 'nowrap',
              },
            }, q.replace(/^\d+_/, '') + ' ' + n)
          })
          return React.createElement('div', { key: 'mon', style: { display: 'flex', flexDirection: 'column', gap: '10px', padding: '2px' } }, [
            React.createElement('div', { key: 'h', style: { display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' } }, [
              React.createElement('span', { key: 't', style: { fontWeight: 600, fontSize: ff(13), color: '#333' } }, '📊 入库监控'),
              React.createElement('span', { key: 'up', style: { fontSize: ff(11), color: '#2e7d32', background: '#e8f5e9', border: '1px solid #a5d6a7', borderRadius: '10px', padding: '1px 8px' } }, '● 运行中 ' + fmtUptime(d && d.uptime_s)),
              React.createElement('span', { key: 'doc', style: { fontSize: ff(11), color: '#3a453f' } }, '文档 ' + ((d && d.docs_total) || 0) + ' · 切块 ' + ((d && d.chunks_total) || 0)),
              React.createElement('span', { key: 'mv', style: { fontSize: ff(11), color: '#5f6b63' } }, 'manifest v' + ((d && d.manifest_version) || '-')),
            ]),
            // 批量提交文献区（多选/拖放 → 管线后台入库；D6 起点导入通道）
            React.createElement('div', { key: 'upbox', style: { border: '2px dashed #b8d4f0', borderRadius: '8px', padding: '10px 12px', background: '#f7faff' } }, [
              React.createElement('div', { key: 'upt', style: { fontSize: ff(12), fontWeight: 600, color: '#2c5f8a', marginBottom: '6px' } }, '📤 批量提交文献（PDF/docx 等 → 管线后台入库）'),
              React.createElement('div', {
                key: 'drop', onDragOver: function (e) { e.preventDefault(); e.stopPropagation() },
                onDrop: function (e) { e.preventDefault(); e.stopPropagation(); submitUpload(e.dataTransfer && e.dataTransfer.files) },
                style: { border: '1px dashed #4a90d9', borderRadius: '6px', padding: '14px 8px', textAlign: 'center', color: '#6b8db5', fontSize: ff(12), cursor: 'pointer', background: '#fff', marginBottom: '6px' },
              }, '将文献文件拖放到此处，或点击下方按钮选择（支持多选批量）'),
              React.createElement('div', { key: 'upctl', style: { display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' } }, [
                React.createElement('input', {
                  key: 'fi', ref: fileInputRef, type: 'file', multiple: true, accept: '.pdf,.doc,.docx,.md,.txt,.caj',
                  style: { display: 'none' },
                  onChange: function (e) { submitUpload(e.target.files); e.target.value = '' },
                }),
                React.createElement('button', {
                  key: 'pick', onClick: function () { if (fileInputRef.current) fileInputRef.current.click() }, disabled: uploading,
                  style: Object.assign({}, btnStyle, { color: '#4a90d9', borderColor: '#4a90d9' }),
                }, uploading ? '上传中…' : '选择文件…'),
                pickRef.current.length ? React.createElement('span', { key: 'n', style: { fontSize: ff(11), color: '#3a453f' } }, '已选 ' + pickRef.current.length + ' 个（' + pickRef.current.map(function (f) { return f.name }).slice(0, 3).join('、') + (pickRef.current.length > 3 ? ' 等' : '') + '）') : null,
              ]),
              // 提交结果反馈
              upResult ? React.createElement('div', { key: 'res', style: { marginTop: '6px', fontSize: ff(11), lineHeight: 1.7 } }, [
                upResult.error ? React.createElement('div', { key: 'e', style: { color: '#c62828' } }, upResult.error) : null,
                (upResult.accepted && upResult.accepted.length) ? React.createElement('div', { key: 'a', style: { color: '#2e7d32' } }, '✅ 已入队 ' + upResult.accepted.length + ' 个：' + upResult.accepted.map(function (a) { return a.path }).slice(0, 4).join('、') + (upResult.accepted.length > 4 ? ' 等' : '')) : null,
                (upResult.rejected && upResult.rejected.length) ? React.createElement('div', { key: 'r', style: { color: '#c62828' } }, '⚠️ 未接受 ' + upResult.rejected.length + ' 个：' + upResult.rejected.map(function (r) { return r.path + '（' + r.reason + '）' }).slice(0, 3).join('；') + (upResult.rejected.length > 3 ? ' 等' : '')) : null,
                (upResult.warnings && upResult.warnings.length) ? React.createElement('div', { key: 'w', style: { color: '#e65100' } }, '⚠️ 提示：' + upResult.warnings.map(function (w) { return w.path + '（' + w.reason + '）' }).slice(0, 3).join('；')) : null,
              ]) : null,
              React.createElement('div', { key: 'hint', style: { fontSize: ff(10), color: '#5f6b63', marginTop: '4px' } }, '提交后自动进入「00_待处理」队列 → ①识别 → ②OCR → ③翻译 → ④索引，进度见下方阶段条；已存在的内容自动判重跳过。'),
            ]),
            m.error ? React.createElement('div', { key: 'err', style: { color: '#c62828', fontSize: ff(12) } }, m.error) : null,
            React.createElement('div', { key: 'q', style: { display: 'flex', gap: '5px', flexWrap: 'wrap', alignItems: 'center' } }, [
              React.createElement('span', { key: 'ql', style: { fontSize: ff(11), color: '#5f6b63' } }, '队列：'),
              queueEls,
            ]),
            React.createElement('div', { key: 'st', style: { border: '1px solid #e0e0e0', borderRadius: '8px', padding: '10px 12px', background: '#fff' } }, [
              React.createElement('div', { key: 'stt', style: { fontSize: ff(12), fontWeight: 600, marginBottom: '8px', color: '#333' } }, '阶段进度'),
              stageEls,
            ]),
            (d && ((d.processing && d.processing.length) || (d.pending && d.pending.length))) ? React.createElement('div', { key: 'pend', style: { border: '1px solid #e0e0e0', borderRadius: '8px', padding: '10px 12px', background: '#fff' } }, [
              (d.processing && d.processing.length) ? React.createElement('div', { key: 'pro', style: { marginBottom: '6px' } }, [
                React.createElement('div', { key: 't', style: { fontSize: ff(12), fontWeight: 600, color: '#333', marginBottom: '4px' } }, '⏳ 处理中'),
                d.processing.map(function (p, i) { return React.createElement('div', { key: 'p' + i, style: { fontSize: ff(11), color: '#3a453f', padding: '1px 0' } }, '· ' + p) }),
              ]) : null,
              (d.pending && d.pending.length) ? React.createElement('div', { key: 'pe', style: {} }, [
                React.createElement('div', { key: 't', style: { fontSize: ff(12), fontWeight: 600, color: '#333', marginBottom: '4px' } }, '📥 待处理队列'),
                d.pending.map(function (p, i) {
                  var broken = String(p).indexOf('[损坏]') === 0
                  return React.createElement('div', { key: 'pd' + i, style: { fontSize: ff(11), color: broken ? '#c62828' : '#666', padding: '1px 0' } }, '· ' + p)
                }),
              ]) : null,
            ]) : null,
            React.createElement('div', { key: 'ctl', style: { display: 'flex', alignItems: 'center', gap: '8px' } }, [
              React.createElement('button', { key: 'rf', onClick: loadStatus, style: Object.assign({}, btnStyle, { fontSize: ff(11), padding: '2px 8px' }) }, '↻ 立即刷新'),
              React.createElement('span', { key: 'ts', style: { fontSize: ff(10), color: '#aaa' } }, m.updatedAt ? ('更新于 ' + new Date(m.updatedAt).toLocaleTimeString('zh-CN', { hour12: false })) : ''),
            ]),
          ])
        }

        // 头部（项目/格式/刷新）+ 草稿(左) | 文献工作台(右，子 Tab + PDF 下) 可拖拽分栏
        function PanelWorkbench() {
          var s = useStore()
          var ds = useDraftStore()
          var ls = useLayoutStore()
          // 挂载：数据初始化（loadPapers/loadLayout；草稿联动由 loadPapers→selectPaper→paperId effect 触发）
          var mountedRef = React.useRef(false)
          React.useEffect(function () {
            if (!mountedRef.current) {
              mountedRef.current = true
              loadLayout()
              if (!s.paperId && !s.loading) loadPapers()
            }
          }, [])
          // paperId 变化 → 联动草稿/输入资料/清单
          var prevPid = React.useRef('')
          React.useEffect(function () {
            if (s.paperId && s.paperId !== prevPid.current) {
              prevPid.current = s.paperId
              loadDrafts()
              loadInputs()
              refreshBiblio()
            }
          }, [s.paperId])
          // 头部单行：项目/格式 + 文献工作台子 Tab（引用链/速览卡/检索/输入资料）右对齐——内容整体上移一行
          var pl = usePanelLitTab()
          var subTabs = [
            { id: 'citations', label: '📑 引用链' },
            { id: 'digest', label: '✨ 速览卡' },
            { id: 'search', label: '🔍 检索' },
            { id: 'inputs', label: '📁 输入资料' },
          ]
          var subTabBtns = subTabs.map(function (tb) {
            var active = pl.current === tb.id
            return React.createElement('button', {
              key: 'tb' + tb.id,
              onClick: function () { panelLitTab.set({ current: tb.id }) },
              style: {
                padding: '4px 10px', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: ff(13),
                background: active ? '#4a90d9' : 'transparent', color: active ? '#fff' : '#333',
                fontWeight: active ? 600 : 400, marginRight: '2px', whiteSpace: 'nowrap',
              },
            }, tb.label)
          })
          var head = React.createElement('div', { key: 'head', style: { display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px', flexShrink: 0 } }, [
            React.createElement('span', { key: 'l2', style: { fontWeight: 600, fontSize: ff(12), whiteSpace: 'nowrap' } }, '格式'),
            React.createElement('select', {
              key: 'fmt', value: s.format,
              onChange: function (e) { switchFormat(e.target.value) },
              style: { padding: '3px 6px', border: '1px solid #ccc', borderRadius: '4px', fontSize: ff(12) },
            }, FORMATS.map(function (f) { return React.createElement('option', { key: f, value: f }, f) })),
            React.createElement('div', { key: 'subtabs', style: { display: 'flex', marginLeft: 'auto', overflowX: 'auto' } }, subTabBtns),
          ])
          return React.createElement('div', { key: 'wb', style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, gap: '4px' } }, [
            head,
            s.status ? React.createElement('div', { key: 'st', style: { color: '#55624d', fontSize: ff(11), flexShrink: 0 } }, s.status) : null,
            React.createElement('div', { key: 'body', style: { display: 'flex', flex: 1, minHeight: 0, gap: '4px' } }, [
              // 左：草稿编辑器（可拖拽宽度 draftShare%）
              React.createElement('div', { key: 'draftPane', style: { width: (ls.draftShare * 100) + '%', minWidth: 260, minHeight: 0, display: 'flex', flexDirection: 'column' } }, [
                React.createElement(WorkbenchDraftEditor, { key: 'de' }),
              ]),
              React.createElement('div', { key: 'resizer-v', onMouseDown: startLibDrag, style: { width: '6px', cursor: 'col-resize', background: 'transparent', flexShrink: 0, borderRadius: '3px' } }),
              // 右：文献工作台（子 Tab + PDF 下）
              React.createElement('div', { key: 'libPane', style: { flex: 1, minWidth: 240, minHeight: 0, display: 'flex', flexDirection: 'column', border: '1px solid #e0e0e0', borderRadius: '8px', overflow: 'hidden' } }, [
                React.createElement('div', { key: 'tabPane', style: { height: (ls.tabShare * 100) + '%', minHeight: 0, display: 'flex', flexDirection: 'column' } }, [
                  React.createElement(PanelWorkbenchTabs, { key: 'tabs' }),
                ]),
                React.createElement('div', { key: 'resizer-h', onMouseDown: startTabDrag, style: { height: '6px', cursor: 'row-resize', background: '#f0f0f0', flexShrink: 0, borderTop: '1px solid #e0e0e0', borderBottom: '1px solid #e0e0e0' } }),
                React.createElement('div', { key: 'pdfPane', style: { flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex' } }, [
                  React.createElement('div', { key: 'pdfWrap', style: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' } }, [
                    React.createElement(PdfPreview, { key: 'comp' }),
                  ]),
                ]),
              ]),
            ]),
          ])
        }
        // 草稿编辑器顶层组件（面板工作台用，独立 hook 链）
        function WorkbenchDraftEditor() {
          var ds = useDraftStore()
          var s = useStore()
          var cc = useCiteConfirm()
          // 逻辑：标题/时间线/textarea/建议面板（数据层与面板共享）
          var btnSt = { fontSize: ff(12), padding: '3px 10px', border: '1px solid #ccc', borderRadius: '4px', background: '#fff', cursor: 'pointer' }
          return React.createElement('div', { key: 'draft', style: { display: 'flex', flexDirection: 'column', height: '100%', border: '1px solid #e0e0e0', borderRadius: '8px', overflow: 'hidden', background: '#fff' } }, [
            React.createElement('div', { key: 'dhead', style: { display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 10px', background: '#f5f5f5', borderBottom: '1px solid #e0e0e0' } }, [
              React.createElement('span', { key: 'dt', style: { fontWeight: 600, fontSize: ff(12) } }, '📝 论文草稿'),
              React.createElement('span', { key: 'dname', style: { fontSize: ff(12), color: '#333', fontWeight: 500 } }, ds.current || '（无草稿）'),
              React.createElement('button', { key: 'dsave', onClick: saveDraft, disabled: ds.saving, style: btnSt }, ds.saving ? '保存中…' : '💾 保存'),
              React.createElement('button', { key: 'dupload', onClick: uploadDraft, style: btnSt, title: '上传 docx/md 草稿，替换当前草稿（保留版本记录）' }, '↑ 上传草稿'),
              React.createElement('button', { key: 'dtl', onClick: function () { draftStore.set({ timelineOpen: !ds.timelineOpen }) }, style: btnSt }, '🕘 时间线' + (ds.versions.length ? ' (' + ds.versions.length + ')' : '')),
              React.createElement('span', { key: 'dv', style: { fontSize: ff(11), color: '#55624d', marginLeft: 'auto' } }, (ds.dirty ? '未保存 · ' : '') + 'v' + ds.version),
            ]),
            ds.timelineOpen ? React.createElement('div', { key: 'tl', style: { maxHeight: '140px', overflowY: 'auto', padding: '6px 10px', background: '#fafbfc', borderBottom: '1px solid #e0e0e0' } }, [
              React.createElement('div', { key: 'tlt', style: { fontSize: ff(12), fontWeight: 600, marginBottom: '4px' } }, '📜 版本时间线'),
              ds.versions.length === 0 ? React.createElement('div', { key: 'tle', style: { fontSize: ff(11), color: '#5f6b63' } }, '暂无版本记录') : null,
              ds.versions.map(function (v) {
                var actionLabel = v.action === 'edit' ? '✏️ 编辑' : v.action === 'insert_ref' ? '📎 插入引用' : v.action === 'rollback' ? '↩️ 回滚' : v.action === 'create' ? '🆕 创建' : '📝 ' + (v.action || '')
                return React.createElement('div', { key: 'v' + v.draft + v.version, style: { display: 'flex', gap: '8px', alignItems: 'center', padding: '3px 0', borderBottom: '1px dashed #eee', fontSize: ff(11) } }, [
                  React.createElement('span', { key: 'vn', style: { fontWeight: 700, color: '#4a90d9', width: '36px' } }, 'v' + v.version),
                  React.createElement('span', { key: 'va', style: { flex: 1 } }, actionLabel + (v.note ? ' · ' + v.note : '')),
                  React.createElement('span', { key: 'vts', style: { fontSize: ff(10), color: '#aaa' } }, v.ts),
                ])
              }),
            ]) : null,
            React.createElement('div', { key: 'dbody', style: { position: 'relative', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' } }, [
              React.createElement('textarea', {
                key: 'ta', id: 'cr-wb-panel-draft-ta', value: ds.content,
                onChange: function (e) { draftStore.set({ content: e.target.value, dirty: true }) },
                onSelect: function (e) { try { draftStore.set({ cursor: e.target.selectionStart }) } catch (err) { } },
                onMouseUp: function (e) { try { draftStore.set({ cursor: e.target.selectionStart }) } catch (err) { } },
                onPaste: function (e) { handleDraftPaste(e, ds.content) },
                onKeyDown: function (e) { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveDraft() } },
                placeholder: '论文草稿（Markdown）...\n\n保存：Ctrl+S 或点「保存」→ 快照 + 记录改动 + 生成阶段 DOCX。\n引用标记：从「引用链」面板点「插入草稿」，在光标处插入 [N]。',
                style: { flex: 1, minHeight: '80px', width: '100%', border: 'none', padding: '10px 14px', fontFamily: 'Consolas, "Courier New", monospace', fontSize: ff(13), resize: 'none', boxSizing: 'border-box' },
              }),
              // T16 引文登记确认浮卡（粘贴命中时右下非模态；确认登记 / 仅粘贴）
              cc.visible ? React.createElement('div', { key: 'citeCard', style: { position: 'absolute', right: '10px', bottom: '10px', width: '320px', maxWidth: '90%', background: '#fff', border: '1px solid #4a90d9', borderRadius: '8px', boxShadow: '0 4px 16px rgba(0,0,0,0.18)', padding: '10px 12px', zIndex: 20, fontSize: ff(12) } }, [
                React.createElement('div', { key: 'ch', style: { fontWeight: 600, marginBottom: '4px', color: '#333' } }, '📎 是否确认引用？'),
                React.createElement('div', { key: 'csrc', style: { fontSize: ff(11), color: '#4a90d9', marginBottom: '2px', wordBreak: 'break-all' } },
                  '来源：' + resolveDocTitle(s.items, cc.docId, '') + (cc.capture && cc.capture.page ? ' · 第 ' + cc.capture.page + ' 页' : '')),
                React.createElement('div', { key: 'csnip', style: { fontSize: ff(11), color: '#3a453f', lineHeight: 1.5, maxHeight: '60px', overflow: 'hidden', marginBottom: '6px' } },
                  '「' + String(cc.pasted || '').slice(0, 80) + '…」'),
                cc.error ? React.createElement('div', { key: 'cerr', style: { color: '#c62828', fontSize: ff(11), marginBottom: '4px' } }, cc.error) : null,
                React.createElement('div', { key: 'cbtns', style: { display: 'flex', gap: '6px', justifyContent: 'flex-end' } }, [
                  React.createElement('button', { key: 'skip', onClick: skipCite, disabled: cc.busy, style: { fontSize: ff(12), padding: '3px 10px', border: '1px solid #ccc', borderRadius: '4px', background: '#fff', cursor: 'pointer' } }, '仅粘贴'),
                  React.createElement('button', { key: 'ok', onClick: confirmCite, disabled: cc.busy, style: { fontSize: ff(12), padding: '3px 10px', border: '1px solid #4a90d9', borderRadius: '4px', background: '#4a90d9', color: '#fff', cursor: 'pointer' } }, cc.busy ? '登记中…' : '确认引用'),
                ]),
              ]) : null,
              // T10 疑似未登记引用建议面板（保存后台扫描 → 非模态浮卡；仅建议非空才显示）
              ds.suggest.visible && ds.suggest.items.length ? React.createElement('div', { key: 'suggCard', style: { position: 'absolute', left: '10px', bottom: '10px', right: '10px', maxHeight: '40%', overflowY: 'auto', background: '#fff', border: '1px solid #e0a030', borderRadius: '8px', boxShadow: '0 4px 16px rgba(0,0,0,0.18)', padding: '10px 12px', zIndex: 20, fontSize: ff(12) } }, [
                React.createElement('div', { key: 'sh', style: { fontWeight: 600, marginBottom: '3px', color: '#8a6d1a' } }, '📎 疑似未登记引用建议'),
                React.createElement('div', { key: 'shint', style: { fontSize: ff(11), color: '#5f6b63', marginBottom: '6px' } }, '保存时对照库内资料扫描（含项目输入资料）· 建议制，确认才登记'),
                ds.suggest.items.map(function (it) {
                  return React.createElement('div', { key: 's' + it.idx, style: { borderTop: '1px solid #f0f0f0', padding: '5px 0' } }, [
                    React.createElement('div', { key: 'smeta', style: { fontSize: ff(11), color: '#4a90d9', marginBottom: '2px', wordBreak: 'break-all' } },
                      '相似度 ' + Math.round((it.score || 0) * 100) + '% · ' + (it.title || it.doc_id || '')),
                    React.createElement('div', { key: 'ssnip', style: { fontSize: ff(11), color: '#3a453f', lineHeight: 1.5, maxHeight: '44px', overflow: 'hidden', marginBottom: '4px' } },
                      '「' + String(it.text || it.anchor || '').slice(0, 120) + '」'),
                    React.createElement('div', { key: 'sbtns', style: { display: 'flex', gap: '6px', justifyContent: 'flex-end' } }, [
                      React.createElement('button', { key: 'sno', onClick: function () { dismissSuggestion(it) }, disabled: ds.suggest.busy, style: { fontSize: ff(12), padding: '2px 10px', border: '1px solid #ccc', borderRadius: '4px', background: '#fff', cursor: 'pointer' } }, '忽略'),
                      React.createElement('button', { key: 'syes', onClick: function () { confirmSuggestion(it) }, disabled: ds.suggest.busy, style: { fontSize: ff(12), padding: '2px 10px', border: '1px solid #e0a030', borderRadius: '4px', background: '#e0a030', color: '#fff', cursor: 'pointer' } }, ds.suggest.busy ? '处理中…' : '确认引用'),
                    ]),
                  ])
                }),
              ]) : null,
            ]),
          ])
        }
        // 面板文献工作台子 Tab 容器（引用链/速览卡/检索/输入资料）
        // 面板文献工作台子 Tab 内容（Tab 条已提升至工作台顶行与项目/格式同行，本组件只渲染面板内容）
        function PanelWorkbenchTabs() {
          var pl = usePanelLitTab()
          var sub = null
          if (pl.current === 'citations') sub = React.createElement(CitationsPanelBody, { key: 'cit' })
          else if (pl.current === 'inputs') sub = React.createElement(InputsPanelBody, { key: 'inp' })
          else if (pl.current === 'digest') sub = React.createElement(DigestPanelBody, { key: 'dg' })
          else if (pl.current === 'search') sub = React.createElement(SearchPanelBody, { key: 'sr' })
          return React.createElement('div', { key: 'tabcontent', style: { height: '100%', minHeight: 0, overflowY: 'auto', padding: '6px 4px', boxSizing: 'border-box' } }, sub)
        }


        // ── T12 增补：details 右栏（论文工作台面板，priority -1 覆盖 DetailsPanel）──
        // 机制：AppFrame 三列（sidebar|center|details）；details 打开自动挤压会话区，关闭收起但保持挂载
        // 判定：论文工作区（cwd 下存在 papers 数据根）→ 会话切换自动 openDetails 弹草稿
        // 视图：📝 草稿（默认）| 📚 文献工作台 | 📄 PDF 预览占位 | 📊 入库监控占位；关闭文献自动回草稿（草稿持久化）
        try {
          var layoutSvc = ctx.get('layout')
          // ── 方案C：shell.overlay 工作台面板（对齐 cr-doc-review：fixed 右侧浮层 + grid 强制挤压 + 宽屏扩展）──
          // 面板 CSS：打开时注入（面板宽度 + 三列布局让位），关闭时移除（弹回）
          var WB_CSS_ID = 'cr-paper-workbench-css'
          var WB_PANEL_CSS = [
            '.cr-wb-panel{position:fixed;top:0;right:0;bottom:0;width:66vw;z-index:9999;background:#fafbfc;border-left:1px solid #ddd;box-shadow:-4px 0 24px rgba(0,0,0,0.12);display:flex;flex-direction:column;overflow:hidden;font-family:system-ui,sans-serif}',
            '[class*="_frame"]{grid-template-columns:auto minmax(0,1fr) 66vw !important}',
            '@media (min-width:2000px){.cr-wb-panel{width:70vw}}',
            '@media (min-width:2000px){[class*="_frame"]{grid-template-columns:auto minmax(0,1fr) 70vw !important}}',
'@media (max-width:1280px){.cr-wb-panel{width:82vw}}',
'@media (max-width:1280px){[class*="_frame"]{grid-template-columns:auto minmax(0,1fr) 82vw !important}}',
'@media (max-width:900px){.cr-wb-panel{width:100vw}}',
'@media (max-width:900px){[class*="_frame"]{grid-template-columns:auto minmax(0,1fr) 100vw !important}}',
            '.cr-pdf-textlayer{position:absolute;inset:0;overflow:hidden;line-height:1;text-align:initial;text-size-adjust:none;transform-origin:0 0;caret-color:CanvasText;z-index:1;user-select:text;-webkit-user-select:text;color:transparent}',
            '.cr-pdf-textlayer span,.cr-pdf-textlayer br{color:transparent;position:absolute;white-space:pre;cursor:text;transform-origin:0% 0%}',
            '.cr-pdf-textlayer ::selection{background:rgba(26,115,232,0.4);color:transparent}',
          ].join('\n')
          function applyWbCss() {
            if (document.getElementById(WB_CSS_ID)) return
            var tag = document.createElement('style')
            tag.id = WB_CSS_ID
            tag.textContent = WB_PANEL_CSS
            document.head.appendChild(tag)
          }
          function removeWbCss() {
            var tag = document.getElementById(WB_CSS_ID)
            if (tag) tag.remove()
          }
          // 打开工作台：注入 CSS（强制挤压会话区）+ openDetails（details 列状态同步）
          function openWorkbench() {
            try {
              detailsView.set({ open: true })
              applyWbCss()
              if (layoutSvc) layoutSvc.openDetails()
              reportState({ event: 'workbench-open' })
            } catch (e) { reportState({ event: 'workbench-open-error', error: String(e).slice(0, 200) }) }
          }
          function closeWorkbench() {
            try {
              detailsView.set({ open: false })
              removeWbCss()
              if (layoutSvc) layoutSvc.closeDetails()
              reportState({ event: 'workbench-close' })
            } catch (e) { reportState({ event: 'workbench-close-error', error: String(e).slice(0, 200) }) }
          }
          // 右栏视图 store（面板内部状态，切换后保持；open 控制面板显隐——对齐 cr-doc-review open 门控）
          var detailsView = {
            current: 'workbench',
            open: false,
            listeners: [],
            get: function () { return this },
            set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
            subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
          }
          function useDetailsView() {
            var t = React.useState(0)
            React.useEffect(function () { return detailsView.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
            return detailsView
          }
          function setDetailsView(v) {
            detailsView.current = v
            detailsView.listeners.forEach(function (fn) { fn() })
            reportState({ event: 'details-view', view: v })
          }
          // T15 增补：面板文献工作台子 Tab（引用链/输入资料）
          var panelLitTab = {
            current: 'citations',
            listeners: [],
            get: function () { return this },
            set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
            subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
          }
          function usePanelLitTab() {
            var t = React.useState(0)
            React.useEffect(function () { return panelLitTab.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
            return panelLitTab
          }
          // ── T13：PDF 预览（blob + PDF.js，同源 /cr-paper/vendor/pdfjs/*）──
          // 数据源：host /cr-paper/docs-file?id=（二进制透传，管线 /docs/{id}/file → blob）
          // PDF.js：管线 webui 已托管（Apache-2.0）；经 /cr-paper/vendor/pdfjs/* 同源加载（规避跨源 script/worker）
          // 选区捕获（T16 前置钩子）：textLayer 选中 → 写入 captureSlot（引用此段登记在 T16 接入）
          var pdfStore = {
            docId: '',            // 当前预览文档（'' = 未加载）
            source: 'doc',        // 文档来源：'doc' = 管线 docs 库（/cr-paper/docs-file）；'input' = 项目输入资料（/cr-paper/input-file）
            name: '',             // input 模式：文件名（doc 模式留空）
            rev: 0,               // 加载代次（每次 openPdfDoc 递增；缓存命中也递增 → 强制重渲染）
            page: 1,              // 当前物理页
            pageCount: 0,
            zoom: 1,              // 缩放倍率（1 = 适宽基线）
            loading: false,
            error: '',
            origLabel: null,      // 原始页码（当前页动态计算；null = 未检测/不可信）
            origInfo: null,       // 原始页码偏移 { startIdx, startVal }（startVal + (page-1-startIdx) = 原页）
            captureSlot: null,    // T16 前置：选区捕获 { docId, page, text }
            listeners: [],
            get: function () { return this },
            set: function (patch) { Object.assign(this, patch); this.listeners.forEach(function (fn) { fn() }) },
            subscribe: function (fn) { this.listeners.push(fn); return function () { this.listeners = this.listeners.filter(function (x) { return x !== fn }) }.bind(this) },
          }
          function usePdfStore() {
            var t = React.useState(0)
            React.useEffect(function () { return pdfStore.subscribe(function () { t[1](function (n) { return n + 1 }) }) }, [])
            return pdfStore
          }
          // 加载 PDF.js（动态注入 <script>，同源；幂等：已就绪/加载中直接复用）
          var pdfjsPromise = null
          function loadPdfJs() {
            if (window.__crPdfjsLib) return Promise.resolve(window.__crPdfjsLib)
            if (pdfjsPromise) return pdfjsPromise
            pdfjsPromise = new Promise(function (resolve, reject) {
              var s = document.createElement('script')
              s.src = '/cr-paper/vendor/pdfjs/pdf.min.js'
              s.onload = function () {
                var lib = window.pdfjsLib
                if (!lib) { reject(new Error('pdf.min.js 加载后未暴露 window.pdfjsLib')); return }
                lib.GlobalWorkerOptions.workerSrc = '/cr-paper/vendor/pdfjs/pdf.worker.min.js'
                window.__crPdfjsLib = lib
                resolve(lib)
              }
              s.onerror = function () { pdfjsPromise = null; reject(new Error('pdf.min.js 加载失败（/cr-paper/vendor 路由不可达？）')) }
              document.head.appendChild(s)
            })
            return pdfjsPromise
          }
          // 打开指定文档 PDF（fetch blob → getDocument → 渲染首页；页码回跳锚点 page）
          // single-slot 缓存：Tab pdfPane 与面板 pdf 视图双实例共享同一文档，避免重复 fetch/解析
          // inflight 去重：双实例同时触发时复用同一 promise（pdfStore 共享，目标页一致）
          var pdfDocCache = { docId: '', doc: null, origInfo: null }
          var inflightPdf = { docId: '', promise: null }
          async function openPdfDoc(docId, targetPage, opts) {
            var source = (opts && opts.source) || 'doc'
            var fileName = (opts && opts.name) || ''
            if (!docId) { pdfStore.set({ docId: '', page: 1, pageCount: 0, error: '', loading: false }); return }
            var cacheKey = source + ':' + docId
            if (pdfDocCache.docId === cacheKey && pdfDocCache.doc) {
              const hitPage = targetPage > 0 ? targetPage : 1
              const hitOrig = pdfDocCache.origInfo
              pdfStore.set({ docId: docId, source: source, name: fileName, rev: pdfStore.rev + 1, loading: false, error: '', page: hitPage, pageCount: pdfDocCache.doc.numPages, origInfo: hitOrig, origLabel: hitOrig ? (hitOrig.startVal + (hitPage - 1 - hitOrig.startIdx)) : null, captureSlot: null })
              reportState({ event: 'pdf-loaded', docId: docId, source: source, pageCount: pdfDocCache.doc.numPages, cached: true })
              return pdfDocCache.doc
            }
            if (inflightPdf.docId === cacheKey && inflightPdf.promise) return inflightPdf.promise
            pdfStore.set({ docId: docId, source: source, name: fileName, rev: pdfStore.rev + 1, loading: true, error: '', page: targetPage > 0 ? targetPage : 1, origLabel: null, captureSlot: null })
            try {
              const pdfjs = await loadPdfJs()
              const fetchUrl = (source === 'input')
                ? ('/cr-paper/input-file?id=' + encodeURIComponent(docId) + '&name=' + encodeURIComponent(fileName))
                : ('/cr-paper/docs-file?id=' + encodeURIComponent(docId))
              const resp = await fetch(fetchUrl, { cache: 'no-store' })
              if (!resp.ok) {
                let msg = 'HTTP ' + resp.status
                try { const j = await resp.json(); if (j && j.error) msg = j.error } catch (e) { }
                throw new Error(msg)
              }
              const data = await resp.arrayBuffer()
              const doc = await pdfjs.getDocument({ data }).promise
              // 原始页码偏移检测（PageLabels 优先；失败不阻塞渲染，显示物理页；翻页时动态计算原页）
              let origInfo = null
              try {
                const labels = await doc.getPageLabels()
                if (Array.isArray(labels) && labels.length >= 1) {
                  const n = parseInt(labels[0], 10)
                  if (Number.isInteger(n) && n > 0) origInfo = { startIdx: 0, startVal: n }
                }
              } catch (e) { origInfo = null }
              pdfDocCache = { docId: cacheKey, doc: doc, origInfo: origInfo }
              pdfStore.set({ rev: pdfStore.rev + 1, pageCount: doc.numPages, loading: false })
              reportState({ event: 'pdf-loaded', docId: docId, source: source, pageCount: doc.numPages, bytes: data.byteLength })
              const curPage = targetPage > 0 ? targetPage : 1
              pdfStore.set({ origInfo: origInfo, origLabel: origInfo ? (origInfo.startVal + (curPage - 1 - origInfo.startIdx)) : null })
              return doc
            } catch (err) {
              pdfStore.set({ loading: false, error: 'PDF 加载失败: ' + String(err.message || err) })
              reportState({ event: 'pdf-error', docId: docId, source: source, error: String(err.message || err).slice(0, 200) })
              return null
            } finally {
              inflightPdf = { docId: '', promise: null }
            }
          }
          // ── T13：PdfPreview 组件（canvas 渲染 + 翻页/缩放/页码 + textLayer 选区捕获钩子）──
          // 无 JSX：React.createElement 组装；canvas/textLayer 用 ref + effect 操作 DOM
          function PdfPreview() {
            var ps = usePdfStore()
            var canvasRef = React.useRef(null)
            var wrapRef = React.useRef(null)
            var viewRef = React.useRef(null)     // 外层滚动容器（适宽计算基准：可用视口宽）
            var docRef = React.useRef(null)      // PDFDocumentProxy
            var scaleRef = React.useRef(1.2)     // 当前缩放（适宽后基线 1.0 语义）
            var renderTaskRef = React.useRef(null)
            var textLayerRef = React.useRef(null)
            // 渲染指定页（canvas + textLayer 叠加；物理页码显示 + 原始页码参考）
            function renderPage(pageNum) {
              var doc = docRef.current
              var canvas = canvasRef.current
              var wrap = wrapRef.current
              if (!doc || !canvas || !wrap) return
              if (renderTaskRef.current) { try { renderTaskRef.current.cancel() } catch (e) { } renderTaskRef.current = null }
              doc.getPage(pageNum).then(function (page) {
                var scale = scaleRef.current
                var viewport = page.getViewport({ scale: scale })
                canvas.width = viewport.width
                canvas.height = viewport.height
                canvas.style.width = viewport.width + 'px'
                canvas.style.height = viewport.height + 'px'
                canvas.style.background = '#fff'
                // 清旧 textLayer（绝对定位覆盖在 canvas 上）
                var old = wrap.querySelector('.cr-pdf-textlayer')
                if (old) old.remove()
                textLayerRef.current = null
                var ctx = canvas.getContext('2d')
                var task = page.render({ canvasContext: ctx, viewport: viewport })
                renderTaskRef.current = task
                task.promise.then(function () {
                  renderTaskRef.current = null
                  // textLayer：文字可选中（T16 选区捕获钩子前置）
                  page.getTextContent().then(function (tc) {
                    try {
                      var lib = window.__crPdfjsLib
                      if (!lib) return
                      var tl = document.createElement('div')
                      tl.className = 'cr-pdf-textlayer'
                      tl.style.cssText = 'position:absolute;top:0;left:0;width:' + viewport.width + 'px;height:' + viewport.height + 'px;overflow:hidden;line-height:1;color:transparent;user-select:text;z-index:1;'
                      tl.style.setProperty('--scale-factor', String(scale))
                      wrap.appendChild(tl)
                      var task2 = lib.renderTextLayer({ textContentSource: tc, container: tl, viewport: viewport })
                      task2.promise.then(function () {
                        textLayerRef.current = tl
                        var spans = tl.querySelectorAll('span').length
                        reportState({ event: 'pdf-page-rendered', docId: ps.docId, page: pageNum, spans: spans })
                      }).catch(function (e) { reportState({ event: 'pdf-textlayer-error', error: String(e && e.message || e).slice(0, 120) }) })
                    } catch (e) { reportState({ event: 'pdf-textlayer-error', error: String(e && e.message || e).slice(0, 120) }) }
                  })
                }).catch(function (e) {
                  if (!(e && e.name === 'RenderingCancelledException')) reportState({ event: 'pdf-render-error', page: pageNum, error: String(e && e.message || e).slice(0, 120) })
                })
                // 页码/原始页码上报（origLabel 按当前页动态计算；翻页后自动更新）
                var oi = pdfStore.origInfo
                var orig = oi ? (oi.startVal + (pageNum - 1 - oi.startIdx)) : null
                if (orig !== ps.origLabel) pdfStore.set({ origLabel: orig })
                reportState({ event: 'page-changed', docId: ps.docId, page: pageNum, pageCount: doc.numPages, origLabel: orig })
              }).catch(function (e) { reportState({ event: 'pdf-page-error', page: pageNum, error: String(e && e.message || e).slice(0, 120) }) })
            }
            // 文档加载：docId 变化 → openPdfDoc 加载并把 doc 存入 docRef（渲染由下方 render effect 驱动）
            var prevDocRef = React.useRef('')
            React.useEffect(function () {
              var loadKey = (ps.source || 'doc') + ':' + ps.docId + ':' + (ps.name || '')
              if (ps.docId && loadKey !== prevDocRef.current) {
                prevDocRef.current = loadKey
                docRef.current = null
                openPdfDoc(ps.docId, ps.page, { source: ps.source || 'doc', name: ps.name || '' }).then(function (doc) {
                  if (doc) docRef.current = doc
                })
              }
            }, [ps.docId, ps.source, ps.name])
            // 渲染 effect：加载完成（loading=false & pageCount>0）或页码/rev 变化 → renderPage
            // 关键 1：effect 在 DOM commit 后运行，canvas 必然已挂载（避免 .then 时序下 canvasRef 为 null 的空白页）
            // 关键 2：rev = 加载代次——缓存命中/重挂载时 docRef 重新赋值，rev 变化强制重渲染（修复：关面板后重开同文档无内容）
            var prevRenderKey = React.useRef('')
            React.useEffect(function () {
              if (!ps.docId || !docRef.current || ps.loading || ps.error || ps.pageCount <= 0) return
              var key = ps.rev + '|' + ps.page + '|' + ps.pageCount
              if (key === prevRenderKey.current) return
              prevRenderKey.current = key
              renderPage(ps.page)
            }, [ps.docId, ps.rev, ps.page, ps.pageCount, ps.loading, ps.error])
            // 选区捕获钩子（T16 前置：textLayer 内选中 ≥5 字 → captureSlot）
            React.useEffect(function () {
              // 2026-08-17 修复：监听绑 document（事件委托）——原 wrap 监听实测 mouseup 不触发
              // （wrap 可能在加载/渲染切换时被 React 重建导致监听丢失）；document 级监听永不失效，
              // handler 内再判断选区是否在 textLayer 内（多 PdfPreview 实例无碍：非 textLayer 选区快速 return）。
              function onMouseUp() {
                var sel = window.getSelection()
                var text = sel ? sel.toString().trim() : ''
                if (text.length < 5) return
                var inLayer = false
                var node = sel.anchorNode
                while (node && node !== document) {
                  if (node.classList && node.classList.contains('cr-pdf-textlayer')) { inLayer = true; break }
                  node = node.parentNode
                }
                if (!inLayer || !ps.docId) return
                var cap = { docId: ps.docId, page: ps.page, text: sliceCaptureText(text), ts: Date.now(), source: ps.source || 'doc', name: ps.source === 'input' ? (ps.name || '') : '' }
                pdfStore.set({ captureSlot: cap })
                recordCopyCapture(cap)
                reportState({ event: 'pdf-capture', docId: ps.docId, page: ps.page, source: cap.source, len: text.length })
                // T16 U3：选区即显示会话框来源 chip（复制即提示，不弹窗不打断；标题懒解析）
                try { showCiteChip(cap, resolveDocTitle(s_itemsForTitle(), cap.docId, '')) } catch (e) { showCiteChip(cap, cap.docId) }
              }
              document.addEventListener('mouseup', onMouseUp)
              return function () { document.removeEventListener('mouseup', onMouseUp) }
            }, [ps.docId, ps.page])
            // 控制条按钮
            function btn(label, onClick, disabled, title) {
              return React.createElement('button', {
                onClick: onClick,
                disabled: !!disabled,
                title: title || '',
                style: { fontSize: ff(12), padding: '2px 9px', border: '1px solid #ccc', borderRadius: '4px', background: '#fff', cursor: 'pointer', flexShrink: 0 },
              }, label)
            }
            var total = ps.pageCount || 0
            var controls = React.createElement('div', { key: 'ctl', style: { display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 2px', flexShrink: 0, flexWrap: 'wrap' } }, [
              btn('⏮ 首页', function () { if (total > 0) { pdfStore.set({ page: 1 }); reportState({ event: 'page-changed', docId: ps.docId, page: 1, pageCount: total }) } }, ps.loading || !total),
              btn('◀ 上一页', function () { if (ps.page > 1) pdfStore.set({ page: ps.page - 1 }) }, ps.loading || ps.page <= 1),
              React.createElement('span', { key: 'pn', style: { fontSize: ff(12), color: '#444', whiteSpace: 'nowrap' } },
                '第 ' + (ps.page || 1) + ' / ' + (total || '–') + ' 页' + (ps.origLabel ? '（原第 ' + ps.origLabel + ' 页）' : '')),
              btn('下一页 ▶', function () { if (ps.page < total) pdfStore.set({ page: ps.page + 1 }) }, ps.loading || ps.page >= total),
              btn('🔍−', function () { scaleRef.current = Math.max(0.5, scaleRef.current * 0.85); pdfStore.set({ zoom: Math.round(scaleRef.current * 100) }); reportState({ event: 'zoom-changed', docId: ps.docId, zoom: Math.round(scaleRef.current * 100) }); if (docRef.current) renderPage(ps.page) }, ps.loading),
              btn('🔍+', function () { scaleRef.current = Math.min(3, scaleRef.current * 1.18); pdfStore.set({ zoom: Math.round(scaleRef.current * 100) }); reportState({ event: 'zoom-changed', docId: ps.docId, zoom: Math.round(scaleRef.current * 100) }); if (docRef.current) renderPage(ps.page) }, ps.loading),
              btn('适宽', function () {
                var doc = docRef.current
                if (!doc) return
                doc.getPage(ps.page).then(function (pg) {
                  var vp = pg.getViewport({ scale: 1 })
                  // 适宽基准 = 外层可用视口宽（padding 8*2 扣除；wrap 宽度跟随 canvas，不能作基准）
                  var avail = viewRef.current ? viewRef.current.clientWidth : (wrapRef.current ? wrapRef.current.clientWidth : 600)
                  var cw = Math.max(200, avail - 16)
                  scaleRef.current = Math.max(0.5, cw / vp.width)
                  pdfStore.set({ zoom: 100 })
                  reportState({ event: 'zoom-changed', docId: ps.docId, zoom: 100, fit: true, avail: avail })
                  renderPage(ps.page)
                })
              }, ps.loading),
            ])
            var body = null
            if (!ps.docId) {
              body = React.createElement('div', { key: 'empty', style: { color: '#5f6b63', fontSize: ff(12), padding: '20px 8px', textAlign: 'center', lineHeight: 1.8, whiteSpace: 'pre-line' } },
                '📄 未打开文档\n从「引用链」点「第 N 页 ↗」或「已读原文」打开对应 PDF。')
            } else if (ps.loading) {
              body = React.createElement('div', { key: 'loading', style: { color: '#3a453f', fontSize: ff(12), padding: '20px 8px', textAlign: 'center' } }, '正在加载 PDF…')
            } else if (ps.error) {
              body = React.createElement('div', { key: 'err', style: { color: '#c62828', fontSize: ff(12), padding: '20px 8px', textAlign: 'center', lineHeight: 1.8 } }, ps.error)
            } else {
              body = React.createElement('div', { key: 'view', ref: viewRef, style: { position: 'relative', flex: 1, minHeight: 0, overflow: 'auto', background: '#eef0f2', padding: '8px', display: 'flex', justifyContent: 'center' } }, [
                React.createElement('div', { key: 'wrap', ref: wrapRef, style: { position: 'relative', margin: '0 auto', boxShadow: '0 2px 10px rgba(0,0,0,0.35)' } }, [
                  React.createElement('canvas', { key: 'cv', ref: canvasRef, style: { display: 'block' } }),
                ]),
              ])
            }
            return React.createElement('div', { key: 'pdfroot', style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 } }, [controls, body])
          }
          // 论文工作区判定（会话切换时调用；AppFrame 切换会话自动 closeDetails，需在其后重开）
          // 单一事实来源 = host /cr-paper/paper-workspace（host 按 papersRoots 判定，client 不本地猜）
          function checkPaperWorkspace(cwd, cb) {
            if (!cwd) { cb(null); return }
            fetch('/cr-paper/paper-workspace?cwd=' + encodeURIComponent(cwd), { cache: 'no-store' })
              .then(function (r) { return r.json() })
              .then(function (d) { cb(d && d.isPaper ? d : null) })
              .catch(function (e) { cb(null) })
          }
          function currentCwd(props) {
            var us = props && props.useSessions
            if (typeof us !== 'function') { reportState({ event: 'cwd-diag', reason: 'no-useSessions', hasProps: !!props }) ; return '' }
            try {
              var snap = us(function (x) { return x })
              var cur = snap && snap.current
              var curObj = cur && snap.byId && snap.byId[cur] ? snap.byId[cur] : null
              var cwd = curObj ? String(curObj.cwd || '') : ''
              if (!cwd) reportState({ event: 'cwd-diag', reason: 'empty-cwd', curId: cur || null, hasCurObj: !!curObj })
              else reportState({ event: 'cwd-diag', reason: 'has-cwd', cwdTail: cwd.slice(-50) })
              return cwd
            } catch (e) { reportState({ event: 'cwd-diag', reason: 'err', error: String(e).slice(0, 120) }); return '' }
          }
          // 1) 会话头部按钮：论文工作台开关（openWorkbench/closeWorkbench）
          slots.inject('conversation.session.header.actions', function () {
            return slots.register(
              { name: 'conversation.session.header.actions', id: 'cr-paper-workbench-toggle', order: 100, priority: 0 },
              function () {
                return React.createElement('button', {
                  onClick: function () { openWorkbench() },
                  style: { fontSize: ff(12), padding: '3px 10px', border: '1px solid #4a90d9', borderRadius: '4px', background: '#fff', color: '#4a90d9', cursor: 'pointer' },
                }, '📚 论文工作台')
              }
            )
          })
          // 1.1) 侧边栏底部动作区：新建论文项目（左栏可见，任何会话可直接建，不需先打开工作台）
          slots.inject('sidebar.footer.action', function () {
            return slots.register(
              { name: 'sidebar.footer.action', id: 'cr-paper-create-paper', order: 30, priority: 0 },
              function () {
                // 外行友好：点「＋ 新建论文项目」展开内嵌表单（输入题目），不用浏览器 prompt 弹窗。
                var openRef = React.useState(false); var open = openRef[0]; var setOpen = openRef[1]
                var titleRef = React.useState(''); var title = titleRef[0]; var setTitle = titleRef[1]
                var inputOnChange = function (e) { setTitle(e.target.value) }
                var submit = function () {
                  setOpen(false); setTitle(''); createPaper(title)
                }
                var cancel = function () { setOpen(false); setTitle('') }
                var openBtn = React.createElement('button', {
                  key: 'newpaper-btn',
                  onClick: function () { setOpen(true) },
                  style: { fontSize: ff(12), padding: '6px 10px', width: '100%', border: '1px solid #e0a030', borderRadius: '6px', background: '#fff', color: '#b7791f', cursor: 'pointer', textAlign: 'left' },
                }, '＋ 新建论文项目')
                if (!open) return React.createElement('div', { key: 'newpaper-wrap' }, openBtn)
                // 展开的内嵌表单：文本框 + 创建/取消
                var input = React.createElement('input', {
                  key: 'np-input', type: 'text', value: title, placeholder: '输入你的论文题目…', onChange: inputOnChange, autoFocus: true,
                  onKeyDown: function (e) { if (e.key === 'Enter') submit() },
                  style: { fontSize: ff(12), padding: '6px 8px', width: '100%', boxSizing: 'border-box', border: '1px solid #ccc', borderRadius: '6px', marginBottom: '6px' },
                })
                var createBtn = React.createElement('button', {
                  key: 'np-create', onClick: submit,
                  disabled: !title.trim(),
                  style: { fontSize: ff(12), padding: '5px 10px', border: '1px solid #e0a030', borderRadius: '6px', background: '#e0a030', color: '#fff', cursor: title.trim() ? 'pointer' : 'not-allowed', marginRight: '4px' },
                }, '创建')
                var cancelBtn = React.createElement('button', {
                  key: 'np-cancel', onClick: cancel,
                  style: { fontSize: ff(12), padding: '5px 10px', border: '1px solid #ccc', borderRadius: '6px', background: '#fff', color: '#666', cursor: 'pointer' },
                }, '取消')
                var hint = React.createElement('div', { key: 'np-hint', style: { fontSize: ff(11), color: '#888', marginBottom: '4px' } }, '给你的论文起个名字，创建后会打开一篇带引导的草稿')
                return React.createElement('div', { key: 'newpaper-wrap' }, openBtn,
                  React.createElement('div', { key: 'np-form', style: { marginTop: '6px', borderTop: '1px dashed #ddd', paddingTop: '6px' } }, hint, input,
                    React.createElement('div', { key: 'np-actions' }, createBtn, cancelBtn)))
              }
            )
          })
          // 1.5) T16 U3：会话框来源 chip（conversation.composer.dock——输入区附加，复制即提示不打断）
          slots.inject('conversation.composer.dock', function () {
            return slots.register(
              { name: 'conversation.composer.dock', id: 'cr-paper-cite-chip', order: 20, label: '来源 chip' },
              function (props) {
                var chip = useCiteChip()
                if (!chip.visible) return null
                var inputActions = props && props.inputActions
                // useInput 是 hook：渲染顶层调用（快照供 insertCite 使用）
                var inputSnap = null
                if (props && typeof props.useInput === 'function') {
                  try { inputSnap = props.useInput(function (s) { return s }) } catch (e) { inputSnap = null }
                }
                var insertCite = function () {
                  try {
                    // 把引用注入输入区草稿（追加：来源 + 片段），用户可编辑后发送
                    var cur = (inputSnap && inputSnap.draft) || ''
                    var citeText = (chip.snippet || '').trim()
                    var add = (cur ? '\n' : '') + '（引用自「' + chip.title + '」' + (chip.page ? ' 第' + chip.page + '页' : '') + '）' + (citeText ? '\n' + citeText : '')
                    if (inputActions && typeof inputActions.setDraft === 'function') {
                      inputActions.setDraft(cur + add)
                      reportState({ event: 'chip-insert-input', docId: chip.docId, title: chip.title })
                    } else {
                      reportState({ event: 'chip-insert-input-error', error: 'inputActions 不可用' })
                    }
                  } catch (e) { reportState({ event: 'chip-insert-input-error', error: String(e && e.message || e).slice(0, 120) }) }
                }
                return React.createElement('div', {
                  key: 'chip',
                  style: {
                    display: 'inline-flex', alignItems: 'center', gap: '6px', margin: '0 4px 4px 0',
                    padding: '4px 10px', background: '#eef4fb', border: '1px solid #b8d4f0', borderRadius: '14px',
                    fontSize: ff(12), color: '#2c5f8a', maxWidth: '100%',
                  },
                }, [
                  React.createElement('span', { key: 'ic', style: { flexShrink: 0 } }, '📎'),
                  React.createElement('span', { key: 'tx', style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } },
                    '来源 ' + chip.title + (chip.page ? ' · 第' + chip.page + '页' : '')),
                  React.createElement('button', {
                    key: 'ins', onClick: insertCite, title: '插入到输入区',
                    style: { fontSize: ff(11), padding: '1px 6px', border: '1px solid #4a90d9', borderRadius: '10px', background: '#fff', color: '#4a90d9', cursor: 'pointer', flexShrink: 0 },
                  }, '插入'),
                  React.createElement('button', {
                    key: 'x', onClick: hideCiteChip, title: '移除',
                    style: { fontSize: ff(11), padding: '1px 5px', border: 'none', background: 'none', color: '#55624d', cursor: 'pointer', flexShrink: 0 },
                  }, '✕'),
                ])
              }
            )
          })
          // 2) shell.overlay：工作台面板（方案C，对齐 cr-doc-review；details 保留给工具详情）
          slots.inject('shell.overlay', function () {
            return slots.register(
              { name: 'shell.overlay', id: 'cr-paper-workbench-panel', order: 90 },
              function (props) {
                var dv = useDetailsView()
                // ── auto-open：渲染顶层取会话快照（hook 必须在顶层调用，不能进 effect——React #321 根因规避）──
                var sessionsSnap = null
                var curCwd = ''
                if (props && typeof props.useSessions === 'function') {
                  try { sessionsSnap = props.useSessions(function (s) { return s }) } catch (e) { sessionsSnap = null }
                }
                if (sessionsSnap) {
                  var curSid = sessionsSnap.current
                  var curObj = curSid && sessionsSnap.byId && sessionsSnap.byId[curSid] ? sessionsSnap.byId[curSid] : null
                  curCwd = curObj ? String(curObj.cwd || '') : ''
                }
                // 会话变化 → 论文工作区判定 → 自动打开（延迟避开 AppFrame 切换会话的 closeDetails）
                var prevCwdRef = React.useRef('')
                React.useEffect(function () {
                  if (curCwd && curCwd !== prevCwdRef.current) {
                    prevCwdRef.current = curCwd
                    checkPaperWorkspace(curCwd, function (d) {
                      if (d) {
                        setTimeout(function () {
                          try {
                            openWorkbench()
                            // 若工作区带 .paper-link（论文项目），加载该论文的草稿/引用链
                            if (d.paperId) {
                              reportState({ event: 'paper-link-loaded', paperId: d.paperId })
                              if (store.paperId !== d.paperId) { store.set({ paperId: d.paperId }); selectPaper(d.paperId) }
                              else { selectPaper(d.paperId) }
                            } else if (d.papers && d.papers.length) {
                              // 无 .paper-link 时回退：加载工作区下第一个论文项目
                              if (!store.paperId) { store.set({ paperId: d.papers[0] }); selectPaper(d.papers[0]) }
                            }
                            reportState({ event: 'auto-open', cwdPaper: true })
                          } catch (e) { reportState({ event: 'auto-open-error', error: String(e).slice(0, 200) }) }
                        }, 300)
                      } else {
                        reportState({ event: 'auto-skip', reason: 'not-paper-workspace', cwdTail: String(curCwd).slice(-40) })
                      }
                    })
                  }
                }, [curCwd])
                // 面板挂载：加载分析后端状态（顶栏切换按钮显示当前模式）
                React.useEffect(function () { loadBackend() }, [])
                // 分析后端（hook 必须在 early return 之前顶层调用——React hooks 数量一致性）
                var bs = useBackendStore()
                reportState({ event: 'wb-render', view: dv.current, open: dv.open })
                if (!dv.open) return null
                // 视图切换条
                var tabs = [
                  { id: 'workbench', label: '📝 工作台' },
                  { id: 'monitor', label: '📊 入库监控' },
                ]
                var tabBtns = tabs.map(function (tb) {
                  var active = dv.current === tb.id
                  return React.createElement('button', {
                    key: 'tb' + tb.id,
                    onClick: function () { setDetailsView(tb.id) },
                    style: {
                      padding: '4px 10px', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: ff(12),
                      background: active ? '#4a90d9' : 'transparent', color: active ? '#fff' : '#555',
                      fontWeight: active ? 600 : 400, marginRight: '2px', whiteSpace: 'nowrap',
                    },
                  }, tb.label)
                })
                // 内容区
                var content = null
                if (dv.current === 'workbench') {
                  content = React.createElement(PanelWorkbench, { key: 'wb' })
                } else if (dv.current === 'monitor') {
                  content = React.createElement(MonitorBody, { key: 'mon' })
                }
                // 分析后端切换（顶栏、✕ 旁：本地/云端/完全本地；影响速览卡提炼/引用核验/联网检索）
                var backendBtn = null
                if (bs.mode || bs.loading) {
                  var modeLabel = BACKEND_LABEL[bs.mode] || (bs.loading ? '…' : '')
                  backendBtn = React.createElement('div', { key: 'bk', style: { position: 'relative', marginLeft: 'auto', flexShrink: 0 } }, [
                    React.createElement('button', {
                      key: 'btn', onClick: function () { backendStore.set({ menuOpen: !bs.menuOpen }) }, disabled: bs.loading,
                      title: '分析后端：速览卡提炼/引用核验走本地模型或云端；「完全本地」关闭联网检索',
                      style: { fontSize: ff(11), padding: '2px 8px', border: '1px solid #4a90d9', borderRadius: '4px', background: '#fff', color: '#4a90d9', cursor: 'pointer', whiteSpace: 'nowrap' },
                    }, '⚙️ ' + modeLabel),
                    bs.menuOpen ? React.createElement('div', { key: 'menu', style: { position: 'absolute', right: '0', top: '26px', zIndex: 30, background: '#fff', border: '1px solid #ccc', borderRadius: '6px', boxShadow: '0 4px 16px rgba(0,0,0,0.15)', padding: '4px', minWidth: '170px' } }, [
                      [['local', '💻 本地（llama.cpp 模型）'], ['cloud', '☁️ 云端（API）'], ['off', '🔌 完全本地（禁联网）']].map(function (opt) {
                        var active = bs.mode === opt[0]
                        return React.createElement('button', {
                          key: opt[0], onClick: function () { setBackendMode(opt[0]) },
                          style: { display: 'block', width: '100%', textAlign: 'left', fontSize: ff(11), padding: '4px 10px', border: 'none', background: active ? '#e8f0fe' : '#fff', color: active ? '#4a90d9' : '#333', cursor: 'pointer', borderRadius: '4px' },
                        }, (active ? '✓ ' : '') + opt[1])
                      }),
                      bs.message ? React.createElement('div', { key: 'msg', style: { fontSize: ff(10), color: '#55624d', padding: '3px 8px', maxWidth: '220px', lineHeight: 1.4, borderTop: '1px dashed #e0e0e0', marginTop: '2px' } }, bs.message) : null,
                    ]) : null,
                  ])
                }
                return React.createElement('div', {
                  className: 'cr-wb-panel',
                  style: {
                    boxSizing: 'border-box', display: 'flex', flexDirection: 'column',
                    padding: '10px 12px', fontFamily: 'system-ui, sans-serif', fontSize: ff(13),
                  },
                }, [
                  React.createElement('div', { key: 'bar', style: { display: 'flex', alignItems: 'center', gap: '6px', borderBottom: '1px solid #e0e0e0', paddingBottom: '6px', marginBottom: '8px', flexWrap: 'wrap' } }, [
                    tabBtns,
                    backendBtn,
                    React.createElement('button', {
                      key: 'close',
                      onClick: function () { closeWorkbench() },
                      style: { flexShrink: 0, fontSize: ff(12), padding: '2px 8px', border: '1px solid #ccc', borderRadius: '4px', background: '#fff', cursor: 'pointer' },
                    }, '✕'),
                  ]),
                  React.createElement('div', { key: 'content', style: { flex: 1, minHeight: 0, overflowY: 'auto' } }, content),
                ])
              }
            )
          })
          reportState({ event: 'workbench-panel-registered', headerBtn: true, overlay: true })
        } catch (e) {
          reportState({ event: 'workbench-panel-error', error: String(e).slice(0, 300) })
        }
      },
    }

    module.exports = { default: plugin }
    return module.exports
  },
})