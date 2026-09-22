"""L4 型付け（実装設計 §4.4）。純関数。"""
from __future__ import annotations

import random

import pytest

from structural_distillation import l4
from structural_distillation.contracts import InputError, Label, Ordinal, Probability

OK = Label.SPLIT


def test_probability_is_p_itself():
    assert l4.to_value(0.73, OK, Probability()).p == 0.73
    assert l4.to_value(0.73, OK, Probability()).level is None


@pytest.mark.parametrize("p,level", [(0.0, 1), (0.19, 1), (0.2, 2), (0.5, 3), (0.6, 4), (0.8, 5), (0.999, 5), (1.0, 5)])
def test_ordinal_equal_bins_match_the_measured_formula(p, level):
    assert l4.to_value(p, OK, Ordinal(5)).level == level


def test_ordinal_bounds_and_labels():
    o = Ordinal(3, labels=("低", "中", "高"), bounds=(0.3, 0.7))
    assert [l4.to_value(p, OK, o).level for p in (0.0, 0.29, 0.3, 0.69, 0.7, 1.0)] == [1, 1, 2, 2, 3, 3]
    assert l4.to_value(0.5, OK, o).label == "中"


@pytest.mark.parametrize("lab", [Label.NO_EVIDENCE, Label.INSTRUMENT_FAULT])
def test_no_value_labels(lab):
    assert l4.to_value(0.9, lab, Ordinal(5)) is None


def test_undefined_p_has_no_value():
    assert l4.to_value(None, OK, Probability()) is None


@pytest.mark.parametrize("p", [-0.01, 1.01])
def test_p_outside_the_unit_interval_is_a_program_error(p):
    with pytest.raises(ValueError):
        l4.to_value(p, OK, Ordinal(5))


def test_values_never_leave_the_type():
    """S2: どの p でも値は型の中（段は 1..K、確率は [0, 1]）。"""
    rng = random.Random(0)
    for _ in range(2000):
        k = rng.randint(2, 9)
        p = rng.random() if rng.random() > 0.05 else rng.choice([0.0, 1.0])
        v = l4.to_value(p, OK, Ordinal(k))
        assert 1 <= v.level <= k and 0.0 <= v.p <= 1.0


@pytest.mark.parametrize("bad", [Ordinal(1), Ordinal(3, labels=("a", "b")), Ordinal(3, bounds=(0.5,)),
                                 Ordinal(3, bounds=(0.6, 0.4)), Ordinal(3, bounds=(0.0, 0.5)), Ordinal(3, bounds=(0.5, 1.0))])
def test_bad_output_types_are_rejected(bad):
    with pytest.raises(InputError):
        l4.check_output(bad)


def test_good_output_types_pass():
    l4.check_output(Probability())
    l4.check_output(Ordinal(5, labels=tuple("abcde"), bounds=(0.1, 0.2, 0.3, 0.4)))
