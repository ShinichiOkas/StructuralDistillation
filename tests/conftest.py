"""テストの共有部品。

- ScriptedReader: 本物の Reader と同じ口（name・calibration・complete）を持つ台本の読み手
- fixture: out4 から抜いた実応答（tests/fixtures/）と、記録ディレクトリ（読むだけ）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

import pytest

from structural_distillation.l0 import RawReply

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
OUT4 = ROOT / ".pair-agent" / "probes" / "upstream-probe-1" / "out4"
PROBE_DIR = ROOT / ".pair-agent" / "probes" / "upstream-probe-1"
sys.path.insert(0, str(ROOT / "tools"))


class ScriptedReader:
    """呼び出しごとに台本の応答を返す。台本は content の列か、(messages, schema, sample, version) → content の関数。

    content が None なら ok=False（L0 の失敗）。呼ばれた記録は `calls` に残る。
    """

    def __init__(self, name: str, script: list | Callable, *, calibration: bool = False):
        self.name = name
        self.calibration = calibration
        self._script = script
        self.calls: list[dict] = []

    async def complete(self, messages, schema, *, sample, version):
        self.calls.append({"messages": messages, "schema": schema, "sample": sample, "version": version})
        if callable(self._script):
            content = self._script(messages, schema, sample, version)
        else:
            i = len(self.calls) - 1
            content = self._script[i] if i < len(self._script) else self._script[-1]
        if isinstance(content, RawReply):
            return content
        if content is None:
            return RawReply(False, None, "scripted failure", {"cached": False})
        return RawReply(True, content, None, {"cached": False})


@pytest.fixture
def cache_cases() -> list[dict]:
    return json.loads((FIXTURES / "cache_keys.json").read_text(encoding="utf-8"))["cases"]


@pytest.fixture
def base21_m01() -> dict:
    return json.loads((FIXTURES / "base21_m01.json").read_text(encoding="utf-8"))


def need_out4():
    if not OUT4.exists():
        pytest.skip("測定 2 周目の記録（.pair-agent/probes/upstream-probe-1/out4）が無い")
