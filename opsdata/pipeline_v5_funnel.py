"""Restructure the Company Ops Data pipeline to the v5 SOP (Ops-Data Supply
Funnel PDF, 2026-09-15): cold-calling becomes the ONLY outreach method.

v5 on top of v4:
- new live:  No pickup, Callback +1 day (the call-outcome loop) and the closing
             tail v4 dropped: Ops data handover done, Payment initiation,
             Closed/Won (Contract signed stops being the closed-won stage)
- new dead:  Dead: Cold Call / Wrong Fit | Wrong Number | Not Interested |
             No Pickup; Dead: 1st Interest / Not Interested;
             Dead: LOI / Pricing Not Agreed
- renames:   Dead: 1st Interest -> Dead: 1st Interest / No Response
             Dead: One Pager / Less Data -> Dead: One Pager / Low Data Quality
             Dead: LOI / Terms Not Agreed -> Dead: LOI / Contractual Not Agreed
- removed:   LinkedIn sent, LinkedIn connected (deals move to the call queue
             via deals_v5_migrate.py, note-driven)
- kept:      Dead: Email Campaign / Branch Retired — not in the PDF, but it
             holds the retired email branch's deals + history; resurrecting or
             re-parking 118 dead deals is a business call, not a migration.

Same discipline as v4 (pipeline_v4_update.py):
- renames go through per-stage PATCH first (pipeline PUT matches by label;
  a rename inside the PUT silently deletes + recreates with a new id)
- a TO_REMOVE stage still holding deals is parked at the tail, not dropped;
  it disappears on a later run once empty. Sequence: this --apply, then
  deals_v5_migrate.py --apply, then this --apply again.

Dry-run by default; --apply to write. Audit JSON to audit/.
"""
import json, os, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_LABEL = "Company Ops Data"

RENAME = {
    "Dead: 1st Interest": "Dead: 1st Interest / No Response",
    "Dead: One Pager / Less Data": "Dead: One Pager / Low Data Quality",
    "Dead: LOI / Terms Not Agreed": "Dead: LOI / Contractual Not Agreed",
}
TO_REMOVE = ["LinkedIn sent", "LinkedIn connected"]

# (label, probability, kind) in v5 display order; kind: live | won | dead
V5 = [
    ("Cold called assigned", 0.05, "live"),
    ("No pickup", 0.05, "live"),
    ("Callback +1 day", 0.05, "live"),
    ("Replied", 0.12, "live"),
    ("1st interest sent", 0.20, "live"),
    ("1st interest follow up", 0.20, "live"),
    ("Discovery call", 0.35, "live"),
    ("Call rescheduled", 0.35, "live"),
    ("One pager requested", 0.45, "live"),
    ("One pager follow up", 0.45, "live"),
    ("One pager received", 0.55, "live"),
    ("LOI signed", 0.70, "live"),
    ("Contract signed", 0.85, "live"),
    ("Ops data handover done", 0.90, "live"),
    ("Payment initiation", 0.95, "live"),
    ("Closed/Won", 1.0, "won"),
    ("Dead: Cold Call / Wrong Fit", 0.0, "dead"),
    ("Dead: Cold Call / Wrong Number", 0.0, "dead"),
    ("Dead: Cold Call / Not Interested", 0.0, "dead"),
    ("Dead: Cold Call / No Pickup", 0.0, "dead"),
    ("Dead: Replied / Not Interested", 0.0, "dead"),
    ("Dead: 1st Interest / No Response", 0.0, "dead"),
    ("Dead: 1st Interest / Not Interested", 0.0, "dead"),
    ("Dead: Discovery Call / No Show", 0.0, "dead"),
    ("Dead: Discovery Call / Rejected by LH2", 0.0, "dead"),
    ("Dead: Discovery Call / Not Interested", 0.0, "dead"),
    ("Dead: One Pager / Not Received", 0.0, "dead"),
    ("Dead: One Pager / Low Data Quality", 0.0, "dead"),
    ("Dead: LOI / Pricing Not Agreed", 0.0, "dead"),
    ("Dead: LOI / Contractual Not Agreed", 0.0, "dead"),
    ("Dead: Email Campaign / Branch Retired", 0.0, "dead"),
]

