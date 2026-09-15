# OutFlo → HubSpot pull — runbook (handover)

For the person taking over this pipeline. This doc covers the **OutFlo lead
sync** only. (A Woodpecker sync is planned but not built yet — when it lands it
should follow the same shape as this one.)

## What it does, in one paragraph

`opsdata/outflo_pull_push.py` pulls every lead from OutFlo's **Company Ops**
LinkedIn campaigns and mirrors their outreach state into the HubSpot
**Company Ops Data** pipeline (portal `246897735`): one **deal per company**,
one **contact per person**, both owned by Kartik Pillai. It is **idempotent**
— run it as often as you like; it only creates what's missing and promotes
stages upward, and it never touches a deal a human has moved.

## Running it

```bash
cd <repo root>
python3 opsdata/outflo_pull_push.py            # DRY RUN — prints the plan, writes nothing
python3 opsdata/outflo_pull_push.py --apply    # actually writes to HubSpot
```

Always glance at the dry-run table before the first apply of the day: it shows
every company with its status, target stage, and action
(`CREATE` / `PROMOTE` / `SKIP`). The totals line is the sanity check.
Every apply writes an audit JSON to `audit/` (gitignored) recording exactly
what was created/changed — that's your undo map if something looks wrong.

**Prereqs:** Python 3 (stdlib only — nothing to pip-install) and a `.env` file
in the repo root, which is **not in git**. Get the keys from Bhanu:

```
HUBSPOT_API_KEY=pat-na2-...     # HubSpot private app token, portal 246897735
OUTFLO_API_KEY=...              # from reach.outflo.io/integrations
```

## The rules it enforces (don't re-learn these the hard way)

1. **Campaign scope:** every OutFlo campaign whose name contains
   "company ops" (case-insensitive) with status **ACTIVE or PAUSED**. DRAFT
   campaigns are excluded — they haven't sent anything. ⚠️ Tell the team to
   **pause campaigns, never delete them**: a deleted campaign's leads vanish
   from the API and can never be imported (we lost ~12 UAE leads this way).

2. **Bucket mapping** (OutFlo → HubSpot stage; stage names as of the v5
   restructure, `docs/OPSDATA_PIPELINE.md` — the funnel has ONE entry now,
   cold-calling, so both pre-reply OutFlo states land in the call queue):
   | OutFlo state | Deal stage |
   |---|---|
   | Request Sent / Previously Request Sent | `Cold called assigned` |
   | Connected / Previously Connected | `Cold called assigned` (warmth kept in `outflo_status`) |
   | Reply Status = Replied | `Replied` |

   "Checking" and "Failed" leads are not imported. Stage moves only **up**
   the ladder (`Cold called assigned → Replied`). A deal at any other stage
   (No pickup, Callback +1 day, Discovery call, a Dead:* stage…) is a
   human's decision and is always skipped.

3. **One deal per company.** Multiple founders at one company become several
   contacts on a single deal. Company names are normalized for matching
   (case, domain suffixes — "DoubleTick" ≡ "DoubleTick.io") so renamed or
   merged deals don't get re-created. The deal is always named after the
   **company**, never a person.

4. **Provenance is frozen at import** on each deal: `outflo_status` (state at
   import), `outflo_campaign`, `outflo_lead_id` (the dedupe key),
   `outflo_assigned_account` (which LinkedIn seat sent the outreach),
   `lead_source` = `Outflo Outreach ( Startups )`. The stage moves on;
   these never do.

5. **`replied_at`** comes from the actual OutFlo conversation timestamp when
   the lead's message is retrievable, else falls back to Last Action At.

6. **Never hardcode stage IDs.** The script resolves stages by label from the
   deal pipeline API at runtime. If the team renames a stage in the HubSpot
   UI, update the `STAGE_*` constants at the top of the script to the new
   labels — that's the only thing that breaks.

## Daily schedule (10:30 AM IST)

The script is safe to run unattended (it's dry-run-by-default only when run
by hand — the scheduled form runs `--apply` directly). On the machine that
will own the schedule:

**macOS / Linux (cron):**
```cron
30 10 * * * cd /path/to/repo && /usr/bin/python3 opsdata/outflo_pull_push.py --apply >> audit/cron.log 2>&1
```
(macOS: give the invoking shell Full Disk Access, or use `launchd`; cron uses
the machine's local timezone — confirm it's IST.)

**Windows (Task Scheduler):**
```
schtasks /create /tn "OutFlo HubSpot sync" /sc daily /st 10:30 /tr "python C:\path\to\repo\opsdata\outflo_pull_push.py --apply"
```

**Before dashboard generation:** run the same `--apply` command as the first
step of the dashboard job. It's idempotent — a no-change run takes ~1–2
minutes and writes nothing.

## Troubleshooting

- **`ABORT: no ACTIVE/PAUSED 'company ops' campaign found`** — campaigns were
  renamed without "company ops" in the name, or all archived. Fix the names
  in OutFlo or widen the filter in `pull_outflo()`.
- **`ABORT: stage '…' missing`** — someone renamed a pipeline stage in the
  HubSpot UI. Update the `STAGE_*` constants to the current labels.
- **`INVALID_OPTION` on a deal write** — you're writing an `outflo_status`
  value that isn't an option on the enum yet. Read the property
  (`GET /crm/v3/properties/deals/outflo_status`), append the option, PATCH it
  back, then re-run. Never write first.
- **HTTP 401 from OutFlo** — key revoked/rotated; regenerate at
  reach.outflo.io/integrations.
- **Counts don't match the OutFlo dashboard** — expected; see the
  reconciliation explainer. Deals < leads because of the company merge, and
  the dashboard's cumulative counters include deleted campaigns.
- Transient 429/5xx/SSL errors are retried with backoff automatically; a
  crash mid-apply is safe to re-run (idempotency picks up where it left off).

## Wider context

- `docs/OPSDATA_PIPELINE.md` — the full pipeline & property contract (stages,
  dead-stage taxonomy, all custom properties, the Gmail email-campaign sync).
- `context.md` — the LH2 CRM operating rules this project inherits (outcome
  stages, no automated mail, phone gate, metric rules).
- `Company_Ops_SOP_flowchart_v3.png` — the flow the pipeline implements.
- Owner of everything in HubSpot: Kartik Pillai (owner id `96316911`, the
  only seat in this portal).
