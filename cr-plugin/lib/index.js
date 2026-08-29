// cr-paper Host 半区（M1 集成，T6：paper 领域只读服务；T7：写操作代理）
// 能力：挂载 T4 domain（cr-plugin/domain），按管线数据根约定读取 paper 项目；
//       POST /cr-paper/pipe/* 透传代理到管线 HTTP（白名单 + token 鉴权，单一事实来源 = 管线）
// 数据根约定（单一事实来源 = 管线）：<工作区>/user-data/pipeline/papers/{paper_id}/
// 管线服务：127.0.0.1:8737（对齐 pipeline/config.yaml http_port），token 在 <数据根>/.api_token
import { Paper, Bibliography, Versions } from '../src/domain/index.js'
import { request as httpRequest } from 'node:http'
import { resolve as pathResolve } from 'node:path'
import { existsSync as fsExistsSync, readFileSync as fsReadFileSync, writeFileSync as fsWriteFileSync, mkdirSync as fsMkdirSync, unlinkSync as fsUnlinkSync } from 'node:fs'

// 数据根约定（单一事实来源 = 管线）；兼容两种布局：
//   - 开发工作区：<工作区>/user-data/pipeline/papers/{paper_id}/
//   - 便携包：    <工作区>/data/pipeline/papers/{paper_id}/（config.yaml pipeline_root='../data/pipeline'）
// 遍历时对每个工作区根都尝试这两个候选，命中任一即可定位项目。
const DATA_SUBDIRS = ['user-data/pipeline/papers', 'data/pipeline/papers']

// 管线 HTTP 基址（对齐 config.yaml http_port；改端口需同步此处）
const PIPE_HOST = '127.0.0.1'
const PIPE_PORT = 8737

// 写代理白名单（paper 级写操作；重型/上传/外部检索类暂不代理，M3 按需加项）
// 路径 = 管线路径（去 /cr-paper/pipe 前缀后）；paper_id 限安全字符，draft/input 名透传管线校验
// T14 增补：本地检索（/search、/search/quote）+ 联网检索（/web/*）+ 收藏（/web/collections*）POST
const PIPE_WHITELIST = [
  /^\/papers\/[A-Za-z0-9_\-]+\/rename$/,
  /^\/papers\/[A-Za-z0-9_\-]+\/bibliography\/(?:add|remove|format|verify)$/,
  /^\/papers\/[A-Za-z0-9_\-]+\/drafts$/,
  /^\/papers\/[A-Za-z0-9_\-]+\/drafts\/[^/]+$/,
  /^\/papers\/[A-Za-z0-9_\-]+\/drafts\/[^/]+\/(?:rollback|insert_mark)$/,
  /^\/papers\/[A-Za-z0-9_\-]+\/citesuggestions\/dismiss$/,
  /^\/papers\/[A-Za-z0-9_\-]+\/inputs\/[^/]+\/index$/,
  /^\/search$/,
  /^\/search\/quote$/,
  /^\/provider$/,
  /^\/web\/literature_search$/,
  /^\/web\/translate_query$/,
  /^\/web\/search$/,
  /^\/web\/oa_ingest$/,
  /^\/web\/collections$/,
  /^\/web\/collections\/[^/]+\/export$/,
]

