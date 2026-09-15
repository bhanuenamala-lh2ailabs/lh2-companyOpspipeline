# -*- coding: utf-8 -*-
"""Note classification for the Company Ops dashboard — the single source of truth.

WHY NOTES AT ALL. Two dashboard metrics have no stage behind them, because neither is a
state the deal is IN:

  * "1st Interest Email Sent" — we mailed them something (deck, intro, calendly) while the
    deal legitimately stays wherever it is. There is no v3 stage for it and there should not
    be one: `docs/OPSDATA_PIPELINE.md` §"the outcome IS the stage" forbids a stage that means
    "we tried". So it is read off the note somebody wrote.
  * "One-Pager Received" — them confirming receipt / coming back on the one-pager. `One Pager
    Shared` is us sending; the reply is an event on the deal, not a new state.

ORDER MATTERS — first match wins, same as the sales-side note_rules.py. This module lives NEXT
TO build_ops_dashboard.py and inside the repo on purpose: the sales dashboard once imported its
rules from a local-only sibling directory, which resolved on the laptop and raised ImportError
in Actions, leaving the note KPIs silently empty on every deployed build.

These rules are IMPORTED, never copied. An earlier hand-copy of KPI definitions on the sales
side drifted and inflated dial counts 2.7x.

The regexes below were tuned against the ACTUAL note corpus of portal 246897735 — all 68 bodies
were read, and every one of them lands in a named bucket. Before that pass 39 of 68 fell into
"Other", almost all of them our own size/fit disqualifications ("2-10 employees, not interesting")
that no rule had ever been written for.

Re-run the check after any edit here:

    python dashboard/check_note_rules.py

It classifies every note in the portal and prints whatever still lands in "Other". A handful of
genuinely unclassifiable notes is fine; a cluster is a missing rule.
"""
import re, html

