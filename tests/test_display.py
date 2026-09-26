"""화면·파일에 쓰는 숫자 표기. GUI 없이도 시험된다."""

from __future__ import annotations


def test_shares_are_shown_with_a_sign_so_selling_is_visible():
    """순매도를 순매수처럼 보이게 하면 안 된다."""
    from chartfinder.display import _shares

    assert _shares(18_825) == "+1.9만"
    assert _shares(-47_910) == "-4.8만"
    assert _shares(300) == "+300"
    assert _shares(-300) == "-300"


def test_missing_flow_numbers_show_a_dash_not_zero():
    """수급을 안 받은 것과 순매수가 0인 것은 다르다."""
    from chartfinder.display import _days, _money, _shares

    assert _shares(None) == "-"
    assert _days(None) == "-"
    assert _money(None) == "-"
    assert _shares(0) == "+0"


def test_money_switches_unit_at_a_trillion():
    from chartfinder.display import _money

    assert _money(238.9) == "239억"
    assert _money(12_500) == "1.2조"


def test_recommended_preset_includes_flow_confirmation():
    """권하는 구성에 수급 확인이 빠져 있으면 안 된다."""
    from chartfinder import presets as presets_mod

    preset = next(p for _, p in presets_mod.load_all() if "추천" in p.name)
    keys = {spec.key for spec in preset.conditions}
    assert {"both_net_buy", "net_buy_ratio"} <= keys
    assert preset.needs_flows and preset.needs_fundamentals and preset.needs_profiles


def test_net_buy_amount_keeps_its_sign_and_unit():
    """순매수 금액은 부호와 단위가 함께 있어야 읽힌다."""
    from chartfinder.display import _signed_money

    assert _signed_money(12.5) == "+12억"
    assert _signed_money(-25.5) == "-26억"
    assert _signed_money(15_000) == "+1.5조"
    assert _signed_money(None) == "-"


def test_small_amounts_keep_one_decimal_so_they_are_not_rounded_to_zero():
    """0.4억을 '+0억' 으로 쓰면 순매수가 없는 것처럼 보인다."""
    from chartfinder.display import _signed_money

    assert _signed_money(0.4) == "+0.4억"
    assert _signed_money(-0.3) == "-0.3억"


def test_flow_amount_columns_are_labelled_in_won_not_shares():
    from chartfinder.screener import EXPORT_LABELS

    assert EXPORT_LABELS["foreign_value_5d"] == "외국인 순매수 5일(억)"
    assert EXPORT_LABELS["inst_value_20d"] == "기관 순매수 20일(억)"
    # 주식 수 컬럼도 단위를 명시해 둔다
    assert "(주)" in EXPORT_LABELS["foreign_net_5d"]
