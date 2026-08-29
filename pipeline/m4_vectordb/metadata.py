"""metadata_json 构建。字段见 C4 §3。"""

from __future__ import annotations


def build_metadata(chunk, sidecar: dict, source_file: str) -> dict:
    """构建 C4 §3 metadata_json。"""
    lang_map = {"A": "zh", "B": "en", "C": "mixed"}
    lang = lang_map.get(
        sidecar.get("classification", {}).get("language", "C"), "mixed"
    )

    return {
        "source_file": source_file,
        "title": sidecar.get("title") or None,
        "heading_path": chunk.heading_path or "",
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "language": lang,
        "low_confidence": sidecar.get("low_confidence", False),
    }