NOTE_RULES = [
    # Machine-written first so our own scripts' notes can never be counted as somebody's work.
    ("System note", r"^(\[outflo|\[opsdata|\[pipeline|imported from outflo|gmail import|"
                    r"auto-created|migration note)"),

    # --- the two that feed dashboard metrics ---
    # "Received" must be tested BEFORE "sent", or "sent the one pager, they received it"
    # lands in the wrong bucket.
    ("One pager received",
        r"((one[- ]?pager|onepager|1[- ]?pager|deck)[^.]{0,40}?"
        r"(received|acknowledg|confirmed receipt|went through|reviewed|got it|came back|"
        r"responded|replied)"
        r"|(received|got|reviewed|went through)[^.]{0,25}?(one[- ]?pager|onepager|deck))"),

    # WE sent them something. Tested before every disqualification bucket because losing a metric
    # event is worse than mis-filing a context note, and a real send is often written in the same
    # breath as the reason the lead later died.
    #
    # EVERY BRANCH NAMES WHAT WAS SENT, deliberately. Bare "sent"/"shared" would swallow the two
    # notes in this portal where the PROSPECT did the sending — "He has sent his email but is is
    # not a ceo or founder" and "He sent a contact number" — and count them as our outreach. The
    # object (deck, profile, details, an email of ours) is what makes it ours.
    ("Interest email sent",
        # the literal phrase, however somebody writes it
        r"((1st|first) interest[^.]{0,15}?(e-?mail|mail)"
        r"|interest (e-?mail|mail)[^.]{0,15}?(sent|shared|out)"
        # "we/I sent|shared ... <thing>"
        r"|\b(we|i)\b[^.]{0,25}?\b(sent|shared|mailed|dropped|fired)\b[^.]{0,45}?"
        r"(e-?mail|mail|deck|profile|details|collateral|material|one[- ]?pager|proposal|calendly)"
        # "sent/shared him|her|them ... <thing>"
        r"|\b(sent|shared|mailed|dropped|fired)\b\s+(him|her|them)\b[^.]{0,45}?"
        r"(e-?mail|mail|deck|profile|details|collateral|material|one[- ]?pager|proposal|calendly)"
        # "sent/shared <the|a|our> <thing> ..."
        r"|\b(sent|shared|mailed|dropped|fired)\b[^.]{0,40}?"
        r"\b(interest (e-?mail|mail)|intro (e-?mail|mail)|introduction (e-?mail|mail)|"
        r"company deck|deck|calendly|company profile|profile|proposal)"
        r"|shared (the )?(details|collateral|material)s? over (mail|e-?mail))"),

    # --- why a lead died: OUR call, not theirs ---
    # Tested before "Not interested" because these are different facts and the words are nearly
    # identical. "He is not interested" is THEM declining. "not interesting" is US disqualifying
    # them, almost always on company size — 20 of the 68 notes in this portal, and every one of
    # them landed in "Other" until this bucket existed.
    #
    # Wrong contact goes first: "asked to connect with digital marketing team, not interesting"
    # is about reaching the wrong person, which is the actionable half.
    ("Wrong contact", r"(not a (decision[- ]maker|potential lead|ceo|founder)"
                      r"|is ?n'?o?t a (ceo|founder|decision)"
                      r"|\b(is|was) an? (intern|student)\b"
                      r"|currently a student"
                      r"|(connect|reach out|speak|talk) (with|to) (the )?(company'?s? )?"
                      r"(it|hr|digital marketing|marketing|tech|product|data)\b"
                      r"|reach out to someone else"
                      r"|explore open roles|offered his services|offered her services)"),

    ("Disqualified — size/fit",
        r"(not interesting|does ?n'?o?t interests? us|not a quality (deal|lead)"
        r"|\d+\s*-\s*\d+\s*(employees|people|members)"
        r"|(very )?small company|less than \d+ (members|people|employees)"
        r"|company size is (very )?small|do not need)"),

    # --- context buckets: not metrics, but they explain a quiet week ---
    ("Not interested", r"(not interested|was ?n'?t interested|were ?n'?t interested|"
                       r"does ?n'?o?t seem interested|do ?n'?o?t seem interested|"
                       r"no bandwidth|no interest\b|not looking|declined|passed on)"),
    ("Privacy concern", r"(privacy|confidential|nda|legal (said|team)|data protection|"
                        r"can'?t share (the )?data|cannot share (the )?data|gdpr|dpa concern)"),
    ("No reply", r"(no (reply|response|resposne)|did ?n'?o?t? (reply|respond|revert)|"
                 r"went (quiet|silent|cold)|ghosted|radio silence|no revert|"
                 r"did not provide|not replying back)"),
    # The prospect handed over a contact detail. Kept AFTER "Interest email sent" so a note that
    # records both ("He was interested and sent his email immediately. We have sent him an email
    # with the company deck.") is still counted as our send.
    ("Prospect shared contact", r"((he|she|they) (has |have )?sent (his|her|their|a|an)\b"
                                r"|sent a contact number|shared (his|her|their) (e-?mail|number))"),
    ("Callback booked", r"(call ?back|asked to call|get(ting)? back|will revert|will lmk|"
                        r"in a meeting|busy|reschedul|connect back|check back)"),
    ("Meeting fixed", r"(call fixed|meeting fixed|gmeet fixed|"
                      r"discovery call (fixed|set|done|on |scheduled)|discovery call\.|"
                      r"scheduled for|slot(s)? (shared|confirmed)|invite sent)"),
    ("Sample discussed", r"(sample[^.]{0,30}?(requested|asked|promised|shared|sent|received)|"
                         r"(asked|requested)[^.]{0,20}?sample)"),
    ("Chase sent", r"(followed up|follow[- ]?up (sent|done)|sent (a )?(message|reminder|nudge)|"
                   r"whats ?app|\bwa\b|pinged|nudged|left a message)"),

    # --- v5 (2026-09-15) cold-call outcome buckets -------------------------------------
    # Appended LAST on purpose: lowest precedence means no existing bucket can regress.
    # These cover the call-log vocabulary that arrived with the cold-calling push, and
    # deals_v5_migrate.py maps them onto the v5 funnel stages (latest note wins there).
    ("Wrong number",
        r"(wrong number|invalid (no\b|no\.|number|contact)|"
        r"(number|no\.?|contact)[^.]{0,12}?(invalid|not valid|wrong)|"
        r"not a valid number|phone number invalid|"
        r"no\.? not present|no contact (number|no\.?)( present)?|"
        r"not found on linkedin)"),
    ("Company too small",
        r"(compan(y|ies)[^.]{0,20}?small|less headcount|size too small|"
        r"(team|headcount)[^.]{0,15}?(small|tiny)|\b2-10 employees)"),
    ("Pitching own services", r"(started pitching|pitching (his|her|their|its )?own|"
                              r"pitching (his|her|their) services)"),
    ("No pickup",
        r"(did ?n'?o?t pick|didn'?t pick|not picking|no pick ?up|no answer|\brnr\b|"
        r"ring(ing)? no (reply|response)|switched off|not reachable|unreachable|"
        r"out of coverage)"),
    ("Call done", r"^(1st |first )?call( done| attempted| made)?\s*([-.:]|$)"),
    ("WhatsApp outreach", r"^(wsp|whatsup|whats app)\b"),
    ("Already engaged", r"already (approached|in touch|contacted|connected)"),
    ("Invalid lead", r"^invalid( no\.?| number| lead)?\s*$"),
    ("No data held", r"(does ?n'?t have (any )?data|has no data|no data (to share|available))"),
    ("First email sent", r"\b(first|1st) e-?mail sent\b"),
]

# Buckets that merely restate a stage KPI are dropped by the builder so the same act is never
# counted once as a stage metric and again as note activity.
BUCKETS = [b for b, _ in NOTE_RULES] + ["Other"]

# The two buckets that ARE dashboard metrics. Named here rather than in the builder so the
# metric and the rule that produces it cannot drift apart.
METRIC_BUCKETS = {"Interest email sent": "interestSent",
                  "One pager received": "onePagerReceived"}


def plain(s):
    """HubSpot note bodies are HTML. Strip tags and collapse whitespace before matching."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def classify(t):
    s = (t or "").lower()
    for name, rx in NOTE_RULES:
        if re.search(rx, s):
            return name
    return "Other"