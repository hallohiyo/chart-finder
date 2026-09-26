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


def test_unscored_conditions_flags_columns_that_are_always_zero(uptrend):
    """데이터가 없어 0점만 나온 조건과 진짜 미달을 구분할 수 있어야 한다."""
    import pandas as pd

    from chartfinder.screener import unscored_conditions

    specs = [ConditionSpec("revenue_growth"), ConditionSpec("above_ma")]
    result = pd.DataFrame({"s_revenue_growth": [0.0, 0.0], "s_above_ma": [0.0, 0.8]})

    assert unscored_conditions(result, specs) == ["revenue_growth"]


def test_unscored_conditions_handles_empty_results():
    from chartfinder.screener import unscored_conditions

    import pandas as pd

    assert unscored_conditions(pd.DataFrame(), [ConditionSpec("above_ma")]) == []
    assert unscored_conditions(None, [ConditionSpec("above_ma")]) == []


# --------------------------------------------------------------------------- 전략별 채점


def _demo_setup(tmp_path, monkeypatch, count=6):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder import cache
    from chartfinder.datasource import get_source

    source = get_source("demo")
    tickers = source.list_tickers()[:count]
    cache.update("demo", symbols=[t.symbol for t in tickers], years=2)
    return tickers


def test_screen_multi_scores_each_strategy_separately(tmp_path, monkeypatch):
    from chartfinder.screener import screen_multi

    tickers = _demo_setup(tmp_path, monkeypatch)
    result = screen_multi(
        "demo",
        {"위": [ConditionSpec("above_ma")], "아래": [ConditionSpec("near_low")]},
        tickers=tickers,
    )
    assert {"p_위", "p_아래", "strategy", "overall"} <= set(result.columns)
    assert result["score"].between(0, 1).all()


def test_headline_score_is_the_best_strategy_not_the_average(tmp_path, monkeypatch):
    """서로 반대인 전략을 평균내면 어느 쪽도 만족 못 하는 종목이 상위에 온다."""
    from chartfinder.screener import screen_multi

    tickers = _demo_setup(tmp_path, monkeypatch)
    result = screen_multi(
        "demo",
        {"고점": [ConditionSpec("near_high")], "저점": [ConditionSpec("near_low")]},
        tickers=tickers,
    )
    for row in result.to_dict("records"):
        assert row["score"] == pytest.approx(max(row["p_고점"], row["p_저점"]), abs=1e-3)


def test_strategy_label_prefers_the_harder_strategy_on_a_tie(tmp_path, monkeypatch):
    from chartfinder.screener import screen_multi

    tickers = _demo_setup(tmp_path, monkeypatch)
    result = screen_multi(
        "demo",
        {
            "쉬움": [ConditionSpec("price_range", {"low": 0, "high": 1e9})],
            "어려움": [
                ConditionSpec("price_range", {"low": 0, "high": 1e9}),
                ConditionSpec("price_range", {"low": 0, "high": 1e9}),
            ],
        },
        tickers=tickers,
    )
    assert (result["strategy"] == "어려움").all()


def test_screen_multi_ranks_by_score_then_conditions_actually_met(tmp_path, monkeypatch):
    """동점은 '실제로 충족한 조건 비율' 로 가른다.

    전략 평균(overall)으로 가르면, 방향이 반대인 전략에도 어중간하게 맞는
    종목이 한 전략에 확실히 맞는 종목을 이긴다. 두 전략이 공유하는
    방향 무관 필터(거래대금·변동성 등) 때문에 올라간 점수라 근거가 못 된다.
    """
    from chartfinder.screener import screen_multi

    tickers = _demo_setup(tmp_path, monkeypatch, count=12)
    result = screen_multi(
        "demo",
        {"A": [ConditionSpec("above_ma")], "B": [ConditionSpec("near_low")],
         "C": [ConditionSpec("volume_surge")]},
        tickers=tickers,
    )
    keys = list(zip(result["score"], result["matched_ratio"], result["gap"]))
    assert keys == sorted(keys, key=lambda k: (-k[0], -k[1], -k[2]))


def test_screen_multi_reports_how_many_conditions_were_met(tmp_path, monkeypatch):
    """점수만으로는 왜 뽑혔는지 알 수 없다."""
    from chartfinder.screener import screen_multi

    tickers = _demo_setup(tmp_path, monkeypatch, count=8)
    result = screen_multi(
        "demo",
        {"둘": [ConditionSpec("above_ma"), ConditionSpec("volume_surge")],
         "하나": [ConditionSpec("near_low")]},
        tickers=tickers,
    )
    sizes = {"둘": 2, "하나": 1}
    for row in result.to_dict("records"):
        assert row["of"] == sizes[row["strategy"]]
        assert 0 <= row["matched"] <= row["of"]


def test_gap_shows_whether_one_strategy_clearly_won(tmp_path, monkeypatch):
    """방향이 반대인 전략에 비슷한 점수가 나면 애매한 종목이다."""
    from chartfinder.screener import screen_multi

    tickers = _demo_setup(tmp_path, monkeypatch, count=10)
    result = screen_multi(
        "demo",
        {"고점": [ConditionSpec("near_high")], "바닥": [ConditionSpec("near_low")]},
        tickers=tickers,
    )
    # 두 전략이 반대이므로 한쪽이 높으면 차이가 커야 한다
    assert (result["gap"] >= 0).all()
    assert result["gap"].max() > 0.5


def test_overall_is_kept_only_as_a_reference_column(tmp_path, monkeypatch):
    """평균은 남기되 참고용이라는 것이 저장 파일 머리글에 드러나야 한다.

    방향이 반대인 전략끼리는 평균이 의미가 없다. 이름이 '전체 적합도' 면
    근거로 오해하게 된다.
    """
    from chartfinder.screener import EXPORT_LABELS, screen_multi

    tickers = _demo_setup(tmp_path, monkeypatch, count=8)
    result = screen_multi(
        "demo",
        {"고점": [ConditionSpec("near_high")], "바닥": [ConditionSpec("near_low")]},
        tickers=tickers,
    )
    assert "overall" in result.columns
    assert "참고" in EXPORT_LABELS["overall"]


def test_screen_multi_requires_at_least_one_strategy(tmp_path, monkeypatch):
    from chartfinder.screener import screen_multi

    tickers = _demo_setup(tmp_path, monkeypatch, count=2)
    with pytest.raises(ValueError):
        screen_multi("demo", {}, tickers=tickers)


def test_shared_conditions_are_computed_once(tmp_path, monkeypatch):
    """같은 조건이 여러 전략에 있어도 종목당 한 번만 계산한다."""
    import dataclasses

    from chartfinder.conditions import base
    from chartfinder.screener import screen_multi

    tickers = _demo_setup(tmp_path, monkeypatch, count=3)
    calls = []
    original = base._REGISTRY["above_ma"]
    counted = dataclasses.replace(
        original, fn=lambda ctx, **kwargs: calls.append(1) or original.fn(ctx, **kwargs)
    )
    monkeypatch.setitem(base._REGISTRY, "above_ma", counted)
    screen_multi(
        "demo",
        {"A": [ConditionSpec("above_ma")], "B": [ConditionSpec("above_ma")],
         "C": [ConditionSpec("above_ma")]},
        tickers=tickers,
    )
    assert len(calls) == len(tickers)
