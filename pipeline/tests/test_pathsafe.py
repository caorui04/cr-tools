"""pathsafe.py 单测（U3 文件访问网关契约第 4 节验收用例，批次 F）。

覆盖三入口穿越校验（paper_id / 草稿名 / inputs 文件名）：
- 中文草稿名合法通过；
- `..` 穿越 / 绝对路径 / 多层路径注入 → 三入口各自拒绝；
- 空名、点开头按现状行为如实断言（零行为变化原则）；
- Unicode 等价文件名（NFC/NFD）按现状行为如实断言（详见测试内注释）。
"""

import unicodedata

import pytest

from pipeline_core.pathsafe import (
    DRAFT_NAME_RE,
    PAPER_ID_RE,
    PathUnsafeError,
    safe_draft_name,
    safe_filename,
    safe_paper_id,
)


# ---- paper_id 入口 ----

def test_paper_id_legal():
    """白名单字符（字母数字下划线连字符）原样返回；首尾空白按现状 strip。"""
    assert safe_paper_id("p_1785760990300_0002") == "p_1785760990300_0002"
    assert safe_paper_id("Demo-01") == "Demo-01"
    assert safe_paper_id(" abc ") == "abc"


@pytest.mark.parametrize("bad", [
    "../x",                 # .. 穿越
    "..\\..\\etc",          # Windows 分隔符穿越
    "a/b",                  # 多层路径注入
    "/etc/passwd",          # 绝对路径（POSIX）
    "C:\\Windows",          # 绝对路径（Windows）
    "",                     # 空名：strip 后为空 → 拒绝
    ".abc",                 # 点开头：`.` 不在白名单 → 拒绝
    "中文项目",              # 非白名单字符 → 拒绝
])
def test_paper_id_rejected(bad):
    with pytest.raises(PathUnsafeError):
        safe_paper_id(bad)


def test_paper_id_error_message():
    """拒绝文案与 routes 迁移前完全一致（含原始入参 repr，非 strip 后值）。"""
    with pytest.raises(PathUnsafeError, match=r"^非法的论文项目 ID: '\.\./x'$"):
        safe_paper_id("../x")


# ---- 草稿名入口 ----

def test_draft_name_chinese_legal():
    """中文草稿名（含空格/连字符/点）合法通过，原样返回。"""
    assert safe_draft_name("中文 草稿-v2.md") == "中文 草稿-v2.md"
    assert safe_draft_name("文献综述.md") == "文献综述.md"
    assert safe_draft_name("draft 01.md") == "draft 01.md"


def test_draft_name_dot_prefixed_legal():
    """点开头按现状允许：`.hidden.md` 匹配字符类且有正文 → 通过。"""
    assert safe_draft_name(".hidden.md") == ".hidden.md"


@pytest.mark.parametrize("bad", [
    "../x.md",              # .. 穿越
    "a/../b.md",            # 多层路径 + 穿越
    "a/b.md",               # 多层路径注入（/ 不在字符类）
    "a\\b.md",              # Windows 分隔符注入
    "/abs/path.md",         # 绝对路径
    "x..md",                # 含 .. 即拒（negative lookahead）
    "x.txt",                # 非 .md 后缀
    "",                     # 空名：字符类至少 1 字符 + 需 .md 后缀 → 拒绝
    ".md",                  # 仅后缀无正文 → 拒绝（现状行为，非点开头一刀切）
])
def test_draft_name_rejected(bad):
    with pytest.raises(PathUnsafeError):
        safe_draft_name(bad)


def test_draft_name_error_message():
    with pytest.raises(PathUnsafeError, match=r"（仅 \*\.md）$"):
        safe_draft_name("../x.md")


# ---- inputs 文件名入口 ----

def test_filename_legal():
    """单组件文件名（含中文、点开头、空格）通过，原样返回。"""
    assert safe_filename("笔记 v2.docx") == "笔记 v2.docx"
    assert safe_filename(".hidden") == ".hidden"      # 点开头：Path().name 不变 → 允许
    assert safe_filename("") == ""                    # 空名：Path("").name == "" → 允许（现状）


@pytest.mark.parametrize("bad", [
    "../x",                 # .. 穿越
    "..\\..\\x",            # Windows 分隔符穿越
    "a/b.txt",              # 多层路径注入
    "/abs/x.txt",           # 绝对路径（POSIX）
    "C:\\x.txt",            # 绝对路径（Windows，Path().name 剥前缀 → 不等 → 拒绝）
])
def test_filename_rejected(bad):
    with pytest.raises(PathUnsafeError, match="^非法文件名$"):
        safe_filename(bad)


# ---- Unicode 等价文件名（NFC/NFD）现状行为记录 ----

def test_unicode_equivalence_as_is():
    """NFC/NFD 按现状行为如实断言（Python 侧不做规范化，无 canonicalize）：

    - safe_filename：纯 Path().name 字符串比对，不做 Unicode 规范化
      → NFC 与 NFD 同名文件均通过（是否命中同一文件由文件系统决定）。
    - safe_draft_name：NFC 的 é（U+00E9，isalnum）匹配 \\w 通过；
      NFD 的组合符（U+0301，类别 Mn）不匹配 \\w → 拒绝。
      即现状下 NFD 草稿名会被 4xx 拒绝，属已知行为，本契约不做变更。
    """
    nfc = unicodedata.normalize("NFC", "café 草稿.md")
    nfd = unicodedata.normalize("NFD", "café 草稿.md")
    assert nfc != nfd
    assert safe_filename(nfc) == nfc
    assert safe_filename(nfd) == nfd
    assert safe_draft_name(nfc) == nfc
    with pytest.raises(PathUnsafeError):
        safe_draft_name(nfd)


def test_regex_objects_exported():
    """契约 3.1 要求的具名正则对象可导入且与原 routes 定义一致。"""
    assert PAPER_ID_RE.pattern == r"^[A-Za-z0-9_-]+$"
    assert PAPER_ID_RE.match("ok_1-x")
    assert not PAPER_ID_RE.match("..")
    assert DRAFT_NAME_RE.match("中文.md")
    assert not DRAFT_NAME_RE.match("a..b.md")
