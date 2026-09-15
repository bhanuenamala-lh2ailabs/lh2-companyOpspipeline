# Company Ops Data — pipeline & property contract

**Portal:** `246897735` (build portal — **not** the main LH2 portal `246754894` that
`context.md` describes). Pipeline **Company Ops Data**, id `2464812771`.
Source of truth for the flow: **`lh2_deal_stage_flowchart_v3.svg`** (team-provided v4 SOP
diagram; despite the filename this supersedes `Company_Ops_SOP_flowchart_v3.png` — v1
`OPSDATA_SOP_GoogleDocs_flowchart1.png`, v2 `Company Ops_SOP_flowchart_v2.png` and v3
`Company_Ops_SOP_flowchart_v3.png` are history).
Synced by `opsdata/pipeline_sync.py` and `opsdata/properties_sync.py` (dry-run by
default, `--apply` to write, audit JSON lands in `audit/`).

**Never hardcode a stage ID.** Resolve from the deal's own `pipeline` value:

```python
s, d = hs("/crm/v3/pipelines/deals")
STAGE = {p["id"]: {st["label"]: st["id"] for st in p["stages"]} for p in d["results"]}
```

## v4 restructure (2026-09-08) — what changed and why

v1→v2 was applied by `pipeline_v2_update.py` + `deals_v2_migrate.py`, v2→v3 by
`pipeline_v3_update.py` (deal move in `deals_v3_migrate.py`, applied 2026-08-13 as part
of the OutFlo bucket fix). v3→v4 is applied by `pipeline_v4_update.py` +
`deals_v4_migrate.py`, following the team's new stage-flowchart diagram literally,
including its dead-stage label wording (a deliberate one-time exception to the
`Dead/<where>/<why>` slash convention documented below — the new diagram uses
`Dead: <where>` with the reason as a second line, and v4 keeps that wording rather than
translating it back to slashes).

- **New entry branch:** `Cold called assigned` sits alongside `LinkedIn sent` (renamed
  from `Cold LinkedIn Sent`) as a second way a deal enters the funnel. There is **no pull
  script for it yet** — it exists as a stage a human can move a hand-created deal into
  once cold-calling on this track starts. Wire up an importer before relying on it.
- **New interest stages:** `1st interest sent` (new) and `1st interest follow up`
  (renamed from `Ghost Follow-Up`, id preserved — 12 live deals) sit between `Replied`
  and `Discovery call`.
- **New loop stage:** `Call rescheduled` hangs off `Discovery call` for a booked meeting
  that needs rebooking. It is a lateral loop, not forward progress — same funnel depth as
  `Discovery call`.
- **Renamed, same id, deals kept in place:** `LinkedIn Connected` → `LinkedIn connected`,
  `Discovery Call` → `Discovery call`, `One Pager Requested` → `One pager requested`,
  `One Pager Follow-Up` → `One pager follow up`, `One Pager Shared` → `One pager
  received`, `Deal Contract Signed` → `Contract signed`.
- **`Contract signed` is now the pipeline's closed-won stage** (`isClosed: true`,
  probability 1.0), replacing `Closed/Won`. The new flowchart does not draw anything past
  contract signing, so there is no separate token-payment/migration/payment-initiation
  tail anymore — signing **is** winning the deal now.
- **Removed entirely** (stages deleted from the pipeline, not just hidden from the
  board): `LinkedIn Follow-Up` (0 deals — the new flowchart has no LinkedIn-branch chase
  stage before `Replied`), `Email Campaign Sent`, `Email Follow-Up` (the Gmail cold-email
  branch is retired — see below), `Internal Evaluation (Sample)`, `Samples Requested`,
  `Sample Follow-Up`, `Sample Received + Quality Check`, `Commercial Negotiations`, `Token
  Amount Paid`, `Data Migration Done`, `Payment Initiation`, `Closed/Won`. A stage cannot be
  deleted out from under live deals, so every one of these was confirmed at **0 deals**
  before removal **except**:
  - `Internal Evaluation (Sample)` (1), `Samples Requested` (1), `Sample Received +
    Quality Check` (2) — 4 deals total, migrated forward to the new `LOI signed` stage by
    `deals_v4_migrate.py` before the pipeline restructure ran.
  - `Email Campaign Sent` (**124 deals**) — **closed out, not revived.** The new
    flowchart has no email branch and therefore no natural *active* target stage for a
    deal that was reached by cold email and never replied; folding them into `LinkedIn
    sent` would have misrepresented 124 email-sourced deals as live LinkedIn outreach.
    Instead they were moved to the new dead stage `Dead: Email Campaign / Branch
    Retired` by `deals_v4_migrate.py --close-email` (sign-off given 2026-09-08). The
    `lead_source` property (`Cold Email ( Company Ops )`) is untouched, so the original
    channel is still visible on each deal even though the stage no longer reflects it.
    Audit trail: `audit/deals_v4_migrate_20260908_161308.{json,csv}` — the CSV lists
    every deal name, id, and its from/to stage for anyone who wants to review the list.