console.log('[cr-paper] MODULE TOP-LEVEL LOADED (T6 domain service)')
export default {
  apply(ctx) {
    const fs = ctx.get('fs')
    const sp = ctx.get('sandboxPolicy')
    const wr = ctx.get('workspaceRegistry')
    const webServer = ctx.get('webServer')
    if (fs === undefined || webServer === undefined) {
      console.log('[cr-paper] EARLY RETURN: missing service')
      return
    }
    // 幂等：HMR 重复插入的条目跳过已注册路由
    if (webServer.exact && webServer.exact.has('/cr-paper/health')) {
      console.log('[cr-paper] routes already registered, skip duplicate entry')
      return
    }
    // 工作区根列表（遍历全部工作区，任意位置/任意目录名）
    let roots = []
    try {
      if (wr) { const list = wr.list(); if (list && list.length) roots = list.map((w) => (w && w.path) || '').filter(Boolean) }
    } catch (err) { console.log('[cr-paper] workspaceRegistry fail', String(err)) }
    if (roots.length === 0 && sp) roots = [sp.workspaceRoot]
    // papers 数据根候选：每个工作区根 × 两种数据根子目录（开发 + 便携包）
    const papersRoots = []
    for (const r of roots) {
      const nr = String(r).replace(/[\\/]+$/, '')
      for (const sub of DATA_SUBDIRS) papersRoots.push(`${nr}/${sub}`)
    }
    // 追加：读管线 config.yaml 的 pipeline_root（单一事实来源 = 管线配置）。
    // 便携包布局：管线进程 cwd = <包根>/pipeline，config.yaml 在那里，pipeline_root='../data/pipeline'。
    // 这样 cr-plugin 无需依赖"工作区子目录"派生，直接定位便携包数据根（<包根>/data/pipeline/papers）。
    // 候选路径：process.cwd()/config.yaml、以及各工作区根上游的 pipeline/config.yaml。
    // 用 Node 同步 fs（apply 非 async，不能 await），失败静默。
    {
      const confCands = []
      try { confCands.push(process.cwd() + '/config.yaml') } catch (e) { }
      for (const r of roots) { const nr = String(r).replace(/[\\/]+$/, ''); confCands.push(nr + '/pipeline/config.yaml') }
      // 便携包：DSH_HOME=<包根>/data/dsh → 便携包根 = DSH_HOME 上级的上级，config 在 <包根>/pipeline/config.yaml
      try {
        const home = String(process.env.DSH_HOME || '').replace(/[\\/]+$/, '')
        if (home) {
          // home 形如 <包根>/data/dsh；便携包根 = 再上溯两级（data/dsh）
          const pkgRoot = home.split(/[\\/]/).slice(0, -2).join('/')
          if (pkgRoot) { confCands.push(pkgRoot + '/pipeline/config.yaml'); confCands.push(pkgRoot + '/config.yaml') }
        }
      } catch (e) { }
      for (const cand of confCands) {
        try {
          if (!fsExistsSync(cand)) continue
          const txt = fsReadFileSync(cand, 'utf-8')
          const m = String(txt || '').match(/^pipeline_root:\s*['"]?([^'"\n]+)/m)
          if (!m) continue
          const rel = m[1].trim()
          const cfgDir = cand.slice(0, cand.lastIndexOf('/'))
          const abs = rel.indexOf('..') === 0 ? pathResolve(cfgDir, rel) : rel
          const pr = String(abs).replace(/[\\/]+$/, '')
          const paperRoot = pr + '/papers'
          if (!papersRoots.includes(paperRoot)) { papersRoots.push(paperRoot); console.log('[cr-paper] pipeline config dataRoot:', paperRoot) }
        } catch (e) { /* 该候选无 config，尝试下一个 */ }
      }
    }
    console.log('[cr-paper] papers roots:', JSON.stringify(papersRoots))

    const esc = (s) => String(s).replace(/\\/g, '/')
    const send = (res, code, obj) => {
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
      res.end(JSON.stringify(obj))
    }
    // 校验 paper id（只允许安全字符，拒绝路径穿越）
    const safeId = (raw) => /^[A-Za-z0-9_\-]+$/.test(String(raw || '')) ? String(raw) : null
    // 读取 paper 目录内文件文本：{ ok, text } / { ok:false, code }（papersRoot 已含数据根，rel 仅 id/文件名）
    async function readPaperFile(papersRoot, id, fname) {
      try {
        const rel = `${id}/${fname}`
        const target = await fs.resolve(rel, { cwd: papersRoot })
        const base = await fs.resolve('.', { cwd: papersRoot })
        if (!fs.contains(base, target)) return { ok: false, code: 403 }
        const info = await fs.stat(target)
        if (!info || info.isFile === false) return { ok: false, code: 404 }
        if (info.size > 512 * 1024) return { ok: false, code: 413 }
        const text = await fs.readText(target)
        return { ok: true, text }
      } catch (e) { return { ok: false, code: 404 } }
    }
    // 定位 paper 项目：遍历 papers roots，返回 { papersRoot } 或 null
    async function locatePaper(id) {
      for (const pr of papersRoots) {
        const r = await readPaperFile(pr, id, 'workbench-state.json')
        if (r.ok) return { papersRoot: pr }
      }
      return null
    }
    // 列出全部 paper 项目（workbench-state 摘要；损坏/缺失跳过）
    async function listPapers() {
      const out = []
      for (const pr of papersRoots) {
        let entries = []
        try {
          const dir = await fs.resolve('.', { cwd: pr })
          entries = await fs.listDir(dir)
        } catch (e) { continue }
        for (const entry of entries || []) {
          if (!entry || !entry.name || entry.isDirectory === false) continue
          if (!safeId(entry.name)) continue
          const r = await readPaperFile(pr, entry.name, 'workbench-state.json')
          if (!r.ok) continue
          const p = Paper.parse(r.text, entry.name)
          out.push({
            id: entry.name,
            createdAt: p.createdAt,
            lastUpdated: p.lastUpdated,
            inputsCount: p.inputsLog.length,
            draftsCount: p.draftLogicalNames().length,
            outputsCount: p.outputLogicalNames().length,
          })
        }
      }
      return out
    }

    webServer.register({ kind: 'exact', path: '/cr-paper/health', handler: async (req, res) => {
      send(res, 200, {
        plugin: 'cr-paper',
        status: 'ok',
        domainSchema: 1,
        domainMounted: true,
        papersRoots,
      })
    } })

    // ── T12 增补：论文工作区判定（cwd → isPaper + 可导入项目）──
    // 判定规则（2026-08-15 用户定案，2026-08-28 兼容便携包重构）：
    //   A. cwd 命中某 papersRoot 的工作区根（即 cwd 是该数据根里某项目所在目录的上溯根）→ 论文工作区；
    //   B. 开发期兼容 + 便携包：cwd 路径含 papers 顶层目录段（<工作区>/papers 或 <工作区>/data/papers）→ 论文工作区。
    // 数据根候选已含 user-data/pipeline/papers 与 data/pipeline/papers（见 DATA_SUBDIRS）。
    webServer.register({ kind: 'exact', path: '/cr-paper/paper-workspace', handler: async (req, res) => {
      try {
        const u = new URL(req.url, 'http://localhost')
        const cwd = String(u.searchParams.get('cwd') || '').trim()
        if (!cwd) return send(res, 400, { error: '缺少 cwd 参数' })
        const norm = cwd.replace(/[\/]+$/, '')
        let isPaper = false
        let paperRootHit = null
        // 判定 A：cwd 是某 papersRoot 的工作区根（对每个候选按对应 subdir 反向推导 wsRoot）
        for (const pr of papersRoots) {
          const prNorm = pr.replace(/[\/]+$/, '')
          let wsRoot = null
          for (const sub of DATA_SUBDIRS) {
            const suf = '/' + sub
            if (prNorm.endsWith(suf)) { wsRoot = prNorm.slice(0, prNorm.length - suf.length); break }
          }
          if (!wsRoot) continue
          if (norm === wsRoot || norm.indexOf(wsRoot + '/') === 0) { isPaper = true; paperRootHit = pr; break }
        }
        // 判定 B（兜底）：cwd 是 <工作区>/papers 或 <工作区>/data/papers 目录段（论文工作区子目录）
        if (!isPaper && /\/(?:data\/)?papers(?:\/|$)/.test(norm)) isPaper = true
        // 该工作区可导入的论文项目（paperRootHit 下已有项目 id）
        let papers = []
        if (paperRootHit) {
          try { const list = await listPapers(); papers = list.filter((p) => p.id).map((p) => p.id) } catch (e) { papers = [] }
        }
        // 若论文工作区目录带 .paper-link（create-workspace 写入的管线项目 id），返回 paperId，供 client 加载对应论文草稿
        let paperId = null
        if (isPaper) {
          try {
            const linkPath = norm + '/.paper-link'
            const info = await fs.stat(linkPath)
            if (info && info.isFile !== false) {
              const txt = await fs.readText(linkPath)
              const pid = String(txt || '').trim()
              if (pid && safeId(pid)) paperId = pid
            }
          } catch (e) { /* 无 .paper-link 走空 */ }
        }
        send(res, 200, { isPaper, paperRoot: paperRootHit, papers, paperId, cwd: norm })
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    // ── T12 增补：管线 /status 透传（入库监控数据源，只读）──
    webServer.register({ kind: 'exact', path: '/cr-paper/status', handler: async (req, res) => {
      try {
        const r = await pipeRequest('GET', '/status', null)
        res.writeHead(r.status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
        res.end(r.raw)
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    // ── T13 增补：PDF 二进制透传（管线 /docs/{id}/file → blob，PdfPreview 数据源）──
    // 与 pipeRequest 不同：二进制流（PDF），不做 JSON 解析；白名单仅放行 /docs/{id}/file 确切模式
    const BINARY_PIPE_WHITELIST = [
      /^\/docs\/[A-Za-z0-9_\-]+\/file$/,
    ]
    async function pipeRequestBinary(pipePath, method, bodyStr) {
      const token = await readPipeToken()
      if (token === null) return { status: 503, headers: {}, raw: Buffer.from(JSON.stringify({ error: '管线服务未启动（.api_token 不存在）' })) }
      const m = method || 'GET'
      const enc = new TextEncoder()
      const len = bodyStr ? enc.encode(bodyStr).length : 0
      return new Promise((resolve) => {
        const r = httpRequest({
          host: PIPE_HOST, port: PIPE_PORT, path: pipePath, method: m,
          headers: {
            Authorization: `Bearer ${token}`,
            ...(bodyStr ? { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': String(len) } : {}),
          },
        }, (res) => {
          const chunks = []
          res.on('data', (c) => chunks.push(c))
          res.on('end', () => {
            const raw = Buffer.concat(chunks)
            resolve({
              status: res.statusCode || 502,
              headers: res.headers || {},
              raw,
            })
          })
        })
        r.on('error', (err) => {
          console.log('[cr-paper] pipe-binary error:', String(err))
          resolve({ status: 503, headers: {}, raw: Buffer.from(JSON.stringify({ error: '管线服务未启动或不可达: ' + String(err) })) })
        })
        if (bodyStr) r.write(bodyStr)
        r.end()
      })
    }
    // GET /cr-paper/docs-file?id=<doc_id> → 管线 /docs/{id}/file（PDF 二进制）
    webServer.register({ kind: 'exact', path: '/cr-paper/docs-file', handler: async (req, res) => {
      try {
        const u = new URL(req.url, 'http://localhost')
        const id = safeId(u.searchParams.get('id'))
        if (!id) return send(res, 400, { error: '非法 doc id' })
        const pipePath = `/docs/${id}/file`
        if (!BINARY_PIPE_WHITELIST.some((re) => re.test(pipePath))) return send(res, 403, { error: '路径不在 PDF 透传白名单: ' + pipePath })
        const r = await pipeRequestBinary(pipePath)
        if (r.status !== 200) {
          const ct = String(r.headers['content-type'] || '').includes('json') ? 'application/json; charset=utf-8' : 'application/json; charset=utf-8'
          res.writeHead(r.status, { 'Content-Type': ct, 'Cache-Control': 'no-store' })
          res.end(r.raw)
          return
        }
        // 透传 PDF：Content-Type + Content-Disposition（RFC 5987 文件名）保持，禁用缓存
        const hdrs = {
          'Content-Type': r.headers['content-type'] || 'application/pdf',
          'Cache-Control': 'no-store',
        }
        if (r.headers['content-disposition']) hdrs['Content-Disposition'] = r.headers['content-disposition']
        res.writeHead(200, hdrs)
        res.end(r.raw)
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    // ── T15 增补：项目输入资料文件读取（二进制透传，任意类型）──
    // 管线 GET /papers/{id}/inputs/{name}（FileResponse 带正确 Content-Type）；client 按扩展名分支渲染
    // 白名单：仅放行 inputs 文件读取确切模式；input_name 中文/空格等由管线 safe_filename 校验，host 仅做长度防护
    const INPUT_FILE_WHITELIST = [
      /^\/papers\/[A-Za-z0-9_\-]+\/inputs\/[^/]+$/,
    ]
    webServer.register({ kind: 'exact', path: '/cr-paper/input-file', handler: async (req, res) => {
      try {
        const u = new URL(req.url, 'http://localhost')
        const id = safeId(u.searchParams.get('id'))
        const name = String(u.searchParams.get('name') || '')
        if (!id || !name || name.length > 200) return send(res, 400, { error: '非法参数' })
        // 文件名含中文/空格：encodeURIComponent 编码后再拼管线路径（避免 ERR_UNESCAPED_CHARACTERS）
        const pipePath = '/papers/' + id + '/inputs/' + encodeURIComponent(name)
        if (req.method !== 'GET' || !INPUT_FILE_WHITELIST.some((re) => re.test(pipePath))) return send(res, 403, { error: '路径不在输入资料白名单: ' + pipePath })
        const r = await pipeRequestBinary(pipePath)
        if (r.status !== 200) {
          res.writeHead(r.status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
          res.end(r.raw)
          return
        }
        const hdrs = {
          'Content-Type': r.headers['content-type'] || 'application/octet-stream',
          'Cache-Control': 'no-store',
        }
        if (r.headers['content-disposition']) hdrs['Content-Disposition'] = r.headers['content-disposition']
        res.writeHead(200, hdrs)
        res.end(r.raw)
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    // ── T13 增补：PDF.js 静态资源透传（同源化：/cr-paper/vendor/pdfjs/* → 管线 /vendor/pdfjs/*）──
    // 管线 webui 已托管 PDF.js（Apache-2.0，免鉴权）；client 经本路由同源加载，规避跨源 script/worker 限制
    const VENDOR_WHITELIST = [
      /^\/pdfjs\/[A-Za-z0-9_\-.]+$/,
    ]
    webServer.register({ kind: 'prefix', path: '/cr-paper/vendor', handler: async (req, res) => {
      try {
        const u = new URL(req.url, 'http://localhost')
        const rel = u.pathname.replace(/^\/cr-paper\/vendor/, '') || '/'
        // 防御：URL 归一化后仍可能残留 .. 段（如 /pdfjs/../../x）；含 .. 一律拒绝
        if (req.method !== 'GET' || rel.indexOf('..') >= 0 || !VENDOR_WHITELIST.some((re) => re.test(rel))) {
          return send(res, 403, { error: '静态资源不在白名单: ' + rel })
        }
        // 管线侧路径 = /vendor + rel（管线静态挂载 /vendor/pdfjs/*）
        const r = await pipeRequestBinary('/vendor' + rel)
        if (r.status !== 200) return send(res, r.status, { error: 'PDF.js 资源获取失败' })
        const hdrs = { 'Content-Type': r.headers['content-type'] || 'application/javascript', 'Cache-Control': 'no-store' }
        res.writeHead(200, hdrs)
        res.end(r.raw)
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    webServer.register({ kind: 'exact', path: '/cr-paper/papers', handler: async (req, res) => {
      try {
        if (req.method === 'POST') {
          // 新建论文项目（显式入口）：读 body title → 调管线 POST /papers 创建（自动带空模板草稿 + 引用清单 + v1）
          const chunks = []
          for await (const c of req) chunks.push(String(c))
          let title = ''
          try { const body = JSON.parse(chunks.join('') || '{}'); title = String(body.title || '').trim() } catch (e) { title = '' }
          if (!title) return send(res, 400, { error: '缺少 title' })
          const r = await pipeRequest('POST', '/papers', JSON.stringify({ title }))
          res.writeHead(r.status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
          res.end(r.raw)
          return
        }
        const items = await listPapers()
        send(res, 200, { papers: items })
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    // ── 新建论文工作区（B：建 papers/<title> 目录 + 投放空模板映射 + 注册 DSH 工作区）──
    // 链路：管线已建项目(paper_id, 空模板草稿在管线数据根)。此处建【用户入口】论文工作区目录
    //   <工作区根>/papers/<title>/，放一个 .paper-link 指向管线项目，并 workspaceRegistry.create 注册为 DSH 工作区。
    // 之后用户点该工作区 → DSH 原生新会话 → cr-plugin auto-open 判定论文工作区 → 经 .paper-link 找管线项目加载空模板。
    webServer.register({ kind: 'exact', path: '/cr-paper/create-workspace', handler: async (req, res) => {
      try {
        if (req.method !== 'POST') return send(res, 405, { error: '仅支持 POST' })
        const chunks = []
        for await (const c of req) chunks.push(String(c))
        let title = '', paperId = ''
        try { const body = JSON.parse(chunks.join('') || '{}'); title = String(body.title || '').trim(); paperId = String(body.paper_id || '').trim() } catch (e) { }
        if (!title) return send(res, 400, { error: '缺少 title' })
        if (!paperId || !safeId(paperId)) return send(res, 400, { error: '缺少合法 paper_id' })
        // 论文工作区根：优先「当前工作区根」的 papers/ 子目录；避免反复新建嵌套
        // （若当前工作区本身已是论文工作区（含 papers 段），则回退到第一个不含 papers 的工作区根，保证平级不嵌套）。
        let base = ''
        // 候选：sp.workspaceRoot（当前工作区根）> wr.list 里不含 papers 的第一个
        try { if (sp && sp.workspaceRoot) base = String(sp.workspaceRoot || '').replace(/[\\/]+$/, '') } catch (e) { }
        if (base && /(?:\\|\/)papers(?:\\|\/|$)/.test(base)) base = ''  // 当前已是论文工作区，弃
        if (!base && wr) { try { const l = wr.list(); if (l && l.length) { const hit = l.find((w) => w && w.path && !/(?:\\|\/)papers(?:\\|\/|$)/.test(String(w.path))); base = String((hit && hit.path) || l[0].path || '').replace(/[\\/]+$/, '') } } catch (e) { } }
        if (!base) return send(res, 503, { error: '未找到工作区根' })
        const papersRoot = base + '/papers'
        const wsDir = papersRoot + '/' + title
        // 建目录（工作区目录须真实存在才能被 workspaceRegistry.create 接受）
        try { fsMkdirSync(wsDir, { recursive: true }) } catch (e) { return send(res, 500, { error: '建工作区目录失败: ' + String(e) }) }
        // 投放映射标记（.paper-link：管线项目 id）
        try { fsWriteFileSync(wsDir + '/.paper-link', String(paperId)) } catch (e) { }
        // 注册为 DSH 工作区（拿 id；若 create 不直接返回 id，则从 list 反查 path 匹配项）
        let workspaceId = null
        try {
          if (wr && wr.create) {
            const w = wr.create(wsDir, title)
            workspaceId = (w && (w.id || w.workspaceId)) || null
          }
          if (!workspaceId && wr && wr.list) {
            const l = wr.list() || []
            for (const it of l) { if (it && String(it.path || '').replace(/[\\/]+$/, '') === wsDir.replace(/[\\/]+$/, '')) { workspaceId = (it.id || it.workspaceId) || null; break } }
          }
        } catch (e) { return send(res, 500, { error: '注册工作区失败: ' + String(e) }) }
        send(res, 200, { ok: true, paper_id: paperId, workspacePath: wsDir, workspaceId })
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    webServer.register({ kind: 'exact', path: '/cr-paper/paper', handler: async (req, res) => {
      try {
        const u = new URL(req.url, 'http://localhost')
        const id = safeId(u.searchParams.get('id'))
        if (!id) return send(res, 400, { error: '非法 paper id' })
        const hit = await locatePaper(id)
        if (!hit) return send(res, 404, { error: 'paper 不存在: ' + id })
        const r = await readPaperFile(hit.papersRoot, id, 'workbench-state.json')
        const p = Paper.parse(r.text, id)
        send(res, 200, p.toJSON())
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    webServer.register({ kind: 'exact', path: '/cr-paper/bibliography', handler: async (req, res) => {
      try {
        const u = new URL(req.url, 'http://localhost')
        const id = safeId(u.searchParams.get('id'))
        if (!id) return send(res, 400, { error: '非法 paper id' })
        const hit = await locatePaper(id)
        if (!hit) return send(res, 404, { error: 'paper 不存在: ' + id })
        const r = await readPaperFile(hit.papersRoot, id, 'bibliography.csl.json')
        const b = Bibliography.parse(r.ok ? r.text : '', id)
        send(res, 200, b.toJSON())
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    webServer.register({ kind: 'exact', path: '/cr-paper/versions', handler: async (req, res) => {
      try {
        const u = new URL(req.url, 'http://localhost')
        const id = safeId(u.searchParams.get('id'))
        if (!id) return send(res, 400, { error: '非法 paper id' })
        const hit = await locatePaper(id)
        if (!hit) return send(res, 404, { error: 'paper 不存在: ' + id })
        const r = await readPaperFile(hit.papersRoot, id, 'versions.jsonl')
        const v = Versions.parse(r.ok ? r.text : '')
        send(res, 200, { paperId: id, entries: v.entries })
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    // ── 写操作代理（T7）：POST /cr-paper/pipe/<管线路径>，白名单 + token 透传 ──
    // 读取管线 token（<工作区>/user-data/pipeline/.api_token，管线启动时生成）
    async function readPipeToken() {
      for (const pr of papersRoots) {
        try {
          // token 在 <数据根>/user-data/pipeline/.api_token（papersRoot 的上级目录）
          const t = await fs.resolve('../.api_token', { cwd: pr })
          const info = await fs.stat(t)
          if (!info || info.isFile === false) continue
          const text = await fs.readText(t)
          const tok = String(text || '').trim()
          if (tok) return tok
        } catch (e) { /* 该工作区无 token，尝试下一个 */ }
      }
      return null
    }
    // 透传请求到管线：{ status, body(JSON对象或null), raw(文本原样) }
    async function pipeRequest(method, pipePath, bodyStr) {
      const token = await readPipeToken()
      if (token === null) return { status: 503, raw: JSON.stringify({ error: '管线服务未启动（.api_token 不存在）' }) }
      const enc = new TextEncoder()
      const len = bodyStr ? enc.encode(bodyStr).length : 0
      return new Promise((resolve) => {
        const r = httpRequest({
          host: PIPE_HOST, port: PIPE_PORT, path: pipePath, method,
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json; charset=utf-8',
            ...(len > 0 ? { 'Content-Length': String(len) } : {}),
          },
        }, (res) => {
          const chunks = []
          res.on('data', (c) => chunks.push(String(c)))
          res.on('end', () => {
            const raw = chunks.join('')
            let body = null
            try { body = raw ? JSON.parse(raw) : null } catch (e) { body = null }
            resolve({ status: res.statusCode || 502, body, raw })
          })
        })
        r.on('error', (err) => {
          console.log('[cr-paper] pipe error:', String(err))
          resolve({ status: 503, raw: JSON.stringify({ error: '管线服务未启动或不可达: ' + String(err) }) })
        })
        if (len > 0) r.write(bodyStr)
        r.end()
      })
    }
    // ── T9 M2 只读透传（GET）：citesuggestions / doc-meta / draft，白名单固定模式 ──
    // 白名单：仅放行固定模式；路径段拼接前逐段校验（id 安全字符，draft 名 pathsafe 由管线校验）
    // T14 增补：速览卡（/docs/{id}/digest[/progress]）+ 联网状态（/web/search_status）+ 收藏列表（/web/collections*）
    const READ_PIPE_WHITELIST = [
      /^\/papers\/[A-Za-z0-9_\-]+$/,
      /^\/papers\/[A-Za-z0-9_\-]+\/citesuggestions$/,
      /^\/docs$/,
      /^\/docs\/[A-Za-z0-9_\-]+$/,
      /^\/docs\/[A-Za-z0-9_\-]+\/digest$/,
      /^\/docs\/[A-Za-z0-9_\-]+\/digest\/progress$/,
      /^\/papers\/[A-Za-z0-9_\-]+\/drafts\/[^/]+$/,
      /^\/web\/search_status$/,
      /^\/web\/collections$/,
      /^\/web\/collections\/[^/]+$/,
    ]
    // T15/T14：删除透传（回收站语义 _trash，管线 DELETE 端点）
    const DELETE_PIPE_WHITELIST = [
      /^\/papers\/[A-Za-z0-9_\-]+\/inputs\/[^/]+$/,
      /^\/web\/collections\/[^/]+$/,
    ]
    // 统一透传 handler：POST 走写白名单（T7），GET 走只读白名单（T9）
    const pipeHandler = async (req, res) => {
      const u = new URL(req.url, 'http://localhost')
      const pipePath = u.pathname.replace(/^\/cr-paper\/pipe/, '') || '/'
      if (req.method === 'POST') {
        if (!PIPE_WHITELIST.some((re) => re.test(pipePath))) return send(res, 403, { error: '路径不在写代理白名单: ' + pipePath })
        console.log('[cr-paper] pipe POST recv:', pipePath)
        const chunks = []
        for await (const c of req) chunks.push(String(c))
        console.log('[cr-paper] pipe POST body len:', chunks.join('').length, 'chunks:', chunks.length)
        const r = await pipeRequest('POST', pipePath, chunks.join(''))
        console.log('[cr-paper] pipe POST resp:', r.status)
        res.writeHead(r.status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
        res.end(r.raw)
        return
      }
      if (req.method === 'GET') {
        if (!READ_PIPE_WHITELIST.some((re) => re.test(pipePath))) return send(res, 403, { error: '路径不在只读透传白名单: ' + pipePath })
        const fullPath = u.search ? `${pipePath}${u.search}` : pipePath
        const r = await pipeRequest('GET', fullPath, null)
        res.writeHead(r.status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
        res.end(r.raw)
        return
      }
      if (req.method === 'DELETE') {
        if (!DELETE_PIPE_WHITELIST.some((re) => re.test(pipePath))) return send(res, 403, { error: '路径不在删除白名单: ' + pipePath })
        // T14：DELETE 端点均返回 JSON（inputs 删除 / collections 删除），统一走 JSON 透传
        const r = await pipeRequest('DELETE', pipePath, null)
        res.writeHead(r.status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
        res.end(r.raw)
        return
      }
      send(res, 405, { error: '仅支持 GET/POST/DELETE' })
    }
    webServer.register({ kind: 'prefix', path: '/cr-paper/pipe', handler: pipeHandler })

    // ── T14 增补：收藏清单 CSV 导出文件只读透传（/cr-paper/export-file?name=xxx.csv → 管线 exports/）──
    // 白名单：仅放行 exports/ 下的 .csv（清单名可含中文/下划线/连字符）；fs.resolve + contains 防穿越
    webServer.register({ kind: 'exact', path: '/cr-paper/export-file', handler: async (req, res) => {
      try {
        const u = new URL(req.url, 'http://localhost')
        const name = String(u.searchParams.get('name') || '').trim()
        if (req.method !== 'GET' || !/^[A-Za-z0-9_\-\u4e00-\u9fff]+\.csv$/.test(name) || name.indexOf('..') >= 0) {
          return send(res, 403, { error: '非法导出文件名: ' + name })
        }
        // exports 目录 = 各 papers 数据根上级的 exports/（对齐管线 root/exports）
        for (const pr of papersRoots) {
          try {
            const rel = '../exports/' + name
            const target = await fs.resolve(rel, { cwd: pr })
            const base = await fs.resolve('../exports', { cwd: pr })
            if (!fs.contains(base, target)) continue
            const info = await fs.stat(target)
            if (!info || info.isFile === false) continue
            if (info.size > 2 * 1024 * 1024) continue
            const text = await fs.readText(target)
            res.writeHead(200, { 'Content-Type': 'text/csv; charset=utf-8', 'Cache-Control': 'no-store' })
            res.end(text)
            return
          } catch (e) { /* 该工作区无此导出，尝试下一个 */ }
        }
        send(res, 404, { error: '导出文件不存在: ' + name })
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    // ── 批量文献入库透传（/cr-paper/upload → 管线 POST /upload multipart；浏览器 FormData 多文件）──
    // 管线 /upload：files: list[UploadFile] + translate → 逐个 validate + ingest 入 00_待处理（M1-M4 后台入库）
    // host 不做解析：Content-Type multipart 原样透传（含 boundary），body 二进制流式转发
    async function pipeRequestRaw(method, pipePath, buf, contentType) {
      const token = await readPipeToken()
      if (token === null) return { status: 503, raw: Buffer.from(JSON.stringify({ error: '管线服务未启动（.api_token 不存在）' })) }
      return new Promise((resolve) => {
        const r = httpRequest({
          host: PIPE_HOST, port: PIPE_PORT, path: pipePath, method,
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': contentType,
            'Content-Length': String(buf ? buf.length : 0),
          },
        }, (res) => {
          const chunks = []
          res.on('data', (c) => chunks.push(c))
          res.on('end', () => resolve({ status: res.statusCode || 502, raw: Buffer.concat(chunks) }))
        })
        r.on('error', (err) => {
          console.log('[cr-paper] pipe-raw error:', String(err))
          resolve({ status: 503, raw: Buffer.from(JSON.stringify({ error: '管线服务未启动或不可达: ' + String(err) })) })
        })
        if (buf && buf.length) r.write(buf)
        r.end()
      })
    }
    webServer.register({ kind: 'exact', path: '/cr-paper/upload', handler: async (req, res) => {
      try {
        const ct = String(req.headers['content-type'] || '')
        if (req.method !== 'POST' || ct.indexOf('multipart/form-data') !== 0) {
          return send(res, 400, { error: '仅支持 multipart/form-data POST' })
        }
        const chunks = []
        for await (const c of req) chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c))
        const body = Buffer.concat(chunks)
        if (body.length > 512 * 1024 * 1024) return send(res, 413, { error: '上传体积超限（512MB）' })
        console.log('[cr-paper] upload recv bytes:', body.length)
        const r = await pipeRequestRaw('POST', '/upload', body, ct)
        res.writeHead(r.status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
        res.end(r.raw)
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    // ── 草稿上传通道（/cr-paper/draft-upload → 管线 import_docx，替换空模板）──
    // 浏览器只能拿 File 字节（无磁盘路径）→ host 用 Node fs 把**纯文件字节**落盘到管线数据根临时目录 →
    // 拿路径调管线 /papers/{id}/drafts/import_docx（draft_name 替换指定草稿，单草稿/引用链随它走）。
    // 请求：POST body = 文件字节（Content-Type octet-stream）；query：id(paper_id) + draft(目标草稿) + fname(文件名)。
    // 注：client 直接发文件字节（非 multipart），避免 host 误写整段 boundary。
    webServer.register({ kind: 'exact', path: '/cr-paper/draft-upload', handler: async (req, res) => {
      try {
        const u = new URL(req.url, 'http://localhost')
        const pid = safeId(u.searchParams.get('id'))
        const draftName = String(u.searchParams.get('draft') || '草稿.md').trim()
        const fname = String(u.searchParams.get('fname') || 'draft.docx').trim()
        if (!pid) return send(res, 400, { error: '缺少 paper id' })
        if (req.method !== 'POST') return send(res, 405, { error: '仅支持 POST' })
        const chunks = []
        for await (const c of req) chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c))
        const body = Buffer.concat(chunks)
        if (body.length > 50 * 1024 * 1024) return send(res, 413, { error: '草稿文件超限（50MB）' })
        const ext = (fname.match(/\.(docx|md)$/i) || [])[0] || '.docx'
        // 定位管线数据根（某 papersRoot 的上级，含 .api_token）
        let root = null
        for (const pr of papersRoots) {
          const up = pr.replace(/\/papers$/, '')
          if (fsExistsSync(up + '/.api_token')) { root = up; break }
        }
        if (!root) return send(res, 503, { error: '未找到管线数据根（.api_token）' })
        const tmpDir = root + '/.draft_upload'
        fsMkdirSync(tmpDir, { recursive: true })
        const tmpFile = tmpDir + '/upload_' + Date.now() + ext
        fsWriteFileSync(tmpFile, body)
        // 调管线 import_docx（替换到 draftName）
        const r = await pipeRequest('POST', `/papers/${pid}/drafts/import_docx`, JSON.stringify({ src_path: tmpFile, draft_name: draftName }))
        // 清理临时文件
        try { fsUnlinkSync(tmpFile) } catch (e) { }
        res.writeHead(r.status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
        res.end(r.raw)
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    // ── T9 client-log-monitor 三件套：host 回收路由（环形缓冲 50 条，只读/上报）──
    const STATE_BUFFER = []
    webServer.register({ kind: 'exact', path: '/cr-paper/client-state', handler: async (req, res) => {
      try {
        const u = new URL(req.url, 'http://localhost')
        const report = u.searchParams.get('report')
        if (report) {
          let obj = null
          try { obj = JSON.parse(report) } catch (e) { obj = null }
          if (obj && typeof obj === 'object') {
            obj.t = Date.now()
            STATE_BUFFER.push(obj)
            if (STATE_BUFFER.length > 50) STATE_BUFFER.splice(0, STATE_BUFFER.length - 50)
          }
          return send(res, 200, { ok: true, buffered: STATE_BUFFER.length })
        }
        send(res, 200, { records: STATE_BUFFER.slice() })
      } catch (err) { send(res, 500, { error: String(err) }) }
    } })

    console.log('[cr-paper] services ready: health + papers + paper + bibliography + versions + pipe proxy(GET/POST/DELETE) + client-state + docs-file + vendor + input-file + T14 digest/search/web/collections')
  },
}
