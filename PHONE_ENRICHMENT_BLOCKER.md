# Phone enrichment blocker — Apollo + SignalHire personal mobile reveal

## Goal
For the 303 India-based, tech-product-fintech companies in
`fintech_classification_filtered.csv`, get each company's best contact's **personal mobile
number**, validate it as a real +91-callable Indian number, and report how many are callable.

Algo to follow (per the shared validator convention used elsewhere in this repo,
`context.md` §8.1): Apollo primary, SignalHire fallback when Apollo has no match. Accept `+91`+10
digits or a bare 10-digit Indian number (first digit 6-9); reject any explicit non-`+91` country
code; **prefer mobile over landline** — a landline/switchboard number reaches a receptionist, not
the founder.

## What works today (no blocker)
- Apollo `people/match` (keyed by LinkedIn URL) — synchronous, returns the matched person plus
  their organization's basic info, **including the organization's main/switchboard phone number**,
  with no special flag needed.
- Ran this for all 303 companies: **303/303 matched a person**, **197/303 had an org phone on
  file**, **167/303 of those pass the +91-callable check**.
- This is a real, usable number — but it's the **company switchboard**, not the contact's personal
  mobile. Exactly the kind of number the algo says to deprioritize ("a landline reaches a
  switchboard, not the founder").

## The actual blocker
Getting the **person's personal mobile** — from either Apollo or SignalHire — requires an
asynchronous **webhook callback**, not a value returned in the API response body.

**Apollo** (`people/match` with `reveal_phone_number: true`):
- Calling it **without** a `webhook_url` → immediate `400 Bad Request`. The field is mandatory for
  phone reveal.
- Calling it **with** a `webhook_url` (tried a placeholder `https://example.com/hook`) → returns
  `202`-style response with `phone_enrichment: {status: "pending", request_id: "..."}` and says to
  poll `GET /api/v1/webhook_result/{request_id}`.
- Polling that endpoint with the returned `request_id` consistently returned
  `400 {"error_code": "invalid_request_id"}` — on multiple fresh attempts, with waits up to 60s.
  Either (a) the request_id needs to be echoed back by a live webhook_url before polling becomes
  valid, or (b) this Apollo API key's plan doesn't actually carry phone-reveal credits and the
  "pending" response is a generic placeholder that never resolves. Not conclusively distinguished
  between these two yet.

**SignalHire** (`candidate/search`):
- Tested directly with the key in `.env` (`signal_hire=...`). Immediate
  `406 {"error":"Callback url is required to get profiles"}` — this API is webhook-only, full
  stop. There is no synchronous "give me the result now" mode in their public API.

## Why I stopped rather than route around it
Both services want a **real, internet-reachable URL** to push the contact's name, LinkedIn
profile, and phone number to. I have no such endpoint running in this environment. The obvious
workaround — a free webhook-capture tool like `webhook.site` — would mean piping real people's
personal contact information through an unvetted third-party logging service (results are
publicly viewable at a guessable-ish URL, no data-handling agreement, no expiry guarantee I
control). I didn't want to do that without you explicitly signing off on it, given it's actual
people's phone numbers and LinkedIn identities, not test data.

## What's needed to unblock
Any ONE of these:

1. **A real webhook receiver you're fine with.** E.g., I stand up a small local HTTP server (I can
   write this in a few minutes) and you expose it publicly for the duration of the enrichment run
   via a tunnel tool (`ngrok`, `cloudflared`, etc.) that you control and can tear down after. Then
   Apollo/SignalHire push results there directly — no third party in between.
2. **Confirm the Apollo plan actually has phone-reveal credits.** If it doesn't, the `webhook_url`
   dance is moot for Apollo regardless — worth checking the Apollo dashboard/billing page for
   "mobile phone credits" or similar before building anything.
3. **Explicit go-ahead to use a third-party webhook-capture tool** (with an understanding of the
   exposure that implies), if standing up your own receiver isn't worth the effort for this batch.
4. **Accept the org-switchboard numbers as the deliverable** (167/303 already computed) and skip
   personal-mobile enrichment for this batch entirely.

## Current output files
- `fintech_classification_filtered.csv` — the 303-company input list (India, tech-product-led,
  reachable domain).
- `/private/tmp/claude-501/.../scratchpad/apollo_pass1.json` — full Apollo match results per
  company (person + org data), org phone included where Apollo has it on file.
- No final "phone-enriched" CSV has been produced yet — waiting on which path above to take.
