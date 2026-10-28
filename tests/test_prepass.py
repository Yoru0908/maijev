"""Pure-function tests for prepass glossary rendering — no LLM."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows.maijev.jev import ContextItem, select_anchors  # noqa: E402
from flows.maijev.prepass import build_anchors, render_glossary  # noqa: E402
from flows.maijev.translate_llm import _clean_zh  # noqa: E402


def _item(item_id, text, start=None, end=None):
    return ContextItem(item_id=item_id, text=text, start=start, end=end)


def _result(item_id, text, choice, confidence):
    return {
        "item_id": item_id,
        "text": text,
        "choice": choice,
        "confidence": confidence,
    }


def test_select_anchors_keeps_entities_and_reviews():
    items = [
        _item("a", "中川智尋"),
        _item("b", "番組タイトル"),
        _item("c", "がはは"),
        _item("d", "謎の文字"),
    ]
    results = [
        _result("a", "中川智尋", "person_name", 0.95),
        _result("b", "番組タイトル", "program_title", 0.88),
        _result("c", "がはは", "effect_text", 0.9),   # confident non-entity: drop
        _result("d", "謎の文字", "noise", 0.3),        # low confidence: review
    ]
    selected = select_anchors(items, results)
    labels = {item.item_id: label for item, label, _ in selected}
    assert labels == {"a": "person_name", "b": "program_title", "d": "review"}


def test_build_anchors_serializes_timing():
    items = [_item("a", "中川智尋", start=12.3, end=14.8)]
    results = [_result("a", "中川智尋", "person_name", 0.95)]
    anchors = build_anchors(items, results)
    assert anchors == [{
        "text": "中川智尋",
        "kind": "person_name",
        "confidence": 0.95,
        "start": 12.3,
        "end": 14.8,
    }]


def test_render_glossary_writes_one_row_per_entity():
    result = {
        "theme": "测试  节目 -> 主题",
        "entities": [
            {
                "kind": "person",
                "ja": "野比のび太",
                "variants": ["のび太", "ノビ君", "のび太"],
                "tgt": "大雄",
                "note": "主角",
            },
            # kept as written: still a decision the translator needs
            {"kind": "program", "ja": "スペースウォーズ", "tgt": "スペースウォーズ"},
            # the older src/canonical shape still renders
            {
                "src": ["中川智尋", "中川智导", "中 智"],
                "canonical": "中川智尋",
                "kind": "person_name",
            },
            {"ja": "", "tgt": ""},
            "not-a-dict",
        ],
        "review": [{"src": "謎", "reason": "blur"}],
    }
    assert render_glossary(result).splitlines() == [
        "【本片主题】测试 节目 → 主题",
        "[人名] 野比のび太 / のび太 / ノビ君 -> 大雄  # 主角",
        "[节目] スペースウォーズ -> スペースウォーズ",
        "[人名] 中川智尋 / 中川智导 / 中 智 -> 中川智尋",
    ]


def test_clean_zh_strips_banned_chars():
    # banned chars are Chinese fillers in the *translation* output
    assert _clean_zh("嗯，分かった") == "分かった"
    assert _clean_zh("是啊 嗯") == "是"  # banned chars stripped anywhere
    assert _clean_zh("質問 -嗯，回答") == "質問 -回答"
    # a " -" part left empty after cleanup is dropped entirely
    assert _clean_zh("嗯 -回答") == "回答"
    # JA lines can never start with " -" — a leading dash is model noise
    assert _clean_zh("-是的") == "是的"
    assert _clean_zh(" -山川宇衣你对家乡也") == "山川宇衣你对家乡也"
    # punctuation rules: mid-sentence comma -> space, quotes -> 「」
    assert _clean_zh("他说，“你好”。") == "他说 「你好」"


def test_prepass_reads_private_prompt_and_human_glossaries(monkeypatch, tmp_path):
    import flows.maijev.prepass as prepass
    from flows.maijev.segment_llm import MergedLine

    prompt, manual = tmp_path / "private.md", tmp_path / "manual.md"
    prompt.write_text("私有预分析 prompt", encoding="utf-8")
    manual.write_text("オールナイトニッポン0 -> ANN0", encoding="utf-8")
    monkeypatch.setenv("PREPASS_PROMPT_PATH", str(prompt))
    monkeypatch.setenv("TRANSLATE_GLOSSARY_PATH", str(manual))
    monkeypatch.delenv("TRANSLATE_BASE_GLOSSARY_PATH", raising=False)
    monkeypatch.setattr(prepass, "resolve_model", lambda _: "m")
    seen = {}

    def fake_generate(body, **kwargs):
        seen.update(body=body, system=kwargs["system"])
        return {"theme": "深夜电台", "entities": [{
            "kind": "program", "ja": "オールナイトニッポン0",
            "variants": ["オールナイトニッポンゼロ"], "tgt": "ANN0"}]}

    monkeypatch.setattr(prepass, "generate_json", fake_generate)
    text = prepass.run_prepass([], None, [MergedLine(0, 1, "こんばんは", [0])])
    assert seen["system"] == "私有预分析 prompt"
    assert seen["body"].startswith("【附录：静态词库】\nオールナイトニッポン0 -> ANN0\n")
    assert text.splitlines() == [
        "【本片主题】深夜电台",
        "[节目] オールナイトニッポン0 / オールナイトニッポンゼロ -> ANN0",
    ]
