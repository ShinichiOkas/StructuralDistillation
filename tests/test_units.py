"""単位化（実装設計 §4.U）。"""
from __future__ import annotations

import pytest

from structural_distillation.units import render, rule_version, segment


def test_ja_sentence_splits_after_terminators_and_numbers_units():
    u = segment("雨が降った。  傘は無い！どうする？\n帰った")
    assert [(x.id, x.text) for x in u] == [("s1", "雨が降った。"), ("s2", "傘は無い！"), ("s3", "どうする？"), ("s4", "帰った")]


def test_ja_sentence_drops_empty_pieces():
    assert [x.text for x in segment("。。 甲。")] == ["。", "。", "甲。"]
    assert segment("") == [] and segment("  \n ") == []


def test_paragraph_and_line_rules():
    text = "一段目の一行目\n一段目の二行目\n\n二段目\n \n三段目"
    assert [x.text for x in segment(text, "paragraph")] == ["一段目の一行目\n一段目の二行目", "二段目", "三段目"]
    assert [x.text for x in segment(text, "line")] == ["一段目の一行目", "一段目の二行目", "二段目", "三段目"]
    assert [x.id for x in segment(text, "line")] == ["s1", "s2", "s3", "s4"]


def test_render_is_the_measured_shape():
    assert render(segment("甲。乙。")) == "[s1] 甲。\n[s2] 乙。"


def test_unknown_rule_and_version():
    with pytest.raises(ValueError):
        segment("甲。", "word")
    assert rule_version("ja-sentence") == "ja-sentence/v1"


def test_measured_material_segments_like_the_probe(base21_m01):
    """測定の題材（m01）の単位数が記録と同じ。"""
    assert len(segment(base21_m01["material"]["text"])) == base21_m01["result"]["n_units"]
