"""L0 の実接続（実装設計 §9）。既定の pytest では走らない。

    SD_LIVE=1 SD_LIVE_MODEL=gemma4:31b-cloud pytest -m integration tests/test_l0_live.py

⚠ GPU を使うローカルモデルを指定しないこと（師匠と共有）。クラウドモデル（-cloud）を使う。
"""
from __future__ import annotations

import asyncio
import os

import pytest

from structural_distillation import prompts
from structural_distillation.l0 import OllamaReader, structured
from structural_distillation.prompts import PromptSet
from structural_distillation.units import render, segment

pytestmark = pytest.mark.integration


def _model() -> str:
    if os.environ.get("SD_LIVE") != "1" or not os.environ.get("SD_LIVE_MODEL"):
        pytest.skip("実接続は SD_LIVE=1 と SD_LIVE_MODEL（クラウドモデル）を与えたときだけ走る")
    return os.environ["SD_LIVE_MODEL"]


def test_ollama_returns_schema_conformant_answer():
    model = _model()
    p = PromptSet.builtin("ja")
    units = segment("太郎は駅で花子に手紙を渡した。花子はそれを読まずに鞄にしまった。")
    msgs = p.answer_messages(render(units), "太郎は花子に手紙を渡した")
    s = asyncio.run(structured(OllamaReader(model), msgs, prompts.answer_schema(p), version="p2", sample=0,
                               notice=p.schema_notice, retry=True))
    assert s.ok, s.error
    assert s.obj["verdict"] in p.verdict_words()
    assert s.live >= 1
