"""Local backup for the Company Ops Cluster 1 & 2 India daily report -- same data
pulls, same email format as the n8n Cloud workflow ("Company Ops Cluster 1 & 2 India -
Daily Report"), but runs entirely from this repo so it isn't exposed to n8n Cloud's
intermittent Task Runner hangs. Reads and writes the SAME n8n Data Table snapshot
(cieuZhQTdDCB2sgP) the n8n workflow uses, so future n8n runs stay consistent with
whatever this script writes today.

Default run builds and prints the report but sends nothing; --send actually emails it,
via this repo's existing email_transport.py (proven working: sreenandan.m@lh2.ai ->
bhanu.enamala@lh2.ai, earlier this session).

Usage:
  python opsdata/company_ops_cluster_report_send.py            preview only
  python opsdata/company_ops_cluster_report_send.py --send
"""
import argparse
import datetime as dt
import html as htmlmod
import json
import os
import sys
import time
import urllib.error
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TABLE_ID = "cieuZhQTdDCB2sgP"
CLUSTERS = [("cluster1", "Company Ops Cluster 1 India"), ("cluster2", "Company Ops Cluster 2 India")]

CO_DASHBOARD_ROWS = [
    ("Cold Lead Assigned", {"Cold called assigned"}, True),
    ("Outreach Sent", {"LinkedIn sent", "Cold called assigned"}, True),
    ("LinkedIn Connected", {"LinkedIn connected"}, False),
    ("No Pickup / Callback", {"No pickup", "Retired: Callback +1 day (use No pickup + task instead)"}, False),
    ("Replied", {"Replied"}, False),
    ("1st Interest", {"1st interest sent", "1st interest follow up"}, False),
    ("Discovery Call", {"Discovery call", "Call rescheduled"}, False),
    ("One Pager", {"One pager requested", "One pager follow up", "One pager received"}, False),
    ("LOI Signed", {"LOI signed"}, False),
    ("Contract Signed", {"Contract signed"}, False),
    ("Ops Data Handover", {"Ops data handover done"}, False),
    ("Payment Initiation", {"Payment initiation"}, False),
    ("Closed/Won", {"Closed/Won"}, False),
]
CO_ROW_LABELS = [r[0] for r in CO_DASHBOARD_ROWS] + ["Dead"]


def _env():
    d = dict(os.environ)
    with open(os.path.join(ROOT, ".env"), encoding="utf-8-sig") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                d.setdefault(k, v.strip())
    return d


ENV = _env()
HUBSPOT_KEY = ENV["HUBSPOT_API_KEY"]
N8N_BASE = ENV["n8n_cloud_url"].rstrip("/")
N8N_KEY = ENV["n8n_cloud_api_key"]
TABLE_URL = f"{N8N_BASE}/api/v1/data-tables/{TABLE_ID}"


def hs(method, path, body=None):
    for attempt in range(5):
        req = urllib.request.Request(
            "https://api.hubapi.com" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"HTTP {e.code} on {path}: {e.read().decode(errors='replace')[:500]}")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"Network error on {path}: {e}")


def n8n(method, path, body=None):
    req = urllib.request.Request(
        f"{TABLE_URL}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-N8N-API-KEY": N8N_KEY, "Content-Type": "application/json"},
        method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def ist_date(ts):
    if ts is None:
        return None
    try:
        ms = int(ts)
        return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).astimezone(IST).date().isoformat()
    except (TypeError, ValueError):
        s = str(ts).replace("Z", "+00:00")
        return dt.datetime.fromisoformat(s).astimezone(IST).date().isoformat()


def row_for_label(lab):
    if lab.startswith("Dead"):
        return "Dead"
    for rowlab, labels, _ in CO_DASHBOARD_ROWS:
        if lab in labels:
            return rowlab
    return None


