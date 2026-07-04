# ProvenanceGuard — Change & Decision Log

This document records every deliberate change made to the ProvenanceGuard pipeline
and the reasoning behind it. It exists so design choices can be traced and cited
(e.g. from the README) rather than living only in commit history or memory. Entries
are appended newest-first as work continues.

---

## Remaining pre-stretch work (checklist)

Ordered by dependency — each item unblocks the next. **Already done:** the Flask
rate limiter (`Flask-Limiter` on `/submit`) and the core detection pipeline (3
signals + confidence scorer). Ordering rationale: the audit log records what the
other features produce, so it comes *after* validation and labels; but `/appeal` and
`/flagged` *consume* the audit log, so they come *after* it. Chain:
validate → labels → **audit log** → appeal → flagged.

- [x] **1. Wire `validate_input()` into `/submit`.** ✅ Done 2026-07-03 — see
  changelog entry below. The validator + injection defense now runs as the pipeline's
  entry gate.
- [x] **2. `label_generator()` + transparency label texts.** ✅ Done 2026-07-03 —
  see changelog entry below. Pure presentation function mapping attribution →
  `{label_text, appeal_available}`; wired into the `/submit` response.
- [x] **3. Complete the audit log.** ✅ Done 2026-07-03 — see changelog entry below.
  Schema expanded (5 new columns), DB recreated fresh, submit-time flags wired
  (injection + disagreement), injection rejections logged. **Deferred follow-up:** the
  rate-limit-violation flag (Planning's 4th flag condition) needs a 429 handler and
  was intentionally left for later.
- [x] **4. Real `POST /appeal`.** ✅ Done 2026-07-03 — see changelog entry below.
  Built on the stub's `/appeal` + body shape; lookup, 404/403/409/400 handling, appeal
  recording, and the `high_confidence_ai_with_appeal` flag all wired.
- [x] **5. `GET /flagged`.** ✅ Done 2026-07-03 — see changelog entry below. Read-only
  human-review queue returning `flagged=1` rows, newest first.

**All pre-stretch work is complete.** The rate-limit 429 JSON handler is now in place
too (see changelog). Remaining deferred follow-up: the *DB-write* half of Planning's 4th
flag condition — writing a flagged audit row on a rate-limit violation — is intentionally
held off for now.

Stretch features (provenance certificate, analytics dashboard, multi-modal support)
are **out of scope until every item above is complete** — listed for reference only.

---

## How scoring & labeling works (reference)

The single most important concept: the system produces **two separate measurements**
that answer **different questions**. Confusing them is the usual source of "wait, why
is a high score only medium confidence?"

| Measurement | Question it answers | Range |
|---|---|---|
| **Score** | How AI-like does the writing *look*? | 0.0 (human) → 1.0 (AI) |
| **Confidence** | How much do our detectors *agree* with each other? | high / medium / low |

**Confidence is not derived from the score.** It measures agreement between detectors,
not how big the score is.

### 1. The score (how AI-like)
Three signals each give an independent 0.0–1.0 AI-likelihood; the score is their
weighted average:
- **Groq (LLM)** — reads tone/phrasing/"vibe". Weight **0.45**.
- **Stylometrics** — sentence-length variety, vocabulary richness, punctuation. Weight **0.35**.
- **Burstiness** — whether sentence lengths cluster in human-like bursts. Weight **0.20**
  (abstains on short text; remaining weights renormalize).

### 2. The confidence (how much detectors agree)
Confidence comes from the **spread** = (highest signal − lowest signal):

| Spread (gap between detectors) | Confidence |
|---|---|
| ≤ 0.30 | high |
| ≤ 0.45 | medium |
| > 0.45 | low |

Small gap → detectors agree → high confidence. Big gap → they disagree → low confidence.

### 3. Why a high score can still be "medium"
Example: Groq 0.90, stylometrics 0.75, burstiness 0.55 → average ≈ **0.78** (a strong
score), but the gap between top (0.90) and bottom (0.55) is **0.35**, which lands in the
*medium* band. So: **high score, medium confidence** — because the detectors didn't fully
line up. "High/medium/low" describes the *disagreement*, never the score's height.

### 4. The final verdict needs BOTH a strong score AND agreement
| Verdict | Requires |
|---|---|
| **AI-generated** | score ≥ 0.75 **AND** high confidence |
| **Human-written** | score ≤ 0.30 **AND** high confidence |
| **Uncertain** | everything else (mid score, or detectors disagree) |

So the 0.78-but-medium example above → **Uncertain**, not AI-generated: the score cleared
0.75 but the detectors weren't in enough agreement.

**Why this asymmetry (harder to call AI than human):** falsely accusing a human of using
AI is worse than missing some AI. The "AI-generated" label is only applied when the score
is strong **and** the detectors agree; anything doubtful becomes "Uncertain," which always
offers an appeal. Innocent until *strongly and consistently* shown otherwise.

### 5. From verdict to what the user sees
`label_generator` maps the verdict to (a) the plain-language label text and (b) whether an
appeal is offered (yes for AI-generated & Uncertain, no for a favorable Human-written).

**Design consequence used elsewhere:** "the verdict was AI-generated" is a *stronger*
statement than "the score was above 0.75," because the verdict also required high
agreement. That's why the appeal-time `high_confidence_ai_with_appeal` flag keys off the
**verdict** (`attribution == "AI-generated"`), not a raw score threshold — a 0.78-but-
medium "Uncertain" case was never confidently called AI, so flagging it as "high
confidence AI" would misrepresent what happened.

---

## How to test & verify (reference)

There are two layers of tests, and they cover different things:

| File | What it tests | Needs Groq API? |
|---|---|---|
| `test_groq_signal.py` | **Signal 1 alone** — human vs ChatGPT text scoring (Milestone 3 evidence). | Yes |
| `test_signals.py` | **All 3 signals + the scorer's threshold logic.** Part A/A2 run real inputs; **Part B** is pure-logic forced-score unit tests — the proof the scoring produces meaningful variation, not a constant (Milestone 4 evidence). | Parts A/A2 yes; Part B no |
| `verify.py` | **The HTTP endpoints** — `/submit`, `/appeal`, `/log`, `/flagged`, and every status code (400/403/404/409/429). Uses Flask's test client against a **temp DB** (real `audit_log.db` untouched) and forces verdicts for deterministic appeal/flag paths. | Mostly no (verdicts forced) |

`verify.py` is the plumbing layer; the two `test_*` files are the signal/scoring math.
They're complementary — neither replaces the other.

### Run the automated tests
```powershell
.\.venv\Scripts\python.exe test_groq_signal.py     # Signal 1 in isolation
.\.venv\Scripts\python.exe test_signals.py         # signals + scorer (look for [PASS] in Part B)
.\.venv\Scripts\python.exe verify.py               # all endpoints (prints "N passed, 0 failed")
```

### Manually poke the live server
Start it in one window, hit it from another:
```powershell
.\.venv\Scripts\python.exe app.py                  # serves http://127.0.0.1:5000

# in a second window:
$body = @{ text = "a passage of at least fifteen words so it clears the length gate cleanly here"; creator_id = "me" } | ConvertTo-Json
$r = Invoke-RestMethod http://localhost:5000/submit -Method Post -ContentType "application/json" -Body $body
$r | ConvertTo-Json -Depth 5
Invoke-RestMethod http://localhost:5000/log     | ConvertTo-Json -Depth 5
Invoke-RestMethod http://localhost:5000/flagged | ConvertTo-Json -Depth 5
$a = @{ content_id = $r.content_id; creator_reasoning = "my own work" } | ConvertTo-Json
Invoke-RestMethod http://localhost:5000/appeal -Method Post -ContentType "application/json" -Body $a
```
Gotcha: `Invoke-RestMethod` throws a red error on any 4xx — that error *is* the expected
result for the rejection cases. Wrap it in try/catch to read the JSON body, or just trust
the status code. `verify.py` sidesteps all of this.

### Reset the audit DB for clean grading screenshots
```powershell
Remove-Item audit_log.db        # regenerates empty on next app start
```

---

## 2026-07-03 — Endpoint test harness `verify.py` (+ removed throwaway test)

### Summary
Added `verify.py`, an endpoint-level integration test (Option B: kept the milestone
signal/scorer tests, added a separate endpoints harness). Removed the throwaway
`test_labeled_examples.py`.

### Why this split
`test_signals.py` / `test_groq_signal.py` test the **signal & scoring math** (and are the
Milestone 3/4 evidence). Nothing tested the **HTTP layer**. `verify.py` fills that gap
without duplicating the math tests. See the "How to test & verify" reference section above.

### What `verify.py` does
- Points `app.DB_PATH` at a temp file and re-inits, so the real `audit_log.db` is never
  written to.
- Uses Flask's test client; forces verdicts (patches the signals) where a deterministic
  outcome is needed, so appeal/flag paths don't depend on the live LLM.
- Checks 19 assertions across: validation gate (400s), injection logging, classify +
  label + `appeal_available`, the full appeal flow (200/409/403/404/400 + the
  `high_confidence_ai_with_appeal` flag), `/log` + `/flagged`, and rate limiting (429).
  The rate-limit check runs last because it deliberately exhausts the 10/min limit.

### Verification
`\.venv\Scripts\python.exe verify.py` → **19 passed, 0 failed**.

---

## 2026-07-03 — Rate-limit 429 JSON handler (+ README reference file)

### Summary
Added a Flask `@app.errorhandler(429)` so a rate-limit hit returns clean JSON instead of
Flask-Limiter's default HTML error page. Also created `ReadMeRef.md`, a reference/answer
key for writing the README. **Per the user's instruction, the database half was held off:**
this handler only shapes the HTTP response — it does NOT write a flagged audit row.

### What changed in `app.py`
- New `ratelimit_handler(exc)` registered on `@app.errorhandler(429)`, returning
  `{"error": "...", "limit": "<the limit string>"}` with status `429`.

### Deferred (unchanged)
Planning's 4th monitoring condition ("rate-limit violation → flagged") has two halves: the
HTTP response (done here) and the audit-log write (still deferred, at the user's request).

### Verification (Flask test client)
- Sent 13 rapid `/submit` calls with too-short bodies (they fail validation before Groq
  and before any DB write, but still count against the limiter): first 10 → `400`, then
  `429` for the rest, with body
  `{"error": "Rate limit exceeded. Please slow down and try again shortly.", "limit": "10 per 1 minute"}`.

### ReadMeRef.md
Created a two-part reference: Part 1 is a full draft of every required README section
(architecture, signals, confidence scoring with two real high/low-confidence examples,
the three label texts, rate limiting, known limitations, spec reflection, AI usage), all
using real Milestone-4 numbers; Part 2 is a fill-in-your-own-voice skeleton. Not for
submission — a study/reference aid.

---

## 2026-07-03 — `GET /flagged` review queue (checklist item 5)

### Summary
Added the human-review queue: a read-only `GET /flagged` endpoint returning every audit
row with `flagged=1`, newest first. This makes all the flagging logic from Steps 3–4
actually visible in one place. Completes the pre-stretch work.

### What changed in `app.py`
- New helper `get_flagged(limit=50)` — mirrors `get_log` but filters `WHERE flagged = 1`,
  ordered `id DESC`.
- New route `GET /flagged` returning `{"entries": [...]}`. Same no-auth caveat as `/log`
  (a real system would gate both behind an admin role).

### No open design decisions
The four flag conditions were already decided and implemented in Steps 3–4; this endpoint
only surfaces them. (The 4th Planning flag condition — rate-limit-violation — remains a
documented deferred follow-up.)

### Verification (Flask test client)
- `/flagged` count equals the number of `flagged=1` rows in `/log`; every returned entry
  has `flagged=1`; ordering is newest-first (id descending).
- A freshly-submitted clean **human** verdict does **not** appear in `/flagged`.
- The queue surfaces all four flag types together: `high_severity_injection` (rejected
  entries), `low_severity_injection`, `signal_disagreement (spread=…)`, and
  `high_confidence_ai_with_appeal` — including a combined
  `low_severity_injection: 'act as'; high_confidence_ai_with_appeal` row.

---

## 2026-07-03 — Real `POST /appeal` endpoint (checklist item 4)

### Summary
Replaced the `/appeal` stub (which echoed a canned "under_review" with no side effects)
with the full Flow-2 appeal process: look up the submission, decide if it's appealable,
record the appeal in the audit log, and flag contested AI verdicts for human review.

### Decisions (all confirmed by the user before coding)
1. **Route stays the stub's shape** — `POST /appeal` with `{content_id, creator_reasoning}`
   in the JSON body. No `/appeal/<content_id>` path param (the user's project prefers not
   to add that). Reading `content_id` from the body is not a new parameter — the stub
   already named it; the appeal simply has to identify which submission it contests.
2. **Appeal flag keys off the verdict, not a raw score.** The
   `high_confidence_ai_with_appeal` flag fires when `attribution == "AI-generated"`
   (which already means strong score AND high agreement), aligning with "our AI label"
   rather than a bare `score > 0.75/0.80`. See the "How scoring & labeling works"
   reference section for why the verdict is the honest signal.
3. **Human-written verdicts are declined tersely.** A human verdict never surfaces an
   appeal option (`appeal_available=False`), so an appeal against one only arrives
   out-of-band. We return `403 {"error": "No appeal is available for this submission."}`
   — no explanation of the appeal process and no "you were cleared as human" messaging,
   since the creator did nothing to provoke it.

### What changed in `app.py`
- Two new audit-log helpers next to the others: `get_entry(content_id)` (single-row
  lookup → dict or None) and `update_appeal(content_id, appeal_status, appeal_reason,
  flagged, flag_reason)` (UPDATE of an existing row — the log previously only INSERTed).
- Rewrote `appeal()`:
  1. `400` if the body lacks `content_id`, or if `creator_reasoning` is empty.
  2. `get_entry` lookup; `404` "Submission not found." if missing **or** the row isn't a
     real classification (`status != "classified"` — injection-rejection rows aren't
     appealable and their ids are never handed to users).
  3. `403` terse decline if the original verdict was `Human-written`.
  4. `409` "Appeal already submitted." if `appeal_status` is already set.
  5. Otherwise: flag check — if `attribution == "AI-generated"`, set `flagged=1` and
     **append** `high_confidence_ai_with_appeal` to any existing `flag_reason` (joined
     with `; `, preserving prior flags); non-AI verdicts keep their flags unchanged.
  6. `update_appeal(...)` writes `appeal_status="under_review"` + the reasoning, then
     responds `200` with `{content_id, appeal_status, message}`.

### Verification (Flask test client, verdicts forced by patching the signals)
- **AI verdict → appeal** → `200`, row becomes `appeal_status=under_review`,
  `flagged=1`, `flag_reason=high_confidence_ai_with_appeal`, reasoning stored.
- **Re-appeal** the same id → `409`.
- **Human verdict → appeal** → `403` "No appeal is available for this submission."
- **Uncertain verdict → appeal** → `200`, `flagged` stays `0` (no high-confidence flag).
- **Unknown id** → `404`. **Missing reasoning** → `400`.
- **Flag preservation:** an AI entry already flagged `low_severity_injection: 'act as'`
  at submit → after appeal becomes
  `low_severity_injection: 'act as'; high_confidence_ai_with_appeal` (appended, not
  overwritten).

### Note
`audit_log.db` accumulated more synthetic verification rows (forced-verdict submissions).
Harmless; delete the file to reset — it regenerates empty on next start.

---

## 2026-07-03 — Completed the audit log (checklist item 3)

### Summary
Expanded the audit log from the minimal M3 table to the full Section-7 schema, wired
submit-time monitoring flags, and started logging rejected injection attempts. The DB
was recreated fresh (per the decision below) so it carries no vestige of the older
schema.

### Decisions (all confirmed by the user before coding)
1. **Disagreement flag fires on LOW confidence** (spread > 0.45), not Planning.md's
   literal `spread > 0.40`. Rationale: keep one threshold — the flag means exactly what
   our recalibrated scorer already calls "genuine disagreement," so `flagged` and LOW
   confidence stay aligned.
2. **High-severity injection attempts are logged** as a flagged, rejected entry before
   the 400 response, so security events surface in the future `/flagged` queue.
   Non-injection validation failures (empty / too short / oversized) are *not* logged —
   they aren't security events.
3. **Rate-limit-violation flag (Planning's 4th condition) is deferred.** It needs a
   separate Flask 429 error handler, so it's out of scope for the submit-time write and
   tracked as a follow-up. The limiter itself already returns 429 correctly.
4. **DB recreated fresh** rather than migrated. User's reasoning: the database should
   reflect the current design; any remnant of the old schema is the deviated-from plan.
   Deleting `audit_log.db` and letting `init_db()` rebuild was clean because there is no
   production data to preserve.

### What changed in `app.py`
- **Schema (`init_db`)** now has the full Section-7 column set: added `content_preview`
  (first 200 chars only, for privacy), `appeal_status`, `appeal_reason` (both NULL at
  submit; set later by `/appeal`), `flagged` (0/1), `flag_reason`. No `signal_hf`.
- **`write_log` is now column-driven** off a module-level `AUDIT_COLUMNS` tuple: any
  column a caller omits is stored as NULL. This lets the classified path (full row) and
  the injection-rejection path (no scores) share one writer with no shape mismatch. (SQL
  is built from the fixed tuple, never user input.)
- Added `_now_iso()` helper (UTC ISO-8601 with trailing `Z`), used by both write paths.
- **`/submit` rejection path:** on a high-severity injection, writes a
  `status="rejected_injection"`, `flagged=1` row (with `flag_reason` and a
  `content_preview`, scores NULL) before returning 400.
- **`/submit` classified path:** computes `flagged`/`flag_reason` from two conditions —
  a low-severity injection that was allowed through, and LOW-confidence disagreement
  (`"signal_disagreement (spread=…)"`). Multiple reasons are joined with `; `. Writes the
  full row including `content_preview` and NULL appeal fields.

### Verification (Flask test client)
- New schema confirmed: 16 columns including all five additions, no `signal_hf`.
- Human passage → 200, `status=classified`, `flagged=0`, `flag_reason=NULL`,
  `appeal_status=NULL`, `content_preview` populated.
- High-severity injection → 400, and a logged `rejected_injection` row with `flagged=1`,
  `flag_reason="high_severity_injection: 'ignore all previous instructions'"`, NULL scores.
- Forced high-spread case (patched signals) → `confidence_level=low`, logged `flagged=1`,
  `flag_reason="signal_disagreement (spread=0.8)"`.

### Note
`audit_log.db` now holds a few sample rows from verification (including one synthetic
row from the forced-disagreement test). Harmless; delete the file anytime to reset — it
regenerates empty on next start.

---

## 2026-07-03 — Transparency label generator (checklist item 2)

### Summary
Added `label_generator()` and the three Section-6 transparency labels (A/B/C), and
wired them into the `/submit` response. The placeholder `"label"` string is gone,
replaced by a real `label_text` plus an `appeal_available` boolean.

### Key design decision (diverges from Planning.md — chosen deliberately)
Planning.md M5 specifies `label_generator(combined_score, confidence_level)` that
**re-derives** the attribution (AI/Human/Uncertain) from the score. But
`confidence_scorer()` already makes that decision using the 0.75/0.30 asymmetric
thresholds. Re-deriving in a second function would put those threshold constants in
two places and let them drift.

**Decision:** make `label_generator` a **pure presentation layer** —
`label_generator(attribution)` takes the attribution the scorer already decided and
returns only the wording + appeal flag. One source of truth for the threshold logic
(the scorer), one for the wording (the label generator). Output is identical; the
duplication is removed. (This was raised to the user; implemented on the user's
"use your own judgment" standing guidance while they were away.)

### `appeal_available` rule
`True` for **AI-generated** and **Uncertain**, `False` for **Human-written**. Rationale:
Labels A and C explicitly invite an appeal, and Uncertain is where a false positive on
a human's work lands safely; a favorable Human-written verdict has nothing to contest.
Implemented as `attribution != "Human-written"`.

### What changed in `app.py`
- New Section 6 block after `confidence_scorer`: a `LABELS` dict (the three verbatim
  Section-6 texts, keyed by attribution) and `label_generator(attribution)` returning
  `{"label_text": ..., "appeal_available": ...}`.
- In `submit()`: `label = label_generator(result["attribution"])` after scoring.
- Response: replaced the placeholder `"label"` key with `"label_text"` and added
  `"appeal_available"`. (Kept the existing `content_id` key rather than renaming to
  Planning's `submission_id`, since the audit log and appeal flow already key off
  `content_id`.)

### Verification
- Offline: `label_generator` on all three attributions returns the correct text and
  `appeal_available` (`AI-generated`→True, `Human-written`→False, `Uncertain`→True).
- Live `/submit` end-to-end (test client) on a human-voiced passage →
  `attribution: Human-written`, `confidence_level: high`, `appeal_available: False`,
  and the real Label-B text. Response keys are now
  `[appeal_available, attribution, confidence_level, confidence_score, content_id,
  groq_reason, label_text, signal_scores, signals_used]`.

---

## 2026-07-03 — Wired `validate_input()` into `/submit` (checklist item 1)

### Summary
The input validator + injection defense (`validate_input`, Section 3a/3b) was already
implemented but **never called** — `/submit` ran the detection pipeline on completely
unvalidated input. It is now invoked as the first step of `/submit`, before any signal.

### Why
Without this call, none of the documented gate behavior was actually enforced: empty
text, over-length payloads, sub-minimum-word text, and prompt-injection attempts all
sailed straight into the pipeline (and into a paid Groq call). Wiring the gate in is
the foundation the rest of the pre-stretch work builds on — in particular, the
validator's `flag_reason` output is what the audit log's `flagged`/`flag_reason`
fields will record (checklist item 3).

### What changed in `app.py`
- In `submit()`, after extracting `text`, added:
  `validation = validate_input(text)` and, on `not validation["ok"]`, an early
  `return jsonify({"error": validation["error"]}), 400`.
- The `validation` dict (which also carries `injection` and `flag_reason` for the
  low-severity case that is allowed to continue) is retained for the audit-log step.

### Behavior
- Hard failures → **HTTP 400**: empty/whitespace text, text over `MAX_CHARS`, text
  under `MIN_WORDS` (15), and any high-severity injection phrase (blocked even if the
  text is short — an attack is recorded as an attack, not dismissed as "too short").
- Low-severity injection (e.g. `"you are now"` inside a long passage) → allowed
  through with `injection=True` and a `flag_reason`, to be logged later.

### Verification
Exercised the rejection paths via Flask's test client (no Groq call needed, since
validation returns before the pipeline):
- `too_short` → 400 "Text is too short to analyze (minimum 15 words)."
- `high_injection` (short + "ignore all previous instructions…") → 400 "Invalid input."
- `empty` (whitespace only) → 400 "Text is empty."
- `no_text_field` → 400 (pre-existing JSON-shape check).
- Direct `validate_input()` on a long low-severity-injection passage returns
  `ok=True, injection=True, flag_reason="low_severity_injection: 'you are now'"`.

---

## 2026-07-03 — Removed Signal 4 (HuggingFace RoBERTa classifier)

### Summary
Signal 4 — the HuggingFace `Hello-SimpleAI/chatgpt-detector-roberta` discriminative
classifier — was **removed entirely**. The pipeline is now a **3-signal ensemble**
(Groq LLM + Stylometric heuristics + Burstiness).

### Why
**The deciding reason: it is too difficult to reliably connect a working token to
this API endpoint.** The HuggingFace Inference path requires authentication that the
project can't dependably provide, so rather than fight the token/endpoint setup, the
signal was removed and the pipeline made honest about what it actually computes.

This decision was reinforced by a root-cause investigation. While testing four
labeled example passages (2 clear + 2 borderline), Signal 4 returned `None` on
**every** input — not a transient outage but a permanent break:

- **The endpoint host was deprecated.** `app.py` called
  `https://api-inference.huggingface.co/models/...`. That hostname **no longer
  resolves in DNS** (`getaddrinfo failed`) — HuggingFace retired the old serverless
  Inference API host. Groq (the other network signal) resolved fine, confirming the
  environment had working network; the failure was specific to the dead HF host.
- **The model moved and now requires auth.** The classifier is still reachable, but
  only via the new host `router.huggingface.co/hf-inference/models/...`, which
  returns **HTTP 401** without a HuggingFace token. The project's `.env` only holds
  `GROQ_API_KEY` — no HF token, and provisioning one that authenticates against the
  new endpoint proved impractical.

Net effect: Signal 4 was **structurally guaranteed to abstain in every environment**.
Because `hf_signal` caught all errors and returned `None`, the ensemble silently ran
on 2–3 signals while the code and docs still advertised a 4-signal system. Given how
awkward the token/endpoint wiring is, removing the signal was cleaner than chasing a
connection that may not hold.

### Why removal was safe (behavior-preserving)
An abstaining signal (`None`) already drops out of both the weighted average and the
spread. Since Signal 4 was *always* `None`, it was already contributing nothing.
Re-running the four labeled examples before and after removal produced **identical**
attributions — confirming no behavioral regression on the real pipeline.

### What changed in `app.py`
- Deleted the entire Section 4d: `hf_signal()`, `HF_MODEL`, `HF_URL`.
- Removed the now-unused `import httpx`.
- **Confidence scorer** (`confidence_scorer`):
  - Signature dropped from `(s1, s2, s3, s4)` to `(s1, s2, s3)`.
  - `BASE_WEIGHTS` set to the former "no-S4" values: **S1 0.45 / S2 0.35 / S3 0.20**.
  - Removed the redundant `WEIGHTS_NO_S4` constant and the special-case weight
    branches; the scorer now uses a single general rule — renormalize the base
    weights across whichever signals are present so they sum to 1. (With all three
    present, weights are unchanged; when Signal 3 abstains, S1/S2 rebalance to
    0.5625/0.4375.)
  - `SIGNAL_NAMES` and `signals_used` no longer include `hf`.
- **`/submit` endpoint:** removed the `hf_signal()` call, the `signal_hf` field from
  the audit-log write, and `"hf"` from the `signal_scores` response object.
- **Audit-log schema:** dropped the `signal_hf` column from the `CREATE TABLE`
  statement and the `INSERT`.
- Rewrote the Section 5 comment to describe the 3-signal design and note *why* S4
  was removed.

### What changed in the tests
- `test_signals.py`: removed the `hf_signal` import and the S4 column/calls in
  Parts A and A2; converted all Part B forced-score calls from 4 args to 3 and
  replaced the "S4 unavailable" case with an "S3 abstains → 2 signals" case.
- `test_labeled_examples.py` (throwaway diagnostic harness): same 3-signal update.

### Verification
- `test_signals.py` — **all Part B threshold checks PASS**. Part A2 shows the full
  3-signal path reaching confident labels on real text:
  `long_ai_uniform → AI-generated (high)`, `long_human_irregular → Human-written (high)`.
- The four labeled examples returned identical attributions before and after the
  change (behavior-preserving, as expected).
- Scorer renormalization verified offline: `confidence_scorer(0.8, 0.2, None)` →
  combined `0.5375` (= 0.8×0.5625 + 0.2×0.4375).

### Notes & decisions
- **`Planning.md` is intentionally kept as the historical spec** (Section 4d, the
  weight tables, the Flow 1 diagram still describe the original 4-signal design).
  Decision (2026-07-03): leave it unchanged as a record of the original plan — the
  live 3-signal design is captured here and in the code, not in Planning.md.
- **`audit_log.db`** created before this change still carries a stale `signal_hf`
  column. It is harmless (new writes omit it), but deleting the file regenerates a
  clean schema on next start.

---

## Background — prior design decisions (pre-dating this log)

These earlier choices are recorded here because they are commonly cited when
explaining the scorer's behavior.

### Spread thresholds widened (M4, 2026-07-02)
Confidence thresholds were widened from the original `HIGH ≤0.20 / MEDIUM ≤0.40` to
**`HIGH ≤0.30 / MEDIUM ≤0.45`**. Reason: with heterogeneous signals that each measure
different properties, a ≤0.20 spread almost never occurs, so real inputs collapsed to
"Uncertain". Widening keeps the asymmetric label rule (AI/Human both require HIGH
confidence) intact while letting genuinely clear cases earn a Human/AI label.

### Burstiness (Signal 3) abstains instead of returning a neutral 0.5 (M4, 2026-07-02)
Signal 3 returns `None` (abstains) when it has fewer than 8 sentences **or** lands in
a neutral deadzone (`ai_burst` 0.40–0.60), and then drops out of both the weighted
average and the spread. Reason: its old neutral 0.5 muddied signal agreement and
pushed legitimately human texts toward "Uncertain". Burstiness is a one-directional
signal — informative when flagging uniform AI text, weak at confirming humans — so it
stays quiet when it has nothing meaningful to say.

### Label rule (unchanged from Planning.md)
Attribution is asymmetric and requires agreement: `combined ≥ 0.75` **and** HIGH
confidence → "AI-generated"; `combined ≤ 0.30` **and** HIGH confidence →
"Human-written"; everything else → "Uncertain". A false positive (calling a human's
work AI) is treated as worse than a false negative, so the AI label demands strong
evidence *and* signal agreement.
