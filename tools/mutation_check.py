"""変異試験: パッケージを一時ディレクトリに写して 1 箇所ずつ壊し、テストが落ちるか（緑が偽物でないか）を見る。

    python tools/mutation_check.py            # 全部
    python tools/mutation_check.py l3 H3      # 名前に含む語で絞る

リポジトリのファイルには触らない（写しを一時ディレクトリに作り、PYTHONPATH で先に読ませる。
cwd を写しの側にするのは、python -m がカレントディレクトリを sys.path の先頭に置くため）。
実装を変えて置換の元の文字列が消えたら、その変異は assert で止まる。そのときは変異の定義を直す。
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MUT = Path(tempfile.mkdtemp(prefix="sd_mut_"))
PY = Path(sys.executable)

MUTANTS = [
    ("l3 無効を先に 0（上流 §5.3 の文の規則）", "l3.py", '    sup, ref = side_evidence(vs, vr)\n    if sup and ref:',
     '    if "INVALID" in (vs, vr):\n        return 0, "invalid"\n    sup, ref = side_evidence(vs, vr)\n    if sup and ref:'),
    ("l3 多数決の同数を 0 にしない", "l3.py", "    if list(c.values()).count(n) > 1:\n        return 0, n / len(vals)", "    if False:\n        return 0, n / len(vals)"),
    ("l3 ω の境界 ≤ → <", "l3.py", "if w <= t.omega and p > 0.5:", "if w < t.omega and p > 0.5:"),
    ("l3 無効率の分母（回答ベース → 軸×標本）", "l3.py", "(2 * samples * n)", "(samples * n)"),
    ("l3 矛盾の標本割合", "l3.py", 'sum(1 for k in kinds if k == "contradiction") / samples', 'sum(1 for k in kinds if k == "contradiction") / (samples + 1)'),
    ("l3 要約が偽読み手を除かない", "l3.py", "if not r.calibration and r.label", "if r.label"),
    ("l3 zero_kind の優先順位", "l3.py", '_ZERO_PRIORITY = ("contradiction", "invalid", "silent")', '_ZERO_PRIORITY = ("silent", "invalid", "contradiction")'),
    ("l1 過半数 > → ≥", "l1.py", "if opposing * 2 > len(verifiers):", "if opposing * 2 >= len(verifiers):"),
    ("l1 H3 の閾値 0.6 → 0.99", "l1.py", "H3_JACCARD = 0.6", "H3_JACCARD = 0.99"),
    ("l1 weak を立てない", "l1.py", 'is_weak |= "NEITHER" in codes', "is_weak |= False"),
    ("l1 版が割れたら同数（TIE）にしない", "l1.py", 'return tops[0] if len(tops) == 1 else "TIE"', "return tops[0]"),
    ("l1 H11 の終端から ? を外す", "l1.py", '_QUESTION_ENDS = ("か", "？", "?")', '_QUESTION_ENDS = ("か", "？")'),
    ("l2 角括弧を剥がさない", "l2.py", r'_ID_CLEAN = re.compile(r"[\[\]\s「」]")', r'_ID_CLEAN = re.compile(r"[\s]")'),
    ("l2 根拠 0 個でも有効", "l2.py", "(not ev or any(e not in unit_ids for e in ev))", "(any(e not in unit_ids for e in ev))"),
    ("l0 スキーマを sort_keys で埋める", "l0.py", "m[\"content\"] + notice + json.dumps(schema, ensure_ascii=False)", "m[\"content\"] + notice + json.dumps(schema, ensure_ascii=False, sort_keys=True)"),
    ("l0 structured がフェンスを剥がさない", "l0.py", 'content = strip_fence(reply.content or "")', 'content = reply.content or ""'),
    ("l0 失敗もキャッシュに書く", "l0.py", "            if reply.ok:\n                payload", "            if True:\n                payload"),
    ("l0 再送しない", "l0.py", 'for v in ([version, f"{version}:retry"] if retry else [version]):', "for v in [version]:"),
    ("l4 K 等分の上端", "l4.py", "level = min(output.k - 1, int(p * output.k)) + 1", "level = min(output.k, int(p * output.k)) + 1"),
    ("judge 外した軸も集約する", "judge.py", "readings[r.name] = _read(m, qs.active_ids, qs.retries, output, thresholds)",
     "readings[r.name] = _read(m, [a.id for a in qs.axes], qs.retries, output, thresholds)"),
    ("judge 生成器の偽読み手を許す", "judge.py", "        if gen.calibration:\n", "        if False:\n"),
    ("units 疑問符で割らない", "units.py", 'r"(?<=[。！？])"', 'r"(?<=[。！])"'),
    ("prompts 回答の指示を 1 文字変える", "prompt_sets/ja/set.json", "判定の仕方", "判定のしかた"),
]


def main() -> int:
    only = sys.argv[1:]
    survived = []
    for name, rel, old, new in MUTANTS:
        if only and not any(o in name for o in only):
            continue
        shutil.rmtree(MUT, ignore_errors=True)
        shutil.copytree(REPO / "structural_distillation", MUT / "structural_distillation",
                        ignore=shutil.ignore_patterns("__pycache__"))
        f = MUT / "structural_distillation" / rel
        src = f.read_text(encoding="utf-8")
        assert src.count(old) == 1, (name, src.count(old))
        f.write_text(src.replace(old, new), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(MUT), "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run([str(PY), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", str(REPO / "tests")],
                           cwd=str(MUT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        # 写しが読まれたことを確かめる（読まれていなければ変異は無効）
        chk = subprocess.run([str(PY), "-c", "import structural_distillation as s; print(s.__file__)"], cwd=str(MUT),
                             env=env, capture_output=True, text=True)
        used_copy = str(MUT) in chk.stdout
        failed = r.returncode != 0
        first = next((ln for ln in r.stdout.splitlines() if ln.startswith("FAILED")), "")
        print(f"{'殺した' if failed else '生き残り'}  {name}  {first[:110]}  {'' if used_copy else '（写しが読まれていない!）'}")
        if not failed:
            survived.append(name)
    shutil.rmtree(MUT, ignore_errors=True)
    print(f"\n生き残り {len(survived)}: {survived}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
