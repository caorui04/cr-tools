# cr-tools —— 大学生论文工作台（便携 · 开源）

> 以 DeepSeek Harness（DSH）为基座的论文工作台：**引用链管理 · 知识库语义检索 · AI 对话×资料联动 · 联网检索 · 草稿/时间线/DOCX 交付**。
> 面向人文社科大学生：免安装、随身携带、对话式维护（说需求 → AI 改插件 → 热更新 → 验收）。

## ✨ 核心功能

- **引用链（核心）**：读 PDF → 选中文字 → 复制 → 粘贴即引用（草稿框确认登记 + 会话框来源 chip）→ 自动编号 [N] + 引用清单；重复引用自动复用编号。**设计主线：降低正常引用的操作工作量**。
- **知识库语义检索**：本地向量库（BGE-M3）语义检索 + 联网检索（中文/国际双标签）。
- **速览卡**：文档 AI 摘要（核心要点/章节，页码可跳转）。
- **AI 对话 × 资料联动**：工作台面板 + 对话区同屏；PDF 预览、输入资料、草稿/时间线/DOCX。
- **文献入库**：批量拖放上传（判重自动跳过）+ 入库监控（四阶段进度/队列徽标）。

## 🚀 两种使用方式（任选其一）

### 方式 A：便携包（推荐，免安装）
下载 **portable-lite**（无模型，~1.2GB）或 **portable-full**（含模型，~6GB）压缩包 → 解压到任意位置 → 按包内 `README.txt` 操作：
1. lite 包：先运行 `scripts\download-models.ps1` 下载模型（国内镜像优先），或按 `docs\INSTALL.md` 手动下载放入 `tools\`
2. 双击 `start.bat` → 浏览器打开 `http://127.0.0.1:3180`

### 方式 B：从源码构建（开发者/维护者）
```powershell
# 1) 安装依赖（Node 18+ 必备；Python 3.11 建议）
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# 2) 下载模型（国内镜像优先；或手动按 docs\INSTALL.md）
powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1
# 3) 启动
start.bat
```

## 🛠️ 目录结构

```
release/                     ← 本仓库根
├── cr-plugin/               ← DSH 插件（host 服务 + client 工作台 UI）
├── pipeline/                ← M1-M7 知识库管线（Python；依赖见 pipeline/requirements.txt）
│   └── config.yaml.example  ← 配置模板（复制为 config.yaml 并填 Key；相对路径）
├── scripts/
│   ├── setup.ps1            ← 一键装依赖（Node 检测 + npx 装 DSH + venv + pip install）
│   └── download-models.ps1  ← 模型下载引导（hf-mirror 国内镜像优先 + 手动降级）
├── docs/INSTALL.md          ← 手动下载安装清单（逐项：URL/放置路径/验证）
├── cordis.yml               ← 插件组合蓝图
└── start.bat                ← 一键启动（管线 8737 + 嵌入 8083 + DSH 3180）
```

## 📦 模型与依赖引导下载（lite 包 / 源码构建）

**先运行 `scripts\download-models.ps1` 自动下载（国内镜像优先）；失败或想手动，按下表**：

| 组件 | 来源 | License | 放置路径 |
|---|---|---|---|
| llama.cpp（llama-server.exe） | https://github.com/ggml-org/llama.cpp/releases | MIT | `tools/llama/` |
| BGE-M3 嵌入（bge-m3-Q8_0.gguf，0.6GB） | HF 镜像 https://hf-mirror.com/BAAI/bge-m3（或手动清单） | Apache-2.0 | `tools/models/` |
| Qwen2.5-3B 本地对话（Qwen2.5-3B-Instruct-Q4_K_M.gguf，1.8GB） | 镜像 https://hf-mirror.com/Qwen/Qwen2.5-3B-Instruct-GGUF | Apache-2.0 | `tools/models/` |
| Hy-MT2 翻译（Hy-MT2-1.8B-Q8_0.gguf，1.8GB） | 手动清单见 INSTALL.md（License 以来源页为准） | 见来源页 | `tools/models/` |
| PaddleOCR（OCR） | **随 pip 包自带**（pip install 时下载官方模型到缓存） | Apache-2.0 | 自动 |
| pandoc（DOCX 转换） | https://pandoc.org/installing.html | GPL-2.0 | `tools/pandoc/` |

**完整手动清单（含每个文件的精确 URL 与验证命令）见 [`docs/INSTALL.md`](docs/INSTALL.md)**。

## 🔑 API Key 申请（可选但推荐）

复制 `pipeline\config.yaml.example` 为 `pipeline\config.yaml` 后填入。**不填 Key 也可运行**（本地检索/OCR 等核心功能不受影响；对应联网/云端功能降级）。

| Key | 用途 | 申请地址 | 备注 |
|---|---|---|---|
| **DeepSeek API Key** | 云端分析后端（速览卡摘要/引用核验/翻译） | https://platform.deepseek.com | 注册即用，按量计费 |
| **百度千帆 AppBuilder Key** | 中文联网检索（site 定向百度学术） | https://cloud.baidu.com/product/AppBuilder（控制台 https://console.bce.baidu.com） | 免费 100 次/日 |
| **OpenAlex Key** | 国际联网检索 | https://developers.openalex.org（注册 email 获取） | 10000 credits/日 |
| DOAJ / arXiv | 国际联网检索（开放源） | **无需 Key** | — |

> 完整配置说明见 `docs/INSTALL.md` §5；开发配置模板见 `pipeline/config.yaml.example`（各 Key 有行内注释）。

## ⚖️ 合规声明

- **基座**：构建于 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（MIT，v0.1.1-rc.2）。
- **代码**：本仓库（除标注第三方外）MIT License（见 `LICENSE`）。
- **模型/工具**：见上表 License；分发/商用前核对各来源页条款。
- **API Key**：本仓库不含任何真实密钥；`pipeline/config.yaml` 不入库，模板见 `pipeline/config.yaml.example`。
- **数据**：运行时数据不入库；问卷等原始数据发布前须去标识化。

## 📖 文档

| 文件 | 说明 |
|---|---|
| `docs/onboarding/使用手册.md` | 使用手册（**Markdown 权威版**，内容更新以此为准） |
| `docs/onboarding/使用手册.html` | 使用手册 PPT 演示版（由 Markdown 版派生，浏览器直接打开；**允许滞后于 md**，仅用于演示/分享） |
| `docs/onboarding/安装指导手册.md` | 安装指导（**Markdown 权威版**） |
| `docs/onboarding/安装指导手册.html` | 安装指导 PPT 演示版（同上，派生自 md） |
| `docs/INSTALL.md` | 手动下载安装完整清单（模型精确 URL + 验证命令） |
| `docs/adr/INDEX.md` | 架构决策记录（ADR-001~004） |

> 文档关系：**Markdown 版为单一事实来源**；HTML PPT 版为其派生演示形态（纯 CSS 自包含、无外部依赖），更新手册时以 md 为准，PPT 低频同步。
