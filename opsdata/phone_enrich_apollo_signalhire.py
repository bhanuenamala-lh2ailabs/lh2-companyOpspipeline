"""Personal mobile enrichment: Apollo primary, SignalHire fallback.

Both services' public APIs default to an async, webhook-delivered flow for revealing a
person's personal phone number. Neither actually requires a reachable webhook receiver for
this use case — two request-shape details avoid it entirely:

  * Apollo `people/match` with `reveal_phone_number: true` requires a `webhook_url` field to
    be present, but never needs to successfully deliver to it. The real result is fetched by
    polling `GET /api/v1/webhook_result/{request_id}` — using the TOP-LEVEL `request_id` from
    the `people/match` response, not the one nested inside `phone_enrichment` (that one is
    invalid against the poll endpoint and returns `400 invalid_request_id` on every attempt).
    The poll response wraps its payload in `webhook_result`; `status` starts "pending" and
    resolves to "success"/"failed".

  * SignalHire `candidate/search` 406s with "Callback url is required to get profiles" unless
    the request body sets `"withoutWaterfall": true` (and omits `callbackUrl` entirely) — that
    flag alone switches it to a synchronous "check what SignalHire already has on file" lookup,
    returned directly in the response body under `candidate.contacts`. Trade-off: this mode
    only searches SignalHire's own existing database — it will not trigger their paid
    multi-provider waterfall search for someone they don't already have data on.

Validation: prefer a `mobile`-typed number over any other (a landline/switchboard number reaches
a receptionist, not the person), then require it to resolve to a real +91 Indian number — either
`+91` + 10 digits, or a bare 10-digit number starting 6-9. Any other explicit country code is
rejected outright, never re-stamped as +91.
"""
import json
import re
import time
import urllib.error
import urllib.request

ENV = dict(l.strip().split("=", 1) for l in open(".env") if "=" in l and not l.startswith("#"))
APOLLO_KEY = ENV["apollo_api_key"]
SIGNALHIRE_KEY = ENV["signal_hire"]

APOLLO_WEBHOOK_PLACEHOLDER = "https://example.com/apollo-noop"  # never delivered to — see docstring


def _apollo_call(url, method="GET", body=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-Api-Key": APOLLO_KEY},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except Exception:
        return None, {}


def apollo_mobile(linkedin_url, poll_attempts=4, poll_interval_s=4):
    """Returns a sanitized phone number string (e.g. "+919953487704"), or None."""
    status, resp = _apollo_call(
        "https://api.apollo.io/api/v1/people/match",
        "POST",
        {
            "linkedin_url": linkedin_url,
            "reveal_personal_emails": True,
            "reveal_phone_number": True,
            "webhook_url": APOLLO_WEBHOOK_PLACEHOLDER,
        },
    )
    if status != 200:
        return None
    request_id = resp.get("request_id")  # top-level — NOT resp["phone_enrichment"]["request_id"]
    if not request_id:
        return None

    for _ in range(poll_attempts):
        time.sleep(poll_interval_s)
        poll_status, poll_resp = _apollo_call(f"https://api.apollo.io/api/v1/webhook_result/{request_id}")
        if poll_status != 200:
            continue
        webhook_result = poll_resp.get("webhook_result") or {}
        if webhook_result.get("status") not in ("success", "failed"):
            continue  # still pending
        people = webhook_result.get("people") or []
        if not people:
            return None
        phones = people[0].get("phone_numbers") or []
        mobile = [p for p in phones if p.get("type_cd") == "mobile" and p.get("status_cd") == "valid_number"]
        best = mobile[0] if mobile else (phones[0] if phones else None)
        return best.get("sanitized_number") if best else None
    return None  # exhausted poll attempts while still pending


def signalhire_mobile(linkedin_url):
    """Returns a raw phone number string (e.g. "+91 99534 87704"), or None."""
    body = json.dumps({"items": [linkedin_url], "withoutWaterfall": True}).encode()
    req = urllib.request.Request(
        "https://www.signalhire.com/api/v1/candidate/search",
        data=body,
        headers={"Content-Type": "application/json", "apikey": SIGNALHIRE_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            results = json.loads(r.read())
    except Exception:
        return None
    for item in results if isinstance(results, list) else []:
        if item.get("status") != "success":
            continue
        contacts = (item.get("candidate") or {}).get("contacts") or []
        phones = [c for c in contacts if c.get("type") == "phone"]
        mobile = [c for c in phones if c.get("subType") == "mobile"]
        best = mobile[0] if mobile else (phones[0] if phones else None)
        return best.get("value") if best else None
    return None


def to_e164_india(raw):
    """Validate + normalize to +91XXXXXXXXXX. Returns None for anything else — never re-stamps
    a country code onto a number that carries a different one."""
    if not raw:
        return None
    s = re.sub(r"[\s\-()]", "", raw)
    if re.fullmatch(r"\+91\d{10}", s) and s[3] in "6789":
        return s
    if re.fullmatch(r"91\d{10}", s) and s[2] in "6789":
        return "+" + s
    if re.fullmatch(r"\d{10}", s) and s[0] in "6789":
        return "+91" + s
    return None


def enrich_mobile(linkedin_url):
    """Apollo primary, SignalHire fallback. Returns (e164_or_None, source_or_None)."""
    if not linkedin_url:
        return None, None
    raw = apollo_mobile(linkedin_url)
    source = "apollo" if raw else None
    if not raw:
        raw = signalhire_mobile(linkedin_url)
        source = "signalhire" if raw else None
    return to_e164_india(raw), source