- **New stage `LOI signed`** replaces the old sample-evaluation-and-negotiation stretch
  (`Internal Evaluation (Sample)` → `Commercial Negotiations`) with one stage, sitting
  between `One pager received` and `Contract signed`.
- **Dead-stage renames** (id preserved, matched by pre-v4 meaning):
  `Dead/Cold/Not Interested` (76 deals) → `Dead: Replied / Not Interested`,
  `Dead/Interested/No Show` (3 deals) → `Dead: Discovery Call / No Show`,
  `Dead/One Pager Not Shared` (3 deals) → `Dead: One Pager / Not Received`.
- **Dead stages deleted** (0 deals each, no equivalent kept): `Dead/Cold/No Reply`,
  `Dead/Discovery Call/Privacy Concerns`, `Dead/Sample Not Collected/Wrong Fit-Rejected`,
  `Dead/Sample Not Received/Company No Show`, `Dead/Sample/Bad Quality`,
  `Dead/Negotiations/Pricing`, `Dead/Negotiations/Contractual`, `Dead/Migration/Failed`.
  Note: v4 has no "no reply ever" dead outcome for the *live* branches and no "privacy
  concerns" dead outcome — a deal that never gets a reply just sits at `LinkedIn
  sent`/`Cold called assigned` indefinitely, per the new diagram.
- **New dead stages:** `Dead: 1st Interest` (generic, no sub-reason — matches the diagram,
  which draws this box with no second line), `Dead: Discovery Call / Rejected by LH2`,
  `Dead: Discovery Call / Not Interested`, `Dead: One Pager / Less Data`, `Dead: LOI /
  Terms Not Agreed`, and `Dead: Email Campaign / Branch Retired` (not in the team's
  diagram — added specifically to close out the retired email branch's 124 deals without
  either orphaning them in a dead stage or misrepresenting them as live LinkedIn
  outreach; see above).

## Design decisions (deliberate — don't "fix" these)

- **The outcome IS the stage.** `Begin Here` and `Profile PreScreen` existed as
  stages from an earlier build and were removed (0 deals held them). The start
  marker and the decision diamond are not states; a stage that means "we are
  judging" is the same mistake as the retired `Call Attempted`.
- **Two entry branches, no pre-contact stage.** A lead exists in the CRM only once
  outreach is out (`LinkedIn sent`) or a cold call is assigned (`Cold called assigned`).
- **Renaming a stage: use per-stage PATCH, never the pipeline PUT.** The pipeline PUT
  matches stages **by label** — a rename via PUT silently becomes delete + create with a
  NEW stage id (observed in the v2 restructure: `GMeet Fixed` → `Discovery Call` got a
  fresh id). Harmless only when the renamed stage holds zero deals. A stage that holds
  deals must be renamed with `PATCH /crm/v3/pipelines/deals/{pipelineId}/stages/{stageId}`
  first, and only stages confirmed empty may be silently dropped by the PUT.
  Reordering, however, must still be one atomic PUT (sequential PATCHes re-sequence
  wrongly — main-portal lesson).
- **Removing a stage requires migrating its deals out FIRST.** Unlike a rename, deletion
  removes the stage id from existence — any deal still pointing at it when the PUT runs
  is either rejected by the API or left dangling. `deals_v4_migrate.py` always runs
  before `pipeline_v4_update.py --apply`, and the latter re-checks live deal counts on
  every doomed stage and aborts if any are still nonzero.
- **Price lives in `cost`** ("Deal Cost (USD)") — carried over from v3; this flow never
  writes `amount`. `cost` is now written at `Contract signed` (there is no separate
  Payment Initiation gate anymore).
- **Closed/Won emails (client + internal) are manual.** Hard rule: no automated
  outbound mail except the sanctioned 18:30 IST daily report.
- **Metrics are sets of stages, computed from stage history**
  (`propertiesWithHistory=dealstage`), never counters. There is no `sourceType` filter —
  see `docs/OPS_DASHBOARD_METRIC_SPEC.md`.

## Live stages (v4, applied 2026-09-08)

| # | Stage | Prob | Meaning (state, not activity) | Gate — set when entering |
|---|---|---|---|---|
| 0 | LinkedIn sent | 0.03 | LinkedIn-branch entry: connect request / msg 1 out (renamed from `Cold LinkedIn Sent`, id kept) | `li_msg1_date` |
| 1 | Cold called assigned | 0.03 | Cold-call-branch entry (new — no importer yet, hand-assigned only) | `cold_call_assigned_date` |
| 2 | LinkedIn connected | 0.05 | They accepted the connect (renamed from `LinkedIn Connected`, id kept) | — |
| 3 | Replied | 0.12 | They answered (either branch) | `replied_at` |
| 4 | 1st interest sent | 0.14 | We sent our pitch/interest after the reply (new) | `interest1_sent_date` |
| 5 | 1st interest follow up | 0.16 | Agreed, then went quiet; follow-up out (renamed from `Ghost Follow-Up`, id kept) | — |
| 6 | Discovery call | 0.25 | Meeting booked. **Handover: analyst → Pod Lead** (renamed from `Discovery Call`, id kept) | `gmeet1_link`, `gmeet1_date` |
| 7 | Call rescheduled | 0.25 | Booked meeting needs rebooking — loops back onto Discovery call (new) | `call_rescheduled_date` |
| 8 | One pager requested | 0.30 | They asked for the one-pager (renamed from `One Pager Requested`, id kept) | — |
| 9 | One pager follow up | 0.30 | One-pager promised, chasing (renamed from `One Pager Follow-Up`, id kept) | — |
| 10 | One pager received | 0.35 | Collateral sent/received (renamed from `One Pager Shared`, id kept) | `one_pager_sent_date`, `gmeet1_outcome` |
| 11 | LOI signed | 0.55 | Letter of intent signed — replaces the old sample-evaluation + negotiation stretch (new) | `loi_signed_date` |
| 12 | Contract signed | 1.0 | Signed — **this is now the closed-won stage** (renamed from `Deal Contract Signed`, id kept) | `data_delivery_timeline`, `cost` |

Roles by LH2 names: **Lead Manager** = Pod Lead, **Lead Closer** = Pod Head.
Never put an individual's name in this document — people rotate.

## Dead stages — v4, literal flowchart wording (not the slash convention)

| Stage | Use when |
|---|---|
| Dead: Replied / Not Interested | Replied, said no (renamed from `Dead/Cold/Not Interested`, id kept — 76 deals) |
| Dead: 1st Interest | No reply after the 1st-interest follow-up (new; no sub-reason, matches the diagram) |
| Dead: Discovery Call / No Show | Meeting booked, they never showed (renamed from `Dead/Interested/No Show`, id kept — 3 deals) |
| Dead: Discovery Call / Rejected by LH2 | We decided against them after the call (new) |
| Dead: Discovery Call / Not Interested | Call happened, they declined (new) |
| Dead: One Pager / Not Received | One-pager promised, chased, never arrived (renamed from `Dead/One Pager Not Shared`, id kept — 3 deals) |
| Dead: One Pager / Less Data | One-pager received, but too little data on offer (new) |
| Dead: LOI / Terms Not Agreed | LOI stage reached, terms didn't land (new) |
| Dead: Email Campaign / Branch Retired | Not a real outcome of this funnel — the deal was sitting at `Email Campaign Sent` when the email branch was retired and had never replied. Not in the team's diagram; added to close out those 124 deals (2026-09-08) — see "v4 restructure" above |

v4 has **no "no reply ever" dead outcome** (a cold deal just sits at `LinkedIn
sent`/`Cold called assigned` indefinitely) and **no "privacy concerns" dead outcome** —
both existed in v3 and were dropped along with the stages/deals they were attached to
(all at 0 deals). If either recurs in practice, it needs a new stage added deliberately,
not folded into an existing one.

Per-stage survival is no longer computable purely from string-splitting the dead-stage
label (the `Dead/<where>/<why>` slash convention that made this trivial is gone in v4 by
team decision) — `build_ops_dashboard.py`'s `_DEAD_SEQ` dict now carries that mapping
explicitly instead of deriving it, so the funnel-depth ordering still works but is one
more thing to maintain by hand when a new dead stage is added.

## Property contract (group `opsdata_procurement`)

Greenfield properties get their meaning here; if you change a meaning, change it
here first.

| Property | Type | Definition |
|---|---|---|
| `li_msg1_date` | date | Date LinkedIn msg 1 sent (LinkedIn branch) |
| `cold_call_assigned_date` | date | Date a deal was assigned to the cold-call branch (v4, new) |
| `replied_at` | datetime | First substantive reply from the lead |
| `interest1_sent_date` | date | Date our 1st-interest pitch was sent, post-reply (v4, new) |
| `gmeet1_date` / `gmeet1_link` | datetime / text | First meeting slot + link |
| `gmeet1_outcome` | enum: Proceeds, Wrong Fit, No Show, Privacy Concerns | Maps 1:1 to the post-GMeet branches |
| `call_rescheduled_date` | date | Date a booked meeting was most recently rescheduled (v4, new) |
| `one_pager_sent_date` | date | Date one-pager shared/received |
| `loi_signed_date` | date | Date the letter of intent was signed (v4, new) |
| `sample_pii_flags` | multi-checkbox | Legacy from the v3 sample-evaluation stretch — kept on existing deals, no longer written going forward |
| `token_paid_date`, `migration_volume`, `migration_record_count`, `number_of_datasets`, `dataset_customization_required` | | Legacy from the removed v3 post-signing tail — kept on existing deals (properties are not deleted, only stages are), no longer written going forward |
| `data_delivery_timeline` | date | Delivery deadline agreed at contract signing |
| `cost` | number | Final deal cost in USD — now written at `Contract signed` |

Enum discipline: writing a value that is not an option fails with
`INVALID_OPTION`. Read the property, PATCH the new option in, then write.

## OutFlo import (opsdata/outflo_pull_push.py)

Leads come from **every ACTIVE OutFlo campaign whose name contains "company
ops"** (`live.outflo.in` API, `x-api-key` auth; key `OUTFLO_API_KEY` in `.env`).
Campaigns rotate — Pilot became Company Ops_India, then geo campaigns
(UAE/Singapore), then Company Ops_India _All Industries; inactive campaigns
drop out of sync scope, so stage fixes for their already-imported deals need
one-off migrations.

Bucket mapping: OutFlo **Request Sent** ("leads processed") → `LinkedIn sent`;
**Connected** → `LinkedIn connected`; **Replied** → `Replied`. Stage climbs that ladder
only, never down. "Checking" / "Failed" leads stay in OutFlo.

- One **deal per company** (deal name = company), one contact per person,
  associated; multi-founder companies share one deal.
- All deals + contacts owned by **Kartik Pillai** (owner id `96316911`, the
  only seat in this portal). The actual OutFlo sender is kept in
  `outflo_assigned_account` (Anu Meena / Kartik Pillai) so deals can be
  re-split if Anu gets a seat.
- Provenance frozen at import: `lead_source` = `Outflo Outreach ( Startups )`,
  `outflo_status` (Request Sent/Connected/Replied), `outflo_campaign`,
  `outflo_lead_id`, `outflo_last_action_at`.
- `replied_at` = timestamp of the last conversation message *when the lead sent
  it*, else OutFlo's Last Action At (approximation, documented).
- Idempotent: matched by `outflo_lead_id` then normalized deal name; stage only
  ever climbs `LinkedIn sent` → `LinkedIn connected` → `Replied`; deals a
  human moved anywhere else are never touched. Re-run any time.
- No phones are pushed: OutFlo carries none, and unverified numbers are banned.

## Gmail cold-email import (opsdata/gmail_pull_push.py) — RETIRED in v4

Kartik's mailbox (kartik.pillai@lh2.ai) ran a cold-email campaign; OAuth was read-only
(`gmail.readonly`, token in `.gmail_token.json`, consent via `opsdata/gmail_auth.py`). **A
campaign mail was any SENT message whose body contained a Calendly link.**

The team's v4 flowchart drops the email branch entirely — `LinkedIn sent`/`Cold called
assigned` are the only two entry points now. `gmail_pull_push.py` and `gmail_auth.py`
remain in the repo for reference but **must not be run going forward**: their target
stages (`Email Campaign Sent`, `Email Follow-Up`) are deleted from the live pipeline
(confirmed gone 2026-09-08), so a run now would fail with `INVALID_OPTION` on every
write — it's also gated behind `--i-know-this-is-retired` for exactly this reason. The
124 deals these scripts created were closed to `Dead: Email Campaign / Branch Retired`
by `deals_v4_migrate.py --close-email`; see "v4 restructure" above.

## Known gaps / notes

- **Deal merge produces a NEW object id.** `POST /crm/v3/objects/deals/merge`
  absorbs both records into a fresh id; the old primary id soft-redirects on
  v3 `GET`/`PATCH` but NOT on every v4 endpoint (association listing on the
  old id silently returns empty). After a merge, re-resolve the deal by search
  before reading associations. (Learned merging the DoubleTick /
  DoubleTick.io duplicate — same company, two founders, one deal.)

- `lead_source` in this portal is a plain **text** property, not the 5-option
  enum the main portal uses. Convert before any cross-portal reporting.
- Portal timezone is **US/Eastern**; LH2 reporting days are IST with an
  18:30 IST cutoff — compute day boundaries in code, don't trust portal-local dates.
- Any lead-loading script must gate phones through the shared Indian-number
  validator (`indian_number.py` in the main repo): no Indian mobile, no push.
- **`Cold called assigned` has no importer.** Until one exists, deals only reach it by
  hand. Building that importer (and deciding what "cold called" means operationally —
  who assigns it, from what source) is separate follow-up work, not part of this
  restructure.

## v5 restructure (2026-09-15) — Ops-Data Supply Funnel

Source of truth: the **Ops-Data Supply Funnel PDF** (cold-calling becomes the
ONLY outreach method; one funnel entry). Applied by `pipeline_v5_funnel.py` +
`deals_v5_migrate.py` in the v4 three-step (shape → migrate → shape drops the
emptied stages).

- **New live:** `No pickup`, `Callback +1 day` (call-outcome loop) and the
  restored closing tail `Ops data handover done` → `Payment initiation` →
  `Closed/Won`. `Contract signed` is a live stage again, NOT closed-won.
- **New dead:** `Dead: Cold Call / Wrong Fit | Wrong Number | Not Interested |
  No Pickup`, `Dead: 1st Interest / Not Interested`, `Dead: LOI / Pricing Not
  Agreed`.
- **Renamed (PATCH, ids kept):** `Dead: 1st Interest` → `… / No Response`,
  `Dead: One Pager / Less Data` → `… / Low Data Quality`,
  `Dead: LOI / Terms Not Agreed` → `… / Contractual Not Agreed`.
- **Deleted:** `LinkedIn sent` (id 4080987861), `LinkedIn connected`
  (id 4132224744) — after their 3,007 deals moved. Those ids live on in deal
  history; the dashboard resolves them via `DELETED_STAGE_LABEL` in
  `build_ops_dashboard.py`.
- **Kept off-spec:** `Dead: Email Campaign / Branch Retired` (118 deals +
  history from the v4 email-branch retirement; re-parking dead deals is a
  business call, not a migration).
- **Migration was note-driven:** deals in the three pre-outcome stages were
  placed by their LATEST classifiable note (rules: `dashboard/ops_note_rules.py`,
  extended with the cold-call vocabulary — wrong number, no pickup, callback,
  company-too-small…), blanket `Cold called assigned` otherwise. Owners were
  never written. Full per-deal audit: `audit/deals_v5_migrate_*.{json,csv}`.
- **Importer:** OutFlo request-sent AND connected both enter at
  `Cold called assigned`; ladder is `Cold called assigned → Replied` only.

## LinkedIn branch restored (2026-09-15, after v5)

v5's cold-call-only redesign deleted `LinkedIn sent` / `LinkedIn connected`
after moving their deals to `Cold called assigned`. Restored the same day at
the user's request: `opsdata/restore_linkedin_branch.py` recreated both
stages (new stage ids — HubSpot never reuses a deleted stage's id) in their
original v4 position (`LinkedIn sent → Cold called assigned →
LinkedIn connected → Replied`), then moved deals back **only where safe**:
of the 3,007 deals the v5 migration had moved, 2,999 were still sitting
untouched at `Cold called assigned` and went back to their original stage;
8 had since progressed to `Replied` and were left exactly there — restoring
a stage is not a reason to erase real work done after it was removed.

Live pipeline is now 33 stages: the full v5 funnel, unchanged, plus the two
restored LinkedIn stages as a second entry branch alongside cold-calling.
`dashboard/build_ops_dashboard.py`'s `ENTRY_IDS` now includes both entry
points; its `DELETED_STAGE_LABEL` mapping is kept (not removed) because the
*old*, pre-restoration stage ids are still referenced in deal history from
the window the branch was actually gone, and a deleted stage id can never
reappear in a live pipeline read.
