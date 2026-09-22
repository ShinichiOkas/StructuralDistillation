"""道具（tools/）の小さな部品と、問いの保存庫を見る CLI。LLM は呼ばない。"""
from __future__ import annotations

import asyncio
import sys

import pytest

from structural_distillation import judge
from structural_distillation.contracts import Budget, Probability

import _cli
import questions_cli
from test_judge import PROP, TEXT, planner, reader


def test_read_text_drops_the_bom_and_stops_on_missing_file(tmp_path):
    """受入 m8: BOM 付きで保存した本文は 1 文目が変わり、同じ本文なのに問いが再利用されなかった。"""
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text(TEXT, encoding="utf-8")
    b.write_text(TEXT, encoding="utf-8-sig")
    assert _cli.read_text(str(a)) == _cli.read_text(str(b)) == TEXT
    with pytest.raises(SystemExit):
        _cli.read_text(str(tmp_path / "none.txt"))


def cli(monkeypatch, capsys, *argv) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["questions_cli.py", *argv])
    rc = questions_cli.main()
    return rc, capsys.readouterr().out


def test_questions_cli_list_and_show(tmp_path, monkeypatch, capsys):
    rc, out = cli(monkeypatch, capsys, str(tmp_path / "missing"), "list")
    assert rc == 0 and "無い" in out
    j = asyncio.run(judge(TEXT, PROP, Probability(), readers=[reader("r1", 4)], planner=planner(),
                          question_store=tmp_path, budget=Budget(crosscheck=False)))
    key = j.question_set.store_key
    rc, out = cli(monkeypatch, capsys, str(tmp_path), "list")
    assert rc == 0 and key[:12] in out and PROP in out
    rc, out = cli(monkeypatch, capsys, str(tmp_path), "show", key[:6])
    assert rc == 0 and "甲は0番目の悪事をした" in out and "交差検証なし" in out
    rc, out = cli(monkeypatch, capsys, str(tmp_path), "show", "zz")
    assert rc == 1 and "読めない" in out
