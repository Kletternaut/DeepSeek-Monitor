"""Lightweight web dashboard for real usage and cost data.

The dashboard runs as part of the same FastAPI app as the actual proxy. It
reads exclusively the **really recorded** usage events from the
:class:`~proxy.storage.Storage` (tokens + costs calculated from them) -- no
estimates from balance deltas are used.

The routes are registered via :func:`add_dashboard_routes` on a passed FastAPI
app. There is no global state; the ``Storage`` is passed explicitly and bound
to the endpoints via closure.

Public symbols:
    * :func:`add_dashboard_routes`
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .cost_calc import calc_usage_cost
from .labels import resolve_label
from .pricing import (
    fetch_latest_pricing,
    is_peak,
    load_pricing,
    normalize_model,
    save_pricing,
)
from .storage import Storage, row_to_usage
from .usage import TokenUsage

logger = logging.getLogger("deepseek_proxy.dashboard")

__all__ = ["add_dashboard_routes"]

_BERLIN = ZoneInfo("Europe/Berlin")


# --------------------------------------------------------------------- helper
def _window_start(days: int) -> str:
    """ISO start time ``now - days`` in Europe/Berlin (same format as the DB)."""
    start = datetime.now(_BERLIN) - timedelta(days=max(0, days))
    return start.isoformat(timespec="seconds")


def _validate_calendar_date(value: str | None, field: str) -> str | None:
    """Validate a dashboard date input without rewriting timestamp filters."""
    if value is None:
        return None
    if len(value) == 10:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid {field} date.") from exc
        if parsed.isoformat() != value:
            raise HTTPException(status_code=400, detail=f"Invalid {field} date.")
    return value


def _resolve_fingerprint(
    storage: Storage, fingerprint: str | None, label: str | None
) -> str | None:
    """Resolve ``label`` (if given) to a fingerprint.

    An explicitly set ``fingerprint`` always takes precedence. If only ``label``
    is set, the known fingerprints are searched and the first match (compared
    via :func:`resolve_label`) is returned.
    """
    if fingerprint:
        return fingerprint
    if not label:
        return None
    for fp in storage.get_distinct_fingerprints():
        if resolve_label(storage, fp) == label:
            return fp
    logger.debug("No fingerprint found for label %r.", label)
    return None


def _event_dict(storage: Storage, usage: TokenUsage, pricing: dict) -> dict:
    """Create a JSON-serializable dict for a single usage event."""
    return {
        "timestamp": usage.timestamp,
        "request_id": usage.request_id,
        "model": normalize_model(usage.model),
        "model_raw": usage.model,
        "fingerprint": usage.api_key_fingerprint,
        "label": resolve_label(storage, usage.api_key_fingerprint),
        "input_tokens": int(usage.input_tokens),
        "output_tokens": int(usage.output_tokens),
        "cache_read_tokens": int(usage.cache_read_input_tokens),
        "cache_creation_tokens": int(usage.cache_creation_input_tokens),
        "total_tokens": int(usage.total_tokens),
        "cost_usd": calc_usage_cost(usage, pricing),
        "tariff": "peak" if is_peak(usage.timestamp) else "off_peak",
    }


# ----------------------------------------------------------------- request bodies
class LabelBody(BaseModel):
    """Body for ``POST /api/labels``."""

    fingerprint: str
    label: str


class PricingBody(BaseModel):
    """Body for ``POST /api/pricing``."""

    pricing: dict


# ----------------------------------------------------------------------- routes
def add_dashboard_routes(app: FastAPI, storage: Storage) -> None:
    """Register the dashboard routes on the FastAPI app ``app``.

    The passed ``storage`` is bound to all endpoints via closure. The app is
    mutated in place; there is no global state.
    """

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_index() -> HTMLResponse:
        """Return the self-contained dashboard HTML page."""
        return HTMLResponse(content=_DASHBOARD_HTML)

    # ------------------------------------------------------------------ usage
    @app.get("/api/usage")
    async def api_usage(
        fingerprint: str | None = Query(default=None),
        label: str | None = Query(default=None),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        limit: int | None = Query(default=None),
    ) -> JSONResponse:
        """Return individual usage events, enriched with cost, tariff, label."""
        pricing = load_pricing(storage)
        fp = _resolve_fingerprint(storage, fingerprint, label)
        rows = storage.get_usage(fingerprint=fp, start=start, end=end, limit=limit)
        events = [_event_dict(storage, row_to_usage(row), pricing) for row in rows]
        return JSONResponse(events)

    # ---------------------------------------------------------------- summary
    @app.get("/api/summary")
    async def api_summary(
        fingerprint: str | None = Query(default=None),
        label: str | None = Query(default=None),
        days: int = Query(default=7),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
    ) -> JSONResponse:
        """Aggregate cost/tokens over a relative or explicit calendar-date range."""
        pricing = load_pricing(storage)
        fp = _resolve_fingerprint(storage, fingerprint, label)
        start = _validate_calendar_date(start, "start")
        end = _validate_calendar_date(end, "end")
        if start and end and start > end:
            raise HTTPException(status_code=400, detail="Start date must not be after end date.")
        query_start = start or _window_start(days)

        rows = storage.get_usage(
            fingerprint=fp,
            start=query_start,
            end=end,
            limit=None,
        )
        usages = [row_to_usage(row) for row in rows]

        total_cost = 0.0
        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_creation = 0

        peak = {"cost": 0.0, "requests": 0}
        off_peak = {"cost": 0.0, "requests": 0}
        daily: dict[str, dict] = {}
        per_label: dict[str, dict] = {}

        for usage in usages:
            cost = calc_usage_cost(usage, pricing)
            total_cost += cost
            total_input += int(usage.input_tokens)
            total_output += int(usage.output_tokens)
            total_cache_read += int(usage.cache_read_input_tokens)
            total_cache_creation += int(usage.cache_creation_input_tokens)

            bucket = peak if is_peak(usage.timestamp) else off_peak
            bucket["cost"] = round(bucket["cost"] + cost, 8)
            bucket["requests"] += 1

            day = (usage.timestamp or "")[:10] or "unknown"
            day_entry = daily.setdefault(
                day,
                {"date": day, "cost": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0},
            )
            day_entry["cost"] = round(day_entry["cost"] + cost, 8)
            day_entry["input_tokens"] += int(usage.input_tokens)
            day_entry["output_tokens"] += int(usage.output_tokens)
            day_entry["requests"] += 1

            lbl = resolve_label(storage, usage.api_key_fingerprint)
            lbl_entry = per_label.setdefault(
                lbl,
                {"cost": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0},
            )
            lbl_entry["cost"] = round(lbl_entry["cost"] + cost, 8)
            lbl_entry["input_tokens"] += int(usage.input_tokens)
            lbl_entry["output_tokens"] += int(usage.output_tokens)
            lbl_entry["requests"] += 1

        labels = [resolve_label(storage, fp2) for fp2 in storage.get_distinct_fingerprints()]

        return JSONResponse(
            {
                "days": days,
                "start": start,
                "total_cost": round(total_cost, 8),
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_cache_read_tokens": total_cache_read,
                "total_cache_creation_tokens": total_cache_creation,
                "requests": len(usages),
                "peak": peak,
                "off_peak": off_peak,
                "daily": sorted(daily.values(), key=lambda d: d["date"]),
                "per_label": per_label,
                "labels": labels,
            }
        )

    # ----------------------------------------------------------------- labels
    @app.get("/api/labels")
    async def api_labels() -> JSONResponse:
        """Return ``[{fingerprint, label}]`` for all known fingerprints."""
        return JSONResponse(
            [
                {"fingerprint": fp, "label": resolve_label(storage, fp)}
                for fp in storage.get_distinct_fingerprints()
            ]
        )

    @app.post("/api/labels")
    async def api_set_label(body: LabelBody) -> JSONResponse:
        """Set a manual label for a fingerprint."""
        storage.set_label(body.fingerprint, body.label)
        return JSONResponse({"ok": True})

    # ---------------------------------------------------------------- pricing
    @app.get("/api/pricing")
    async def api_get_pricing() -> JSONResponse:
        """Return the currently loaded price table."""
        return JSONResponse(load_pricing(storage))

    @app.post("/api/pricing")
    async def api_set_pricing(body: PricingBody) -> JSONResponse:
        """Validate and save a manually passed price table."""
        try:
            save_pricing(storage, body.pricing)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse({"ok": True})

    @app.post("/api/pricing/refresh")
    async def api_refresh_pricing() -> JSONResponse:
        """Load the prices from the DeepSeek docs and overwrite the table.

                Important: the client-side confirmation (``confirm()``) happens in the
                UI **before** the POST. This endpoint immediately overwrites the stored
        table when a result is available -- there is no further prompt here. If
                :func:`fetch_latest_pricing` returns ``None`` (no safe result), nothing
                is overwritten.
        """
        result = fetch_latest_pricing()
        if result is None:
            return JSONResponse(
                {
                    "ok": False,
                    "message": "Prices could not be determined – table unchanged.",
                }
            )
        save_pricing(storage, result)
        return JSONResponse({"ok": True, "pricing": result})


# ------------------------------------------------------------------------- HTML
_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DeepSeek Proxy – Dashboard</title>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --panel2: #1e222b; --border: #2a2f3a;
    --fg: #e6e9ef; --muted: #8a93a5; --accent: #4f9dff; --good: #3ddc97;
    --warn: #ffb454; --bad: #ff6b6b;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg);
         font: 14px/1.45 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  header { padding: 18px 24px; border-bottom: 1px solid var(--border);
           display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  .muted { color: var(--muted); }
  main { padding: 20px 24px 60px; max-width: 1200px; margin: 0 auto; }
  .card { background: var(--panel); border: 1px solid var(--border);
          border-radius: 10px; padding: 16px; margin-bottom: 16px; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
  .kpi { background: var(--panel2); border: 1px solid var(--border);
         border-radius: 8px; padding: 14px; }
  .kpi .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  .kpi .value { font-size: 22px; font-weight: 600; margin-top: 6px; }
  .kpi .sub { color: var(--muted); font-size: 12px; margin-top: 4px; }
  h2 { font-size: 15px; margin: 0 0 12px; font-weight: 600; }
  label { color: var(--muted); font-size: 12px; display: block; margin-bottom: 4px; }
  input, select, button { background: var(--panel2); color: var(--fg);
      border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; font-size: 13px; }
  button { cursor: pointer; background: var(--accent); border-color: var(--accent);
           color: #04101f; font-weight: 600; }
  button.ghost { background: var(--panel2); color: var(--fg); border-color: var(--border); font-weight: 500; }
  button:disabled { opacity: .5; cursor: default; }
  .controls { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 7px 9px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-size: 12px; font-weight: 500; text-transform: uppercase; letter-spacing: .03em; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .pill.peak { background: rgba(255,180,84,.18); color: var(--warn); }
  .pill.off_peak { background: rgba(61,220,151,.16); color: var(--good); }
  #toast { position: fixed; right: 18px; bottom: 18px; background: var(--panel2);
           border: 1px solid var(--border); border-left: 3px solid var(--accent);
           padding: 10px 14px; border-radius: 8px; max-width: 360px; display: none; }
  #toast.err { border-left-color: var(--bad); }
  .row-actions { display: flex; gap: 6px; }
  .fp { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted); }
  .empty { color: var(--muted); padding: 8px 0; }
</style>
</head>
<body>
<header>
  <h1>DeepSeek&nbsp;Proxy&nbsp;Dashboard</h1>
  <span class="muted" id="status">loading…</span>
</header>
<main>
  <div class="card">
    <div class="controls">
      <div><label>Label</label><select id="f-label"><option value="">all</option></select></div>
      <div><label>Period (days)</label>
        <select id="f-days">
          <option value="1">1</option><option value="7" selected>7</option>
          <option value="14">14</option><option value="30">30</option>
          <option value="90">90</option>
        </select>
      </div>
      <div><label>Start (optional)</label><input type="date" id="f-start"></div>
      <div><label>End (optional)</label><input type="date" id="f-end"></div>
      <div><button id="btn-apply">Apply</button></div>
      <div><button class="ghost" id="btn-refresh-pricing">Reload prices</button></div>
    </div>
  </div>

  <div class="grid" id="kpis"></div>

  <div class="card">
    <h2>Peak vs. Off-Peak</h2>
    <div class="grid" id="tariff-cards"></div>
  </div>

  <div class="card">
    <h2>Daily breakdown</h2>
    <div id="daily-wrap"><p class="empty">No data.</p></div>
  </div>

  <div class="card">
    <h2>Breakdown by label</h2>
    <div id="per-label-wrap"><p class="empty">No data.</p></div>
  </div>

  <div class="card">
    <h2>Latest requests</h2>
    <div id="events-wrap"><p class="empty">No data.</p></div>
  </div>

  <div class="card">
    <h2>Edit labels</h2>
    <div id="labels-wrap"><p class="empty">No fingerprints known.</p></div>
  </div>
</main>
<div id="toast"></div>
<script>
const $ = (id) => document.getElementById(id);
const fmtUsd = (v) => "$" + Number(v || 0).toFixed(4);
const fmtInt = (v) => Number(v || 0).toLocaleString("en-US");
// All values inserted into innerHTML originate from stored API data and must
// be escaped, especially user-editable labels.
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[ch]));

// Show the stored ISO timestamp as local time without offset.
// The DB already stores Europe/Berlin local time; the offset is only
// truncated, no re-conversion takes place.
const fmtTs = (iso) => {
  if (!iso) return "";
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  if (!m) return iso;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
};

function toast(msg, isErr) {
  const el = $("toast");
  el.textContent = msg;
  el.className = isErr ? "err" : "";
  el.style.display = "block";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.style.display = "none"; }, 5000);
}

function qs(extra) {
  const p = new URLSearchParams();
  const label = $("f-label").value;
  if (label) p.set("label", label);
  return p;
}

async function getJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

function kpi(label, value, sub) {
  return `<div class="kpi"><div class="label">${label}</div>
    <div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

function renderSummary(s) {
  $("kpis").innerHTML =
    kpi("Total cost", fmtUsd(s.total_cost), fmtInt(s.requests) + " Requests") +
    kpi("Input tokens", fmtInt(s.total_input_tokens)) +
    kpi("Output tokens", fmtInt(s.total_output_tokens)) +
    kpi("Cache read tokens", fmtInt(s.total_cache_read_tokens)) +
    kpi("Cache creation tokens", fmtInt(s.total_cache_creation_tokens));

  $("tariff-cards").innerHTML =
    kpi("Peak", fmtUsd(s.peak.cost), fmtInt(s.peak.requests) + " Requests") +
    kpi("Off-Peak", fmtUsd(s.off_peak.cost), fmtInt(s.off_peak.requests) + " Requests");

  const d = s.daily || [];
  if (!d.length) {
    $("daily-wrap").innerHTML = '<p class="empty">No data.</p>';
  } else {
    $("daily-wrap").innerHTML = `<table><thead><tr>
      <th>Date</th><th class="num">Cost</th><th class="num">Input</th>
      <th class="num">Output</th><th class="num">Requests</th></tr></thead><tbody>` +
      d.map(r => `<tr><td>${esc(r.date)}</td><td class="num">${fmtUsd(r.cost)}</td>
        <td class="num">${fmtInt(r.input_tokens)}</td>
        <td class="num">${fmtInt(r.output_tokens)}</td>
        <td class="num">${fmtInt(r.requests)}</td></tr>`).join("") +
      `</tbody></table>`;
  }

  const pl = s.per_label || {};
  const keys = Object.keys(pl).sort();
  if (!keys.length) {
    $("per-label-wrap").innerHTML = '<p class="empty">No data.</p>';
  } else {
    $("per-label-wrap").innerHTML = `<table><thead><tr>
      <th>Label</th><th class="num">Cost</th><th class="num">Input</th>
      <th class="num">Output</th><th class="num">Requests</th></tr></thead><tbody>` +
      keys.map(k => `<tr><td>${esc(k)}</td><td class="num">${fmtUsd(pl[k].cost)}</td>
        <td class="num">${fmtInt(pl[k].input_tokens)}</td>
        <td class="num">${fmtInt(pl[k].output_tokens)}</td>
        <td class="num">${fmtInt(pl[k].requests)}</td></tr>`).join("") +
      `</tbody></table>`;
  }

  const sel = $("f-label");
  const cur = sel.value;
  const labels = s.labels || [];
  sel.innerHTML = '<option value="">all</option>' +
    labels.map(l => `<option value="${esc(l)}">${esc(l)}</option>`).join("");
  sel.value = cur;
}

function renderEvents(events) {
  if (!events.length) {
    $("events-wrap").innerHTML = '<p class="empty">No data.</p>';
    return;
  }
  $("events-wrap").innerHTML = `<table><thead><tr>
    <th>Time</th><th>Label</th><th>Model</th><th>Tariff</th>
    <th class="num">In</th><th class="num">Out</th><th class="num">Cache R</th>
    <th class="num">Cache W</th><th class="num">Cost</th></tr></thead><tbody>` +
    events.map(e => `<tr>
      <td>${esc(fmtTs(e.timestamp))}</td><td>${esc(e.label)}</td><td>${esc(e.model)}</td>
      <td><span class="pill ${esc(e.tariff)}">${esc(e.tariff)}</span></td>
      <td class="num">${fmtInt(e.input_tokens)}</td>
      <td class="num">${fmtInt(e.output_tokens)}</td>
      <td class="num">${fmtInt(e.cache_read_tokens)}</td>
      <td class="num">${fmtInt(e.cache_creation_tokens)}</td>
      <td class="num">${fmtUsd(e.cost_usd)}</td></tr>`).join("") +
    `</tbody></table>`;
}

function renderLabels(items) {
  if (!items.length) {
    $("labels-wrap").innerHTML = '<p class="empty">No fingerprints known.</p>';
    return;
  }
  $("labels-wrap").innerHTML = `<table><thead><tr>
    <th>Fingerprint</th><th>Current label</th><th>New label</th><th></th>
    </tr></thead><tbody>` +
    items.map(it => `<tr>
      <td class="fp">${esc(it.fingerprint.slice(0, 16))}…</td>
      <td>${esc(it.label)}</td>
      <td><input data-fp="${esc(it.fingerprint)}" value="${esc(it.label)}"></td>
      <td class="row-actions"><button class="ghost" data-save="${esc(it.fingerprint)}">Save</button></td>
    </tr>`).join("") + `</tbody></table>`;

  document.querySelectorAll("button[data-save]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const fp = btn.getAttribute("data-save");
      const input = document.querySelector(`input[data-fp="${fp}"]`);
      try {
        const r = await fetch("/api/labels", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({fingerprint: fp, label: input.value}),
        });
        if (!r.ok) throw new Error("HTTP " + r.status);
        toast("Label saved.");
        await reload();
      } catch (err) { toast("Error: " + err.message, true); }
    });
  });
}

async function reload() {
  try {
    const start = $("f-start").value;
    const end = $("f-end").value;
    const p = qs();
    if (start) p.set("start", start);
    if (end) p.set("end", end);
    const days = $("f-days").value;
    const s = await getJson("/api/summary?days=" + encodeURIComponent(days) + "&" + p.toString());
    renderSummary(s);

    const up = qs();
    if (start) up.set("start", start);
    if (end) up.set("end", end);
    up.set("limit", "100");
    const u = await getJson("/api/usage?" + up.toString());
    renderEvents(u || []);

    const l = await getJson("/api/labels");
    renderLabels(l || []);

    $("status").textContent =
      "As of: " + new Date().toLocaleTimeString("en-US") +
      " · " + fmtInt(s.requests) + " requests in period";
  } catch (err) {
    $("status").textContent = "Error loading";
    toast("Error: " + err.message, true);
  }
}

$("btn-apply").addEventListener("click", reload);

$("btn-refresh-pricing").addEventListener("click", async () => {
  if (!confirm("Really reload and overwrite the price table from the DeepSeek docs?")) return;
  const btn = $("btn-refresh-pricing");
  btn.disabled = true;
  try {
    const r = await fetch("/api/pricing/refresh", {method: "POST"});
    const data = await r.json();
    if (data.ok) toast("Prices updated: " + JSON.stringify(data.pricing));
    else toast(data.message || "Prices could not be loaded.", true);
  } catch (err) {
    toast("Error: " + err.message, true);
  } finally {
    btn.disabled = false;
  }
});

reload();
</script>
</body>
</html>
"""