def fetch_cluster(pipeline_label):
    pipelines = hs("GET", "/crm/v3/pipelines/deals")["results"]
    pipe = next((p for p in pipelines if p["label"] == pipeline_label), None)
    if not pipe:
        return {"ok": False, "error": f"pipeline {pipeline_label!r} not found"}
    stage_label = {st["id"]: st["label"] for st in pipe["stages"]}
    today = dt.datetime.now(IST).date().isoformat()
    start = f"{today}T00:00:00Z"

    after, cand_ids = None, []
    while True:
        body = {"filterGroups": [{"filters": [
                    {"propertyName": "pipeline", "operator": "EQ", "value": pipe["id"]},
                    {"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": start}]}],
                "properties": ["dealname", "hs_v2_date_entered_current_stage"], "limit": 200}
        if after:
            body["after"] = after
        r = hs("POST", "/crm/v3/objects/deals/search", body)
        for x in r.get("results", []):
            dtv = x["properties"].get("hs_v2_date_entered_current_stage")
            if dtv and ist_date(dtv) == today:
                cand_ids.append(x["id"])
        after = (r.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break

    dash_flow = {lab: 0 for lab in CO_ROW_LABELS}
    engaged = set()
    for did in cand_ids:
        d = hs("GET", f"/crm/v3/objects/deals/{did}?propertiesWithHistory=dealstage")
        hist = (d.get("propertiesWithHistory") or {}).get("dealstage") or []
        touched = False
        for e in hist:
            if ist_date(e.get("timestamp")) != today:
                continue
            lab = stage_label.get(e.get("value"))
            if not lab:
                continue
            src = e.get("sourceType")
            if src == "CRM_UI":
                touched = True
            rowlab = row_for_label(lab)
            if rowlab and (rowlab != "Dead" and CO_DASHBOARD_ROWS[[r[0] for r in CO_DASHBOARD_ROWS].index(rowlab)][2] or src == "CRM_UI"):
                dash_flow[rowlab] += 1
        if touched:
            engaged.add(did)

    current_state = {}
    for st in sorted(pipe["stages"], key=lambda s: int(s.get("displayOrder", 0))):
        r = hs("POST", "/crm/v3/objects/deals/search", {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline", "operator": "EQ", "value": pipe["id"]},
                {"propertyName": "dealstage", "operator": "EQ", "value": st["id"]}]}],
            "properties": ["dealname"], "limit": 1, "total": True})
        current_state[st["label"]] = r.get("total", 0)

    return {"ok": True, "dashboard_flow": dash_flow, "engaged_deal_ids": sorted(engaged), "current_state": current_state}


def date_minus(date_str, n):
    d = dt.date.fromisoformat(date_str) - dt.timedelta(days=n)
    return d.isoformat()


def cumulative_for(prior_cum, today_flow):
    return {lab: (prior_cum or {}).get(lab, 0) + (today_flow or {}).get(lab, 0) for lab in CO_ROW_LABELS}


def snapshot_for(all_rows, date_str):
    matches = [r for r in all_rows if r["date"] == date_str]
    if not matches:
        return None
    last = max(matches, key=lambda r: r["id"])
    return json.loads(last["payload"])


def build_rows(today_snap, yest_snap, roll7_snaps, prev7_snaps, key):
    have_r7 = all(s and s.get(key) for s in roll7_snaps)
    have_p7 = all(s and s.get(key) for s in prev7_snaps)
    rows = []
    for label in CO_ROW_LABELS:
        sub = today_snap.get(key)
        inception = sub["cumulative"][label] if sub else None
        today_v = sub["dashboard_flow"][label] if sub else None
        yest_sub = yest_snap.get(key) if yest_snap else None
        yest_v = yest_sub["dashboard_flow"][label] if yest_sub else None
        roll7_v = sum(s[key]["dashboard_flow"][label] for s in roll7_snaps) if have_r7 else None
        prev7_v = sum(s[key]["dashboard_flow"][label] for s in prev7_snaps) if have_p7 else None
        rows.append({"label": label, "inception": inception, "today": today_v, "yest": yest_v,
                      "roll7": roll7_v, "prev7": prev7_v})
    return rows


def build_top(today_snap, s7_snap, roll7_snaps, prev7_snaps, key):
    sub = today_snap.get(key)
    if not sub:
        return None
    have_r7 = all(s and s.get(key) for s in roll7_snaps)
    have_p7 = all(s and s.get(key) for s in prev7_snaps)
    s7_sub = s7_snap.get(key) if s7_snap else None
    won_today = sub["cumulative"]["Closed/Won"]
    won_delta = (won_today - s7_sub["cumulative"]["Closed/Won"]) if s7_sub else None
    le_today = len(sub["engaged_deal_ids"])
    le_roll7 = len(set().union(*(s[key]["engaged_deal_ids"] for s in roll7_snaps))) if have_r7 else None
    le_prev7 = len(set().union(*(s[key]["engaged_deal_ids"] for s in prev7_snaps))) if have_p7 else None
    return {"wonToday": won_today, "wonDelta": won_delta, "leToday": le_today, "leRoll7": le_roll7, "lePrev7": le_prev7}


