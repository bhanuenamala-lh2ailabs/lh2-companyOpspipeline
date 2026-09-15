"""Move existing deals into their v5 stages — NOTE-DRIVEN where a note exists.

Scope: only deals currently at 'LinkedIn sent', 'LinkedIn connected' or
'Cold called assigned'. Everything else (Replied and beyond, every dead stage)
was placed by a human and is not touched. Deal OWNERS are never written.

Placement, per deal in scope:
1. Its notes are classified with dashboard/ops_note_rules.py (the portal's
   single source of truth for note text). Notes are read newest-first; the
   first note whose bucket names a call outcome decides the stage — a lead
   whose latest note says "not interested" is dead even if an earlier note
   said "callback".
2. No classifiable note -> the blanket mapping: the v5 funnel has one entry,
   so the deal joins the call queue at 'Cold called assigned'. Warmth is not
   lost: outflo_status (Connected/Request Sent) still says how they arrived.

Bucket -> stage mapping is BUCKET2STAGE below. Buckets that describe activity
rather than an outcome (Chase sent, WhatsApp outreach, System note, ...)
deliberately map to nothing.

Dry-run by default; --apply to write. Audit JSON + reviewable CSV to audit/.
"""
import csv, json, os, sys, time, urllib.error, urllib.request
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))
from ops_note_rules import classify, plain  # noqa: E402

PIPELINE_LABEL = "Company Ops Data"
SCOPE = ["LinkedIn sent", "LinkedIn connected", "Cold called assigned"]
BLANKET = "Cold called assigned"

BUCKET2STAGE = {
    "Wrong number":            "Dead: Cold Call / Wrong Number",
    "Wrong contact":           "Dead: Cold Call / Wrong Number",
    "Invalid lead":            "Dead: Cold Call / Wrong Number",
    "Company too small":       "Dead: Cold Call / Wrong Fit",
    "Disqualified — size/fit": "Dead: Cold Call / Wrong Fit",
    "Pitching own services":   "Dead: Cold Call / Wrong Fit",
    "No data held":            "Dead: Cold Call / Wrong Fit",
    "Not interested":          "Dead: Cold Call / Not Interested",
    "No pickup":               "No pickup",
    "Callback booked":         "Callback +1 day",
    "Meeting fixed":           "Discovery call",
    "Interest email sent":     "1st interest sent",
    "First email sent":        "1st interest sent",
    "One pager received":      "One pager received",
}

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

def main():
    apply = "--apply" in sys.argv
    s, d = hs("/crm/v3/pipelines/deals")
    pipe = next((p for p in d["results"] if p["label"] == PIPELINE_LABEL), None)
    if not pipe:
        sys.exit(f"ABORT: pipeline {PIPELINE_LABEL!r} not found")
    stage_id = {st["label"]: st["id"] for st in pipe["stages"]}
    for lbl in set(BUCKET2STAGE.values()) | {BLANKET}:
        if lbl not in stage_id:
            sys.exit(f"ABORT: target stage {lbl!r} missing — run pipeline_v5_funnel first")
    scope_ids = {stage_id[l] for l in SCOPE if l in stage_id}
    if not scope_ids:
        print("nothing in scope — LinkedIn stages already gone")
        return

    deals, after = [], None
    while True:
        body = {"filterGroups": [{"filters": [
                    {"propertyName": "pipeline", "operator": "EQ", "value": pipe["id"]},
                    {"propertyName": "dealstage", "operator": "IN",
                     "values": sorted(scope_ids)}]}],
                "properties": ["dealname", "dealstage"], "limit": 200}
        if after:
            body["after"] = after
        s, d = hs("/crm/v3/objects/deals/search", body, method="POST")
        deals += d["results"]
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    print(f"in scope: {len(deals)} deals across {SCOPE}")

    # notes: deal -> [note ids], then note bodies + timestamps
    deal_notes, ids = {}, [x["id"] for x in deals]
    for i in range(0, len(ids), 100):
        s, a = hs("/crm/v4/associations/deals/notes/batch/read",
                  {"inputs": [{"id": x} for x in ids[i:i+100]]}, method="POST")
        for r in a.get("results", []):
            # toObjectId is an INT here; every other id in the API is a string
            deal_notes[str(r["from"]["id"])] = [str(t["toObjectId"])
                                                for t in r.get("to", [])]
    note_ids = sorted({n for v in deal_notes.values() for n in v})
    notes = {}
    for i in range(0, len(note_ids), 100):
        s, nb = hs("/crm/v3/objects/notes/batch/read",
                   {"properties": ["hs_note_body", "hs_timestamp"],
                    "inputs": [{"id": n} for n in note_ids[i:i+100]]}, method="POST")
        for n in nb.get("results", []):
            notes[n["id"]] = (n["properties"].get("hs_timestamp") or "",
                              plain(n["properties"].get("hs_note_body") or ""))
    print(f"notes on scope deals: {len(notes)}")

    stage_label = {v: k for k, v in stage_id.items()}
    plan, moves = [], Counter()
    for x in deals:
        cur = stage_label[x["properties"]["dealstage"]]
        target, why, snippet = BLANKET, "blanket (no outcome note)", ""
        for ts, body in sorted((notes[n] for n in deal_notes.get(x["id"], [])
                                if n in notes), reverse=True):
            bucket = classify(body)
            if bucket in BUCKET2STAGE:
                target, why, snippet = BUCKET2STAGE[bucket], f"note: {bucket}", body[:60]
                break
        if target != cur:
            plan.append({"id": x["id"], "dealname": x["properties"].get("dealname"),
                         "from": cur, "to": target, "why": why, "note": snippet})
            moves[(cur, target)] += 1

    print(f"\nPlan — {len(plan)} deals move (owners untouched, stage only):\n")
    print(f"  {'from':<22} {'to':<36} count")
    for (f_, t), n in sorted(moves.items(), key=lambda kv: -kv[1]):
        print(f"  {f_:<22} {t:<36} {n:>5}")
    note_driven = [p for p in plan if p["why"] != "blanket (no outcome note)"]
    print(f"\n  note-driven: {len(note_driven)}, blanket: {len(plan) - len(note_driven)}, "
          f"already correct: {len(deals) - len(plan)}")
    for p in note_driven[:12]:
        print(f"    {p['dealname'][:28]!r:<30} -> {p['to']:<34} [{p['note']}]")

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(ROOT, "audit", f"deals_v5_migrate_{stamp}.csv"),
              "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "dealname", "from", "to", "why", "note"])
        w.writeheader()
        w.writerows(plan)
    print(f"\nfull reviewable plan: audit/deals_v5_migrate_{stamp}.csv")

    if not apply:
        print("Dry run — nothing written. --apply to move.")
        return

    for i in range(0, len(plan), 100):
        s, d = hs("/crm/v3/objects/deals/batch/update",
                  {"inputs": [{"id": p["id"],
                               "properties": {"dealstage": stage_id[p["to"]]}}
                              for p in plan[i:i+100]]}, method="POST")
        if s == 207:
            print(f"WARNING: partial batch (207): {json.dumps(d.get('errors'))[:400]}")
    with open(os.path.join(ROOT, "audit", f"deals_v5_migrate_{stamp}.json"),
              "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    print(f"Applied: {len(plan)} deals moved. Audit: audit/deals_v5_migrate_{stamp}.json")

if __name__ == "__main__":
    main()
