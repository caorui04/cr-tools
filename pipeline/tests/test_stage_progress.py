"""D6 断点续传通用化单测：stage 命名空间隔离 + 读写清除语义。"""

from __future__ import annotations

from pipeline_core.stage_progress import (
    STAGE_OCR,
    STAGE_TRANSLATE,
    clear_progress,
    load_progress,
    save_progress,
)


def test_save_and_load_roundtrip(tmp_path):
    save_progress(tmp_path, "abc123def456", STAGE_OCR, 1)
    save_progress(tmp_path, "abc123def456", STAGE_OCR, 3)
    assert load_progress(tmp_path, "abc123def456", STAGE_OCR) == {1, 3}
    # 文件名带 stage 命名空间
    assert (tmp_path / "progress" / "abc123def456.ocr.progress").exists()


def test_stages_same_doc_id_isolated(tmp_path):
    """两 stage 同名 doc_id 互不干扰（C4 回归）。"""
    doc = "a3f8c2d4e5f6"
    for page in (1, 2, 3):
        save_progress(tmp_path, doc, STAGE_OCR, page)
    save_progress(tmp_path, doc, STAGE_TRANSLATE, 10)

    assert load_progress(tmp_path, doc, STAGE_OCR) == {1, 2, 3}
    assert load_progress(tmp_path, doc, STAGE_TRANSLATE) == {10}

    # 清除一个 stage 不影响另一个（原「M2 完成时清除」时序耦合已消解）
    clear_progress(tmp_path, doc, STAGE_OCR)
    assert load_progress(tmp_path, doc, STAGE_OCR) == set()
    assert load_progress(tmp_path, doc, STAGE_TRANSLATE) == {10}


def test_load_missing_returns_empty(tmp_path):
    assert load_progress(tmp_path, "abc123def456", STAGE_OCR) == set()


def test_load_corrupt_returns_empty(tmp_path):
    progress_dir = tmp_path / "progress"
    progress_dir.mkdir()
    (progress_dir / "abc123def456.translate.progress").write_text(
        "7\n不是数字\n9\n", encoding="utf-8"
    )
    assert load_progress(tmp_path, "abc123def456", STAGE_TRANSLATE) == set()


def test_clear_missing_is_noop(tmp_path):
    clear_progress(tmp_path, "abc123def456", STAGE_OCR)  # 不抛错


def test_invalid_stage_rejected(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        load_progress(tmp_path, "abc123def456", "../escape")
