import math

import pytest

from chartfinder import scoring as sc


def test_soft_lt_full_score_when_satisfied():
    assert sc.soft_lt(25, 30) == 1.0
    assert sc.soft_lt(30, 30) == 1.0


def test_soft_lt_decays_gracefully_when_just_missed():
    near = sc.soft_lt(31, 30)
    far = sc.soft_lt(45, 30)
    assert 0.8 < near < 1.0
    assert far < 0.05
    assert near > far


def test_soft_gt_mirrors_soft_lt():
    assert sc.soft_gt(31, 30) == 1.0
    assert sc.soft_gt(29, 30) == pytest.approx(sc.soft_lt(31, 30))


def test_nan_and_none_score_zero():
    assert sc.soft_lt(None, 30) == 0.0
    assert sc.soft_gt(float("nan"), 30) == 0.0
    assert sc.soft_between(None, 1, 2) == 0.0


def test_soft_between_inside_and_outside():
    assert sc.soft_between(5, 1, 10) == 1.0
    assert 0.0 < sc.soft_between(11, 1, 10) < 1.0


def test_soft_near_peaks_at_target():
    assert sc.soft_near(100, 100) == 1.0
    assert sc.soft_near(105, 100) < 1.0


def test_ordered_perfect_alignment():
    assert sc.ordered([120, 110, 100, 90]) == 1.0


def test_ordered_partial_credit_for_slight_inversion():
    score = sc.ordered([100, 101, 90, 80])  # 첫 구간만 살짝 역전
    assert 0.6 < score < 1.0


def test_ordered_requires_complete_data():
    assert sc.ordered([100, None, 90]) == 0.0
