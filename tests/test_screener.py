import pytest

from chartfinder.screener import ConditionSpec, combine, score_one


def test_parse_spec_with_params_and_weight():
    spec = ConditionSpec.parse("rsi_oversold:period=9,threshold=35,weight=2")
    assert spec.key == "rsi_oversold"
    assert spec.params == {"period": "9", "threshold": "35"}
    assert spec.weight == 2.0


def test_parse_spec_without_params():
    assert ConditionSpec.parse("ma_alignment").params == {}


def test_parse_spec_rejects_malformed_params():
    with pytest.raises(ValueError):
        ConditionSpec.parse("rsi_oversold:period")


def test_unknown_condition_is_rejected():
    with pytest.raises(KeyError):
        ConditionSpec(key="does_not_exist")


def test_non_positive_weight_is_rejected():
    with pytest.raises(ValueError):
        ConditionSpec(key="ma_alignment", weight=0)


def test_score_one_covers_every_spec(uptrend):
    specs = [ConditionSpec("ma_alignment"), ConditionSpec("rsi_oversold")]
    scores = score_one(uptrend, specs)
    assert set(scores) == {"ma_alignment", "rsi_oversold"}


def test_combine_is_weighted_average():
    specs = [ConditionSpec("ma_alignment", weight=3), ConditionSpec("rsi_oversold", weight=1)]
    assert combine({"ma_alignment": 1.0, "rsi_oversold": 0.0}, specs) == 0.75


def test_combine_missing_score_counts_as_zero():
    specs = [ConditionSpec("ma_alignment"), ConditionSpec("rsi_oversold")]
    assert combine({"ma_alignment": 1.0}, specs) == 0.5
