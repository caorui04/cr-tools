# cr-tools 手动下载安装清单（INSTALL.md）

> 适用：① lite 便携包（无模型版）；② 从源码构建。**推荐先运行 `scripts\download-models.ps1` 自动下载（国内镜像优先）**；本清单用于自动下载失败/无网络脚本时的手动路径。
> 原则：所有文件放到便携包或构建目录的 `tools\` 下（目录不存在则创建）。

## 0. 前置环境（仅源码构建需要；便携包已含全部依赖）

| 项 | 要求 | 检查 |
|---|---|---|
| Node.js | ≥18 | `node -v` |
| Python | 3.11（建议） | `python --version` |
| git（可选） | — | `git --version` |

便携包（lite/full）**不需要**以上——依赖已打包。

## 1. Python 依赖（源码构建；便携包已含）

```powershell
cd pipeline
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```
（87 包，见 `pipeline/requirements.txt`；PaddleOCR 模型在首次 OCR 时自动下载到用户缓存，也可预先设置 `PADDLE_PDX_CACHE_HOME`。）

## 2. 模型（GGUF，放入 `tools\models\`）

| # | 文件 | 大小 | 镜像 URL（hf-mirror.com，国内快） | 源站 URL（海外） | 验证 |
|---|---|---|---|---|---|
| 1 | `bge-m3-Q8_0.gguf` | 0.6GB | `https://hf-mirror.com/{BGE-M3-GGUF仓库}/resolve/main/bge-m3-Q8_0.gguf` | `https://huggingface.co/{BGE-M3-GGUF仓库}/resolve/main/bge-m3-Q8_0.gguf` | 启动后 `http://127.0.0.1:8083` 在线 |
| 2 | `Qwen2.5-3B-Instruct-Q4_K_M.gguf` | 1.8GB | `https://hf-mirror.com/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf` | 同源站 `https://huggingface.co/Qwen/...` | 文件约 1.87GB |
| 3 | `Hy-MT2-1.8B-Q8_0.gguf` | 1.8GB | 见下方说明 | 见下方说明 | 文件约 1.78GB |

> **关于 BGE-M3 与 Hy-MT2 的 GGUF 文件**：这两个 GGUF 为社区量化版本，HF 上存在多个转换仓库，文件名/仓库可能变动。**自动下载脚本会先尝试常见候选 URL**；若 404，请在 HuggingFace 搜索 `bge-m3 gguf` / `Hy-MT2 gguf` 找到当前可用的仓库，下载后改名为上表文件名放入 `tools\models\`（脚本与配置按文件名加载，不改名也可用——需同步修改 `config.yaml` 模型路径）。

**下载命令示例（PowerShell）**：
```powershell
curl.exe -L -o tools\models\Qwen2.5-3B-Instruct-Q4_K_M.gguf "https://hf-mirror.com/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf"
```
> 若 URL 含大写文件名但仓库为小写命名，请先访问仓库页确认实际文件名。

## 3. llama.cpp（`tools\llama\llama-server.exe`，嵌入服务运行）

- 源：https://github.com/ggml-org/llama.cpp/releases（找最新 `llama-*-bin-win-cpu-x64.zip`）
- 解压后 `llama-server.exe`（或 `llama-cli` 同级）放入 `tools\llama\`
- 国内镜像：https://github.com/ggml-org/llama.cpp/releases 可经镜像代理（如 ghproxy）加速：
  `https://mirror.ghproxy.com/https://github.com/ggml-org/llama.cpp/releases/download/{tag}/llama-{tag}-bin-win-cpu-x64.zip`
- 验证：`tools\llama\llama-server.exe --version` 有输出

## 4. pandoc（`tools\pandoc\pandoc.exe`，DOCX 生成）

- 源：https://pandoc.org/installing.html → Windows x86_64 zip
- 解压后把 `pandoc.exe` 放到 `tools\pandoc\`（或 `tools\pandoc\pandoc-3.10.1\pandoc.exe`，与 `config.yaml.example` 默认路径一致）
- 缺失影响：仅草稿保存时"生成 DOCX"降级跳过，其余功能正常

## 5. 配置（Key，可选但推荐）

复制 `pipeline\config.yaml.example` 为 `pipeline\config.yaml`，填入（各 Key 申请地址）：

| Key | 用途 | 申请地址 |
|---|---|---|
| `models.llm.cloud.api_key` | 云端分析后端（速览卡/引用核验/翻译） | https://platform.deepseek.com |
| `web_search.qianfan.api_key` | 中文联网检索（百度学术 site 定向，免费 100 次/日） | https://cloud.baidu.com/product/AppBuilder（控制台 https://console.bce.baidu.com） |
| `web_search.openalex.api_key` | 国际联网检索（10000 credits/日） | https://developers.openalex.org（注册 email 获取） |

> DOAJ / arXiv 为开放 API，无需 Key。

## 6. 启动验证

```bat
start.bat
```
浏览器开 `http://127.0.0.1:3180`（DSH UI）→ `http://127.0.0.1:8737/status`（管线）应 200；全量包 `http://127.0.0.1:8083`（嵌入）在线。

## 常见问题

- **OCR 报"Paddle 无法打开模型"**：`PADDLE_PDX_CACHE_HOME` 指向含中文的路径时按 8.3 短路径自动转换；仍失败请设置该变量到纯 ASCII 目录。
- **启动后检索结果 score 恒 0**：向量库未建——提交文档入库后 M4 自动索引（BGE-M3 嵌入需 8083 在线）。
- **lite 包未下模型就启动**：管线可用（文本型入库/检索降级），OCR/嵌入相关功能会报错——补下模型后重启即可。