def fmt(n):
    return "-" if n is None else f"{n:,}"


def pct_change(cur, prev):
    if prev in (0, None) or prev is None:
        return None if not cur else 100.0
    return (cur - prev) / prev * 100


def chip_text(pct):
    if pct is None:
        return "flat"
    arrow = "down" if pct < 0 else "up"
    return f"{arrow} {abs(round(pct))}%"


def chip_html(pct, up="#1a7f37", down="#c0362c", muted="#6b7280"):
    if pct is None:
        return f'<span style="color:{muted};">flat</span>'
    arrow = "&#8595;" if pct < 0 else "&#8593;"
    color = down if pct < 0 else up
    return f'<span style="color:{color};font-weight:700;">{arrow} {abs(round(pct))}%</span>'


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_short_date(iso):
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{MONTHS[m - 1]} {d:02d}"


def gap_td():
    return '<td style="width:10px;border:none;"></td>'


def render_section(title, rows, top, roll7_dates, prev7_dates, today, yesterday):
    won_delta = top["wonDelta"]
    won_sub = "flat this week" if not won_delta else f"{'+' if won_delta > 0 else ''}{won_delta} this week"
    text_lines = [title, "",
                  f"WON TO DATE: {fmt(top['wonToday'])}  ({won_sub})",
                  f"7-DAY LEADS ENGAGED: {fmt(top['leRoll7'])}  (vs {fmt(top['lePrev7'])} prev7)",
                  f"DAILY LEADS ENGAGED: {fmt(top['leToday'])}", "",
                  f"{'Stage':<26}{'Inception':>10}{'Roll7':>8}{'Prev7':>8}{'Today':>8}{'Yest':>7}"]
    for r in rows:
        text_lines.append(f"{r['label']:<26}{fmt(r['inception']):>10}{fmt(r['roll7']):>8}{fmt(r['prev7']):>8}{fmt(r['today']):>8}{fmt(r['yest']):>7}")

    body_rows = ""
    for r in rows:
        bold = r["label"] in ("Closed/Won", "Dead")
        style = "font-weight:800;" if bold else "font-weight:600;"
        rowbg = "#eef2f7" if bold else "#ffffff"
        body_rows += f'''<tr style="background:{rowbg};">
          <td style="{style}font-size:13px;padding:7px 10px;border:1px solid #e2e4e8;">{r['label']}</td>
          <td style="text-align:right;font-size:13px;padding:7px 10px;border:1px solid #e2e4e8;font-style:italic;background:#eaf1fb;">{fmt(r['inception'])}</td>
          {gap_td()}
          <td style="text-align:right;font-size:13px;padding:7px 10px;border:1px solid #e2e4e8;">{fmt(r['roll7'])}</td>
          <td style="text-align:right;font-size:13px;padding:7px 10px;border:1px solid #e2e4e8;">{fmt(r['prev7'])}</td>
          <td style="text-align:right;font-size:13px;padding:7px 10px;border:1px solid #e2e4e8;">{chip_html(pct_change(r['roll7'], r['prev7']))}</td>
          {gap_td()}
          <td style="text-align:right;font-size:13px;padding:7px 10px;border:1px solid #e2e4e8;">{fmt(r['today'])}</td>
          <td style="text-align:right;font-size:13px;padding:7px 10px;border:1px solid #e2e4e8;">{fmt(r['yest'])}</td>
          <td style="text-align:right;font-size:13px;padding:7px 10px;border:1px solid #e2e4e8;">{chip_html(pct_change(r['today'], r['yest']))}</td>
        </tr>'''

    html = f'''
    <h2 style="margin:26px 0 14px;border-top:2px solid #e2e4e8;padding-top:18px;">{title}</h2>
    <table style="width:100%;border-collapse:collapse;background:#fafbfc;border:1px solid #e2e4e8;border-radius:6px;margin-bottom:18px;">
      <tr>
        <td style="text-align:center;padding:18px 24px;"><div style="font-size:11px;font-weight:800;color:#6b7280;">WON TO DATE</div><div style="font-size:26px;font-weight:800;margin-top:4px;">{fmt(top['wonToday'])}</div><div style="font-size:13px;margin-top:4px;">{won_sub}</div></td>
        <td style="text-align:center;padding:18px 24px;"><div style="font-size:11px;font-weight:800;color:#6b7280;">7-DAY LEADS ENGAGED</div><div style="font-size:26px;font-weight:800;margin-top:4px;">{fmt(top['leRoll7'])}</div><div style="font-size:13px;margin-top:4px;">{chip_html(pct_change(top['leRoll7'], top['lePrev7']))}</div></td>
        <td style="text-align:center;padding:18px 24px;"><div style="font-size:11px;font-weight:800;color:#6b7280;">DAILY LEADS ENGAGED</div><div style="font-size:26px;font-weight:800;margin-top:4px;">{fmt(top['leToday'])}</div></td>
      </tr>
    </table>
    <table style="border-collapse:separate;border-spacing:0;width:100%;margin-top:14px;">
      <tr>
        <td style="border:none;"></td>
        <td style="background:#eceef2;text-align:center;font-size:11px;font-weight:800;letter-spacing:.04em;padding:8px 6px;border:1px solid #e2e4e8;">INCEPTION TO DATE</td>
        {gap_td()}
        <td colspan="3" style="background:#eceef2;text-align:center;font-size:11px;font-weight:800;letter-spacing:.04em;padding:8px 6px;border:1px solid #e2e4e8;">ROLLING 7 DAYS</td>
        {gap_td()}
        <td colspan="3" style="background:#eceef2;text-align:center;font-size:11px;font-weight:800;letter-spacing:.04em;padding:8px 6px;border:1px solid #e2e4e8;">DAILY</td>
      </tr>
      <tr>
        <td style="border:none;"></td>
        <td style="background:#eaf1fb;text-align:center;font-size:11px;font-weight:700;padding:6px;border:1px solid #e2e4e8;">All-time</td>
        {gap_td()}
        <td style="background:#f5f6f8;text-align:center;font-size:11px;font-weight:700;padding:6px;border:1px solid #e2e4e8;">{fmt_short_date(roll7_dates[0])} - {fmt_short_date(roll7_dates[1])}</td>
        <td style="background:#f5f6f8;text-align:center;font-size:11px;font-weight:700;padding:6px;border:1px solid #e2e4e8;">{fmt_short_date(prev7_dates[0])} - {fmt_short_date(prev7_dates[1])}</td>
        <td style="background:#f5f6f8;text-align:center;font-size:11px;font-weight:700;padding:6px;border:1px solid #e2e4e8;">Change</td>
        {gap_td()}
        <td style="background:#f5f6f8;text-align:center;font-size:11px;font-weight:700;padding:6px;border:1px solid #e2e4e8;">{fmt_short_date(today)}</td>
        <td style="background:#f5f6f8;text-align:center;font-size:11px;font-weight:700;padding:6px;border:1px solid #e2e4e8;">{fmt_short_date(yesterday)}</td>
        <td style="background:#f5f6f8;text-align:center;font-size:11px;font-weight:700;padding:6px;border:1px solid #e2e4e8;">Change</td>
      </tr>
      {body_rows}
    </table>'''
    return {"html": html, "text": "\n".join(text_lines)}