def _env():
    d = dict(os.environ)
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    d[k] = v.strip()
    return d

ENV = _env()

def hs(path, payload=None, method=None):
    for attempt in range(5):
        req = urllib.request.Request("https://api.hubapi.com" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Bearer {ENV['HUBSPOT_API_KEY']}",
                     "Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"HTTP {e.code} on {path}: {e.read().decode(errors='replace')[:600]}")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise

def stage_deal_counts(pipe):
    counts = {}
    for st in pipe["stages"]:
        body = {"filterGroups": [{"filters": [
                    {"propertyName": "pipeline", "operator": "EQ", "value": pipe["id"]},
                    {"propertyName": "dealstage", "operator": "EQ", "value": st["id"]}]}],
                "limit": 1}
        s, d = hs("/crm/v3/objects/deals/search", body, method="POST")
        counts[st["label"]] = d.get("total", 0)
    return counts

def main():
    apply = "--apply" in sys.argv
    s, d = hs("/crm/v3/pipelines/deals")
    pipe = next((p for p in d["results"] if p["label"] == PIPELINE_LABEL), None)
    if not pipe:
        sys.exit(f"ABORT: pipeline {PIPELINE_LABEL!r} not found")
    cur = {st["label"]: st for st in pipe["stages"]}

    # per-stage PATCH renames, id-preserving (the v2 PUT-rename lesson)
    for old, new in RENAME.items():
        if old in cur and new not in cur:
            print(f"~ RENAME {old!r} -> {new!r} (PATCH, id kept)")
            if apply:
                hs(f"/crm/v3/pipelines/deals/{pipe['id']}/stages/{cur[old]['id']}",
                   {"label": new, "displayOrder": cur[old]["displayOrder"],
                    "metadata": cur[old]["metadata"]}, method="PATCH")
    if apply and RENAME:
        s, d = hs("/crm/v3/pipelines/deals")
        pipe = next(p for p in d["results"] if p["label"] == PIPELINE_LABEL)
        cur = {st["label"]: st for st in pipe["stages"]}

    counts = stage_deal_counts(pipe)
    stages, parked = [], []
    for label, prob, kind in V5:
        st = {"label": label, "displayOrder": len(stages),
              "metadata": {"isClosed": "true" if kind in ("won", "dead") else "false",
                           "probability": str(prob)}}
        if label in cur:
            st["id"] = cur[label]["id"]
            mark = " "
        else:
            mark = "+"
        stages.append(st)
        print(f"  {mark} {st['displayOrder']:>2}  {label:<44} p={prob}")
    for label in TO_REMOVE:
        if label not in cur:
            continue
        n = counts.get(label, 0)
        if n:
            st = dict(cur[label])
            parked.append(label)
            stages.append({"label": label, "id": st["id"], "displayOrder": len(stages),
                           "metadata": st["metadata"]})
            print(f"  ! PARKED {label!r} — still holds {n} deals; dropped once empty")
        else:
            print(f"  - DROP {label!r} (empty)")

    if not apply:
        print(f"\nDry run — {len(stages)} stages would be written. --apply to write.")
        return

    s, d = hs(f"/crm/v3/pipelines/deals/{pipe['id']}",
              {"label": pipe["label"], "displayOrder": pipe.get("displayOrder", 0),
               "stages": stages}, method="PUT")
    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    out = os.path.join(ROOT, "audit",
                       f"pipeline_v5_funnel_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"before": cur and pipe, "after": d}, f, indent=2)
    print(f"\nApplied: {len(d['stages'])} stages live"
          + (f" (parked: {parked})" if parked else "") + f". Audit: {out}")

if __name__ == "__main__":
    main()
