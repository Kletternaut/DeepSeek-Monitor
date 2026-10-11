"""Tests for the price model, peak/off-peak logic and cost calculation."""

from proxy.cost_calc import calc_costs, calc_usage_cost
from proxy.pricing import (
    DEFAULT_PRICING,
    PRICING_SETTING_KEY,
    get_tariff,
    is_peak,
    load_pricing,
    normalize_model,
    save_pricing,
)
from proxy.storage import Storage
from proxy.usage import TokenUsage

# Verifizierte Zeiten:
#   2026-09-24 = Donnerstag (Werktag, kein Feiertag)
#   Peak-Fenster (UTC): 01:00-04:00 und 06:00-10:00
PEAK_TS = "2026-09-24T11:30:42+02:00"  # UTC 09:30 -> Peak
OFFPEAK_TS = "2026-09-24T13:00:00+02:00"  # UTC 11:00 -> Off-Peak
SATURDAY_TS = "2026-09-26T09:00:00+02:00"  # UTC 07:00 Sa -> Off-Peak (Wochenende)
MONDAY_PEAK_TS = "2026-09-28T09:00:00+02:00"  # UTC 07:00 Mo -> Peak


class TestIsPeak:
    def test_peak_weekday(self):
        assert is_peak(PEAK_TS) is True

    def test_offpeak_weekday(self):
        assert is_peak(OFFPEAK_TS) is False

    def test_chinese_public_holiday_is_off_peak(self):
        # Mid-Autumn Festival: the Chinese calendar is used even though the
        # timestamp is represented with a Berlin offset.
        assert is_peak("2026-09-25T11:30:42+02:00") is False

    def test_chinese_public_holiday_is_offpeak_even_in_window(self):
        # 2026-09-25 is an official Chinese public holiday (Mid-Autumn Festival).
        assert is_peak("2026-09-25T09:30:00+02:00") is False

    def test_weekend_is_offpeak_even_in_window(self):
        # Samstag 07:00 UTC liegt im Peak-Fenster, aber Wochenende -> off-peak.
        assert is_peak(SATURDAY_TS) is False

    def test_monday_peak(self):
        assert is_peak(MONDAY_PEAK_TS) is True

    def test_parse_error_returns_offpeak(self):
        assert is_peak("not-a-timestamp") is False

    def test_empty_returns_offpeak(self):
        assert is_peak("") is False


class TestNormalizeModel:
    def test_flash_variants(self):
        for m in ["deepseek-flash", "deepseek-v4-flash", "claude-sonnet-4", "claude-haiku-3", ""]:
            assert normalize_model(m) == "deepseek-flash"

    def test_pro_variants(self):
        for m in ["deepseek-v4-pro", "claude-opus-4", "deepseek-pro"]:
            assert normalize_model(m) == "deepseek-v4-pro"


class TestGetTariff:
    def test_peak_vs_offpeak(self):
        flash_peak = get_tariff("deepseek-flash", PEAK_TS)
        flash_off = get_tariff("deepseek-flash", OFFPEAK_TS)
        assert flash_peak == {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20}
        assert flash_off == {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60}

    def test_pro_model_uses_configured_pro_rates(self):
        pro_peak = get_tariff("deepseek-v4-pro", "2026-09-11T11:30:42+02:00")
        assert pro_peak == {"cache_hit": 0.044, "cache_miss": 1.32, "output": 3.96}

    def test_unknown_model_falls_back_to_flash(self):
        tariff = get_tariff("gibberish", PEAK_TS)
        assert tariff == {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20}


class TestPricingPersistence:
    def test_load_defaults_when_none(self):
        p = load_pricing(None)
        assert p == DEFAULT_PRICING

    def test_roundtrip(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        custom = {
            "deepseek-flash": {
                "off_peak": {"cache_hit": 0.001, "cache_miss": 0.10, "output": 0.50},
                "peak": {"cache_hit": 0.002, "cache_miss": 0.20, "output": 1.00},
            }
        }
        save_pricing(s, custom)
        expected = dict(DEFAULT_PRICING)
        expected["deepseek-flash"] = custom["deepseek-flash"]
        assert load_pricing(s) == expected

    def test_invalid_json_falls_back_to_defaults(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        s.set_setting(PRICING_SETTING_KEY, "{invalid json")
        assert load_pricing(s) == DEFAULT_PRICING


class TestCostCalc:
    def _usage(
        self, timestamp, input_tokens=1000, cache_read=0, output=500, model="deepseek-flash"
    ):
        return TokenUsage(
            timestamp=timestamp,
            input_tokens=input_tokens,
            output_tokens=output,
            cache_read_input_tokens=cache_read,
            model=model,
        )

    def test_offpeak_no_cache(self):
        # DeepSeek: miss = input_tokens, hit = cache_read
        # miss=1000, hit=0, output=500
        # (1000*0.15 + 0*0.003 + 500*0.60)/1e6 = (150 + 300)/1e6 = 0.00045
        u = self._usage(OFFPEAK_TS)
        assert calc_usage_cost(u) == 0.00045

    def test_offpeak_with_cache_hit(self):
        # miss=1000, hit=400, output=500
        # (1000*0.15 + 400*0.003 + 500*0.60)/1e6 = (150 + 1.2 + 300)/1e6
        u = self._usage(OFFPEAK_TS, cache_read=400)
        assert calc_usage_cost(u) == 0.0004512

    def test_peak_cost(self):
        # miss=1000, hit=0, output=500, flash peak
        # (1000*0.30 + 0 + 500*1.20)/1e6 = (300 + 600)/1e6 = 0.0009
        u = self._usage(PEAK_TS)
        assert calc_usage_cost(u) == 0.0009

    def test_pro_model_cost(self):
        # miss=1000, hit=0, output=500, pro peak
        # (1000*1.32 + 0 + 500*3.96)/1e6 = (1320 + 1980)/1e6 = 0.0033
        # Use a known Pro-rate period for the usage-cost regression.
        u = self._usage("2026-09-11T11:30:42+02:00", model="deepseek-v4-pro")
        expected = (1000 * 1.32 + 500 * 3.96) / 1e6
        assert calc_usage_cost(u) == round(expected, 8)

    def test_zero_tokens_zero_cost(self):
        u = self._usage(PEAK_TS, input_tokens=0, cache_read=0, output=0)
        assert calc_usage_cost(u) == 0.0


class TestCalcCosts:
    def test_aggregation(self):
        u1 = TokenUsage(
            timestamp=OFFPEAK_TS,
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=400,
            model="deepseek-flash",
        )
        u2 = TokenUsage(
            timestamp=PEAK_TS,
            input_tokens=200,
            output_tokens=100,
            cache_read_input_tokens=0,
            model="deepseek-flash",
        )
        r = calc_costs([u1, u2])
        assert r["requests"] == 2
        assert r["total_input_tokens"] == 1200
        assert r["total_output_tokens"] == 600
        # u1 (off-peak): miss=1000, hit=400, output=500 -> (150 + 1.2 + 300)/1e6 = 451.2/1e6
        # u2 (peak):     miss=200,  hit=0,   output=100 -> (60 + 0 + 120)/1e6 = 180/1e6
        expected = (451.2 + 180) / 1e6
        assert r["total_cost"] == round(expected, 8)
        assert "deepseek-flash" in r["per_model"]