def render_current_state(title, state):
    if not state:
        return {"html": "", "text": ""}
    dead_total = sum(c for l, c in state.items() if l.startswith("Dead"))
    live_rows = [(l, c) for l, c in state.items() if not l.startswith("Dead")]
    rows = live_rows + [("Dead (all sub-reasons)", dead_total)]
    rows_html = "".join(
        f'<tr><td style="font-size:12px;padding:5px 10px;border:1px solid #e2e4e8;">{htmlmod.escape(l)}</td>'
        f'<td style="font-size:12px;padding:5px 10px;border:1px solid #e2e4e8;text-align:right;">{fmt(c)}</td></tr>'
        for l, c in rows)
    html = f'''
    <h3 style="margin:16px 0 8px;font-size:14px;">{title} -- current pipeline state (deals in each stage right now)</h3>
    <table style="border-collapse:collapse;width:100%;max-width:520px;">
      <tr><td style="font-size:11px;font-weight:800;padding:5px 10px;border:1px solid #e2e4e8;background:#eceef2;">Stage</td>
          <td style="font-size:11px;font-weight:800;padding:5px 10px;border:1px solid #e2e4e8;background:#eceef2;text-align:right;">Deals now</td></tr>
      {rows_html}
    </table>'''
    text_lines = [f"{title} -- current pipeline state (right now)"] + [f"  {l:<40} {c}" for l, c in rows]
    return {"html": html, "text": "\n".join(text_lines)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    args = ap.parse_args()

    today = dt.datetime.now(IST).date().isoformat()
    print(f"Fetching Cluster 1 & 2 India live from HubSpot for {today}...")

    results = {}
    for key, label in CLUSTERS:
        print(f"  {label}...")
        results[key] = fetch_cluster(label)
        if not results[key]["ok"]:
            print(f"    FAILED: {results[key]['error']}")
        else:
            nonzero = {k: v for k, v in results[key]["dashboard_flow"].items() if v}
            print(f"    ok -- today's activity: {nonzero or '(none)'}, engaged: {len(results[key]['engaged_deal_ids'])}")

    print("Reading/writing daily history (n8n Data Table)...")
    all_rows = n8n("GET", "/rows").get("data", [])
    yesterday = date_minus(today, 1)
    yest_snap = snapshot_for(all_rows, yesterday) or {}
    existing_today = snapshot_for(all_rows, today) or {"date": today}

    today_snap = dict(existing_today)
    for key, _ in CLUSTERS:
        if results[key]["ok"]:
            prior_cum = (yest_snap.get(key) or {}).get("cumulative")
            today_snap[key] = {
                "cumulative": cumulative_for(prior_cum, results[key]["dashboard_flow"]),
                "dashboard_flow": results[key]["dashboard_flow"],
                "engaged_deal_ids": results[key]["engaged_deal_ids"],
            }
        else:
            today_snap[key] = existing_today.get(key)

    n8n("POST", "/rows", {"data": [{"date": today, "payload": json.dumps(today_snap)}]})
    print("  saved.")

    all_rows = n8n("GET", "/rows").get("data", [])  # re-read to include what we just wrote
    roll7_snaps = [today_snap] + [snapshot_for(all_rows, date_minus(today, i)) for i in range(1, 7)]
    prev7_snaps = [snapshot_for(all_rows, date_minus(today, i)) for i in range(7, 14)]
    s7_snap = snapshot_for(all_rows, date_minus(today, 7))
    roll7_dates = [date_minus(today, 6), today]
    prev7_dates = [date_minus(today, 13), date_minus(today, 7)]

    sections = []
    for key, label in CLUSTERS:
        top = build_top(today_snap, s7_snap, roll7_snaps, prev7_snaps, key)
        if top:
            rows = build_rows(today_snap, yest_snap, roll7_snaps, prev7_snaps, key)
            sections.append(render_section(label, rows, top, roll7_dates, prev7_dates, today, yesterday))
        else:
            sections.append({"html": f'<p style="color:#c0362c;">{htmlmod.escape(label)}: no data available.</p>',
                              "text": f"{label}: no data available."})

    today_fmt = dt.datetime.strptime(today, "%Y-%m-%d").strftime("%b %d, %Y")
    subject = f"Company Ops Cluster 1 & 2 India - Daily Report - {today_fmt}"
    html_body = (f'<div style="font-family:Arial,Helvetica,sans-serif;color:#1c2130;max-width:900px;">'
                 f'<h1 style="margin-bottom:4px;">{htmlmod.escape(subject)}</h1>'
                 f'<p style="color:#6b7280;margin-top:0;font-size:12px;">Pulled live from HubSpot (local run, bypassing n8n Cloud).</p>'
                 + "".join(s["html"] for s in sections) + "</div>")
    text_body = "\n\n".join(s["text"] for s in sections if s["text"])

    print("\n" + "=" * 70)
    print(text_body)
    print("=" * 70)

    if not args.send:
        print("\nDry run -- nothing sent. Re-run with --send to actually email this.")
        return

    # local email_transport.py has no OAuth/SMTP creds configured -- send instead through
    # the tiny n8n Cloud webhook-only workflow (no HubSpot calls, so it's not exposed to
    # the Code-node Task Runner hang), which reuses n8n's already-authorized Gmail credential.
    webhook_url = f"{N8N_BASE}/webhook/send-report-email"
    req = urllib.request.Request(webhook_url,
        data=json.dumps({"to": "bhanu.enamala@lh2.ai", "from": "sreenandan.m@lh2.ai",
                          "subject": subject, "html": html_body}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read())
    print(f"\nSent via n8n webhook -> Gmail: message id {result.get('id')}")


if __name__ == "__main__":
    main()
