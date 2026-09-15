"""Restore the LinkedIn branch (LinkedIn sent, LinkedIn connected) that v5
removed, and move deals back to it -- but ONLY the ones still sitting exactly
where the v5 migration left them (Cold called assigned). Any deal that has
progressed further since (8 are now at Replied) is left alone: restoring a
deleted stage is not a reason to erase real work that happened after.

The old stage ids (LinkedIn sent=4080987861, LinkedIn connected=4132224744)
are permanently gone once a stage is dropped from a HubSpot pipeline -- this
creates NEW stages with the same labels, in the same position they held
before v5 (LinkedIn sent -> Cold called assigned -> LinkedIn connected, then
the rest of the v5 funnel unchanged).

Source of truth for which deals to move: audit/deals_v5_migrate_20260915_151044.json
(the original v5 migration's own audit trail -- exact list of 3,007 deals it
moved and which of the two LinkedIn stages each came from).

Dry-run by default; --apply to write. Audit JSON to audit/.
"""
import json, os, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_LABEL = "Company Ops Data"
MIGRATION_AUDIT = os.path.join(ROOT, "audit", "deals_v5_migrate_20260915_151044.json")

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
    cur = {st["label"]: st for st in pipe["stages"]}
    if "LinkedIn sent" in cur or "LinkedIn connected" in cur:
        sys.exit("ABORT: a LinkedIn stage already exists live -- already restored?")
    if "Cold called assigned" not in cur:
        sys.exit("ABORT: 'Cold called assigned' not found -- unexpected pipeline state")

    # rebuild the stage list with LinkedIn sent / connected reinserted in
    # their original v4 position, everything else in its current v5 order
    stages, inserted_sent, inserted_connected = [], False, False
    for st in sorted(pipe["stages"], key=lambda s: s["displayOrder"]):
        if st["label"] == "Cold called assigned" and not inserted_sent:
            stages.append({"label": "LinkedIn sent", "displayOrder": len(stages),
                           "metadata": {"isClosed": "false", "probability": "0.03"}})
            inserted_sent = True
        stages.append({**st, "displayOrder": len(stages)})
        if st["label"] == "Cold called assigned" and not inserted_connected:
            stages.append({"label": "LinkedIn connected", "displayOrder": len(stages),
                           "metadata": {"isClosed": "false", "probability": "0.05"}})
            inserted_connected = True

    print(f"Restoring pipeline shape: {len(pipe['stages'])} -> {len(stages)} stages")
    for st in stages:
        mark = "+" if st["label"] in ("LinkedIn sent", "LinkedIn connected") else " "
        print(f"  {mark} {st['displayOrder']:>2}  {st['label']}")

    migrated = json.load(open(MIGRATION_AUDIT, encoding="utf-8"))
    linkedin_moves = [x for x in migrated if x["from"] in ("LinkedIn sent", "LinkedIn connected")]
    print(f"\n{len(linkedin_moves)} deals were originally in the LinkedIn branch")

    if not apply:
        print("\nDry run -- nothing written. Re-run with --apply.")
        return

    s, d = hs(f"/crm/v3/pipelines/deals/{pipe['id']}",
              {"label": pipe["label"], "displayOrder": pipe.get("displayOrder", 0),
               "stages": stages}, method="PUT")
    new_stage_id = {st["label"]: st["id"] for st in d["stages"]}
    print(f"\npipeline restored: LinkedIn sent={new_stage_id['LinkedIn sent']}, "
          f"LinkedIn connected={new_stage_id['LinkedIn connected']}")

    cold_called_id = new_stage_id["Cold called assigned"]
    ids = [x["id"] for x in linkedin_moves]
    live_stage = {}
    for i in range(0, len(ids), 100):
        s2, d2 = hs("/crm/v3/objects/deals/batch/read",
                   {"properties": ["dealstage"],
                    "inputs": [{"id": x} for x in ids[i:i+100]]}, method="POST")
        for r in d2.get("results", []):
            live_stage[r["id"]] = r["properties"]["dealstage"]

    to_revert = [x for x in linkedin_moves if live_stage.get(x["id"]) == cold_called_id]
    skipped = [x for x in linkedin_moves if x["id"] in live_stage
              and live_stage[x["id"]] != cold_called_id]
    print(f"reverting {len(to_revert)} deals still at Cold called assigned; "
          f"leaving {len(skipped)} alone (moved further since the v5 migration)")

    updates = [{"id": x["id"], "properties": {"dealstage": new_stage_id[x["from"]]}}
              for x in to_revert]
    for i in range(0, len(updates), 100):
        s3, d3 = hs("/crm/v3/objects/deals/batch/update",
                   {"inputs": updates[i:i+100]}, method="POST")
        if s3 == 207:
            print(f"WARNING: partial batch (207): {json.dumps(d3.get('errors'))[:400]}")

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    out = os.path.join(ROOT, "audit",
                       f"restore_linkedin_branch_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"reverted": to_revert, "left_alone_progressed": skipped,
                   "new_stage_ids": {"LinkedIn sent": new_stage_id["LinkedIn sent"],
                                     "LinkedIn connected": new_stage_id["LinkedIn connected"]}},
                  f, indent=2)
    print(f"\nApplied: {len(to_revert)} deals restored, {len(skipped)} left untouched. "
          f"Audit: {out}")

if __name__ == "__main__":
    main()
