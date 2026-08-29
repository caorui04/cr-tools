# download-models.ps1 - cr-tools model download guide (hf-mirror CN-first, manual fallback)
# Usage: powershell -ExecutionPolicy Bypass -File scripts/download-models.ps1
# Downloads GGUF models into tools/models/ (or -Out). On 404/failure prints manual steps.
# NOTE: ASCII-only file. Keep English.

param([string]$Out = '')

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Out) { $Out = Join-Path $Root 'tools\models' }
New-Item -ItemType Directory -Path $Out -Force | Out-Null

function Say([string]$m) { Write-Host "[download] $m" -ForegroundColor Cyan }

# model list: File | candidate URLs (mirror first) | min bytes
$models = @(
  @{ File = 'bge-m3-Q8_0.gguf'; Urls = @(
      'https://hf-mirror.com/BAAI/bge-m3/resolve/main/bge-m3-Q8_0.gguf',
      'https://huggingface.co/BAAI/bge-m3/resolve/main/bge-m3-Q8_0.gguf'
    ); Min = 500MB },
  @{ File = 'Qwen2.5-3B-Instruct-Q4_K_M.gguf'; Urls = @(
      'https://hf-mirror.com/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf',
      'https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf'
    ); Min = 1500MB },
  @{ File = 'Hy-MT2-1.8B-Q8_0.gguf'; Urls = @(); Min = 1500MB }
)

$anyMissing = $false
foreach ($m in $models) {
  $target = Join-Path $Out $m.File
  if (Test-Path $target) {
    $sz = (Get-Item $target).Length
    if ($sz -gt $m.Min) { Say "[skip] $($m.File) exists ($([Math]::Round($sz/1MB)) MB)"; continue }
    Say "[warn] $($m.File) too small ($([Math]::Round($sz/1MB)) MB), re-downloading"
    Remove-Item $target -Force
  }
  $ok = $false
  foreach ($u in $m.Urls) {
    if (-not $u) { continue }
    try {
      Say "downloading $($m.File) <- $u"
      & curl.exe -L --fail --retry 2 -o $target $u
      if ((Get-Item $target -ErrorAction SilentlyContinue).Length -gt $m.Min) { $ok = $true; break }
      Say "[warn] file too small / failed, trying next URL"
      Remove-Item $target -Force -ErrorAction SilentlyContinue
    } catch { Say "[warn] failed: $u" }
  }
  if (-not $ok) {
    $anyMissing = $true
    Say "[manual] $($m.File) auto-download failed."
    Say "         Manual steps (see docs\INSTALL.md):"
    Say "         1) search HuggingFace for '<name> gguf' (e.g. bge-m3 gguf / Hy-MT2 gguf)"
    Say "         2) download the Q8_0 gguf file"
    Say "         3) save it into $Out with name: $($m.File)"
  }
}

# -- llama.cpp + pandoc hints (not auto-downloaded; too build-specific) --
Say "Tools (manual, optional but recommended):"
Say "  llama.cpp : https://github.com/ggml-org/llama.cpp/releases (latest win-cpu zip, extract llama-server.exe -> tools\llama\)"
Say "  pandoc    : https://pandoc.org/installing.html (zip, extract pandoc.exe -> tools\pandoc\pandoc-3.10.1\)"
Say "  PaddleOCR : auto (installed with pip, models cached on first OCR)"

if ($anyMissing) {
  Say "Some models need manual download - see docs\INSTALL.md for full list."
} else {
  Say "All models ready. Run start.bat to launch."
}
