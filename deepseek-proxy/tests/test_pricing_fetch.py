"""Regression tests for the official DeepSeek pricing-page parser."""

from proxy.pricing import DEFAULT_PRICING, parse_pricing_html


PRICING_HTML = """\
<html><body>
<table>
  <tr><th>MODEL</th><th>DeepSeek-V4.1-Flash</th><th>DeepSeek-V4-Pro</th></tr>
  <tr><td>PRICING</td><td>1M INPUT TOKENS (CACHE HIT)</td><td>OFF-PEAK</td><td>$0.003</td><td>$0.022</td></tr>
  <tr><td>PEAK</td><td>$0.006</td><td>$0.044</td></tr>
  <tr><td>1M INPUT TOKENS (CACHE MISS)</td><td>OFF-PEAK</td><td>$0.15</td><td>$0.66</td></tr>
  <tr><td>PEAK</td><td>$0.30</td><td>$1.32</td></tr>
  <tr><td>1M OUTPUT TOKENS</td><td>OFF-PEAK</td><td>$0.60</td><td>$1.98</td></tr>
  <tr><td>PEAK</td><td>$1.20</td><td>$3.96</td></tr>
</table>
</body></html>
"""


def test_parse_official_table_layout():
    result = parse_pricing_html(PRICING_HTML)

    assert result == DEFAULT_PRICING


def test_parser_tracks_reversed_model_columns():
    reversed_html = PRICING_HTML.replace(
        "<th>DeepSeek-V4.1-Flash</th><th>DeepSeek-V4-Pro</th>",
        "<th>DeepSeek-V4-Pro</th><th>DeepSeek-V4.1-Flash</th>",
    )
    for first, second in (
        ("$0.003", "$0.022"),
        ("$0.006", "$0.044"),
        ("$0.15", "$0.66"),
        ("$0.30", "$1.32"),
        ("$0.60", "$1.98"),
        ("$1.20", "$3.96"),
    ):
        reversed_html = reversed_html.replace(
            f"<td>{first}</td><td>{second}</td>",
            f"<td>{second}</td><td>{first}</td>",
        )

    assert parse_pricing_html(reversed_html) == DEFAULT_PRICING


def test_parser_fails_closed_for_incomplete_or_ambiguous_tables():
    assert parse_pricing_html("<html><body>pricing is changing</body></html>") is None

    incomplete = PRICING_HTML.replace("<td>$3.96</td>", "<td>unknown</td>")
    assert parse_pricing_html(incomplete) is None


def test_parser_does_not_accept_peak_prices_below_off_peak():
    invalid = PRICING_HTML.replace("<td>$0.006</td>", "<td>$0.001</td>")
    assert parse_pricing_html(invalid) is None
