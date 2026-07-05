# ProvenanceGuard

ProvenanceGuard is a content-provenance service that estimates whether a piece of
submitted text was written by a human or generated with AI assistance. Rather than
returning a single yes/no, it runs several independent detectors, measures how much they
agree, and turns the result into a plain-language transparency label — deliberately
erring on the side of *not* accusing a human when the evidence is mixed.

## Architecture Overview

A submission travels through one pipeline, from raw text to a transparency label:

1. **`POST /submit`** receives JSON `{ "text": ..., "creator_id": <optional> }`.
2. **Rate limiter** (Flask-Limiter, 10/min and 100/day). Over the limit returns a clean
   JSON `429`; otherwise the request continues.
3. **Input validator + injection defense** (`validate_input`) — the gate. Empty text,
   text over 20,000 characters, or text under 15 words is rejected with `400`. A
   **high-severity** injection attempt (e.g. "ignore all previous instructions") is
   logged as a flagged, rejected audit entry and then rejected with `400`. A
   **low-severity** attempt (e.g. "you are now") is allowed through but flagged.
4. **Three detection signals** run on the text (see below): the Groq LLM classifier,
   stylometric heuristics, and burstiness (which may abstain on short text).
5. **Confidence scorer** (`confidence_scorer`) combines the signals into a single
   `combined_score`, measures how much they agree (the spread) to derive a
   `confidence_level`, and applies the asymmetric rule to pick an attribution
   (AI-generated / Human-written / Uncertain).
6. **Transparency label generator** (`label_generator`) maps that attribution to the
   plain-language text the user reads and an `appeal_available` flag.
7. **Audit-log write** (SQLite) happens *before* the response is returned, so nothing is
   answered unlogged — the signals, scores, verdict, a 200-character content preview, and
   any monitoring flags are all persisted first.
8. **JSON response** goes back to the client with the verdict, scores, label, and appeal
   flag.

Supporting endpoints: **`POST /appeal`** contests a verdict and records it for review;
**`GET /log`** returns the audit trail; **`GET /flagged`** returns the human-review queue
of flagged rows.

## Detection Signals

The system is a **3-signal ensemble**. Each signal independently returns an AI-likelihood
from `0.0` (human) to `1.0` (AI). Combining several *different kinds* of evidence means no
single blind spot decides the verdict on its own.

- **Signal 1 — Groq LLM semantic classifier (weight 0.45).**
  *Measures:* holistic "humanness" — tone, hedging, structural uniformity, lexical
  variety. It reads text the way a person judging "this feels AI-written" would.
  *Why chosen:* an LLM captures gestalt qualities that hand-written math can't — voice and
  naturalness — so it carries the most weight as the most semantically informed signal. It
  is also hardened against prompt injection: the submitted text is wrapped in `<content>`
  tags and framed as data, never as instructions.
  *What it misses:* it can be fooled by text deliberately made messy or ungrammatical,
  it's a generative model judging generation (a known circularity), and it's slightly
  non-deterministic run-to-run.

- **Signal 2 — Stylometric heuristics bundle (weight 0.35).**
  *Measures:* three cheap, deterministic sub-metrics, averaged — sentence-length variance
  (coefficient of variation: humans vary, AI is uniform), type-token ratio (vocabulary
  richness), and punctuation density (humans punctuate more expressively).
  *Why chosen:* no API, instant, fully explainable, and it targets AI's tell-tale
  uniformity directly. It's a pure-math counterweight to the LLM's black box.
  *What it misses:* it penalizes clean, uniform human writing — formal/academic prose and
  non-native English both look "AI-like" to it, and it mis-flags repetitive poetry (see
  Known Limitations).

- **Signal 3 — Burstiness score (weight 0.20, abstains on short text).**
  *Measures:* the Goh–Barabási burstiness of sentence lengths — do lengths arrive in
  human-like bursts (long, long, short, tiny) or spread out with robotic evenness?
  *Why chosen:* it asks a *different* question than Signal 2. Signal 2 asks "how much
  variance overall?"; Signal 3 asks "does that variance cluster or stay even?" — sequential
  structure, not aggregate spread.
  *What it misses:* it needs **≥ 8 sentences** to be meaningful, so it **abstains** on
  short text, and it's one-directional (loud when flagging uniform AI, weak at confirming
  humans), so it stays silent unless it has a real opinion. When it abstains, its weight is
  redistributed to Signals 1 and 2.

> *Signal 4 was removed.* The original design included a fourth signal, a HuggingFace
> RoBERTa detector. Its hosted endpoint was deprecated and reliably wiring an auth token
> proved impractical, so it could never actually run. Because an abstaining signal already
> drops out of the math, removing it was behavior-preserving.

## Confidence Scoring

The core idea is **two separate numbers answering two different questions**:

- **Score** (`0.0`–`1.0`): *how AI-like does the writing look?* — the weighted average of
  the signals.
- **Confidence** (high / medium / low): *how much do the signals agree with each other?* —
  derived from the **spread** (highest signal − lowest signal), **not** from the score.

**Combining into the score** is a weighted average: Groq 0.45, Stylometric 0.35,
Burstiness 0.20. If Burstiness abstains, the remaining weights renormalize to sum to 1
(Groq 0.5625 / Stylometric 0.4375).

**Confidence from spread:**

| Spread (gap between highest & lowest signal) | Confidence |
|---|---|
| ≤ 0.30 | high |
| ≤ 0.45 | medium |
| > 0.45 | low |

**Final verdict — asymmetric, deliberately harder to accuse than to clear:**

| Verdict | Requires |
|---|---|
| AI-generated | score ≥ 0.75 **AND** high confidence |
| Human-written | score ≤ 0.30 **AND** high confidence |
| Uncertain | everything else |

**Why this way:** falsely accusing a human of using AI is worse than missing some AI, so
the "AI-generated" label demands both a strong score *and* signal agreement. Anything
doubtful becomes "Uncertain," which always offers an appeal — innocent until *strongly and
consistently* shown otherwise.

The scoring produces meaningful variation rather than a near-constant, as two real
submissions with sharply different confidence show:

**High-confidence example** — an irregular, personal human anecdote ("So here's the thing
about my grandmother's kitchen. It was tiny…"):

| Signal | Score |
|---|---|
| Groq | 0.20 |
| Stylometric | 0.30 |
| Burstiness | (abstained) |
| **Combined** | **0.24** |
| **Spread** | **0.10 → HIGH** |
| **Verdict** | **Human-written** |

Both signals land low and only 0.10 apart, so the system confidently clears it as human.

**Lower-confidence example** — an anaphoric, repetitive poem ("I have a dream that one day
this nation will rise up. I have a dream that…"):

| Signal | Score |
|---|---|
| Groq | 0.20 |
| Stylometric | 0.76 |
| Burstiness | (abstained) |
| **Combined** | **0.45** |
| **Spread** | **0.56 → LOW** |
| **Verdict** | **Uncertain** |

Here the signals flatly disagree: the LLM reads it as clearly human (0.20) while the
stylometric math reads it as AI-like (0.76, because the poem repeats phrases and uses
uniform line lengths). A 0.56 spread yields low confidence and an "Uncertain" verdict that
correctly refuses to make a call and offers an appeal — same system, wildly different
confidence (0.10 vs 0.56).

## Transparency Label

`label_generator` maps each verdict to exactly one of three labels. The display text is
verbatim:

- **High-confidence AI** (verdict = AI-generated):
  > "Our analysis suggests this content was likely generated with AI assistance. Multiple
  > independent checks returned consistent results. If this is your original work, you can
  > submit an appeal below."
  > **Appeal offered: yes.**

- **High-confidence Human** (verdict = Human-written):
  > "Our analysis suggests this content was written by a human author. The writing patterns
  > we checked were consistent with human authorship."
  > **Appeal offered: no** — a favorable verdict has nothing to contest.

- **Uncertain** (everything else):
  > "Our analysis returned mixed signals about this content's origin. This could mean the
  > writing blends human and AI elements, or that our system isn't confident enough to make
  > a determination. You can submit additional context through an appeal."
  > **Appeal offered: yes** — this is where a false positive on a human's work lands safely.

## Rate Limiting

The chosen limits are **10 requests per minute and 100 per day**, applied to `/submit`.

- **10/min** is roughly one submission every 6 seconds sustained — comfortably above a real
  person editing and re-checking a draft (submit, tweak, resubmit), but well below scripted
  hammering. It blocks automated abuse without frustrating a genuine creator who's iterating.
- **100/day** caps sustained abuse across a whole day while staying generous for even a
  prolific legitimate user. It's the backstop against someone who stays just under the
  per-minute limit but grinds all day.
- **Over-limit response:** a Flask `429` error handler returns clean JSON (not the default
  HTML error page), echoing which limit was hit — e.g.
  `{"error": "Rate limit exceeded. Please slow down and try again shortly.", "limit": "10 per 1 minute"}`.
- **Storage:** limits are held in memory, which is fine for this single-process app. A real
  deployment would use a shared store (e.g. Redis) so limits hold across multiple
  workers/servers.

## Known Limitations

**Repetitive / structured poetry (and, for the same reason, non-native or formal-academic
prose) is misclassified as AI-like — by Signal 2 specifically.**

Take anaphoric poetry — "I have a dream… I have a dream…" — where a line or phrase repeats
deliberately. The **stylometric signal (Signal 2)** flags this as AI-like (it scored
**0.76** in testing) because it measures the exact properties such poetry intentionally
violates:
- **Type-token ratio is low** — words repeat by design, which the signal reads as an
  AI's limited vocabulary.
- **Sentence-length variance is low** — parallel structure makes lines uniform in length,
  which the signal reads as robotic evenness.

So a deliberate human literary device trips the same features that flag machine
uniformity. This isn't a "needs more training data" problem — it's baked into *what the
signal mathematically measures*. The same mechanism penalizes non-native English writers
(simpler vocabulary, more uniform structure) and clean academic prose. Our mitigations:
the LLM signal often disagrees (pushing these to "Uncertain" rather than a false AI
accusation, as Example B shows), and every "Uncertain" verdict offers an appeal — a human
review path is the real backstop.

## Spec Reflection

**Helped:** the spec's confidence-and-label design (Section 5/6) handed me a precise
contract instead of a blank page — the **asymmetric thresholds** (score ≥ 0.75 to call AI,
≤ 0.30 to call human, everything else Uncertain), the three fixed label texts, and the
"Uncertain always offers an appeal" rule. I implemented that asymmetry directly, and it
shaped the system's whole philosophy: a false accusation against a human is worse than a
missed AI, so the AI label demands strong evidence *and* signal agreement.

**Diverged:** I **widened the confidence-spread thresholds** from the spec's
`≤0.20 = high / ≤0.40 = medium` to **`≤0.30 = high / ≤0.45 = medium`**. Why: with three
*heterogeneous* signals that each measure different things, a spread as tight as 0.20
almost never happens on real text — so nearly every genuine input collapsed to "Uncertain"
and the system couldn't ever confidently clear a human or flag an AI. Widening the bands
let clear-cut cases actually earn a verdict while keeping the asymmetric rule intact.
*(A second, larger divergence: I removed the spec's 4th signal, the HuggingFace RoBERTa
detector, because its hosted API endpoint was deprecated and reliably attaching an auth
token was impractical — it could never run, so a 3-signal ensemble is the honest system.)*

## AI Usage

**Instance 1 — Diagnosing and removing a dead signal.**
I directed the AI to run the detection pipeline on labeled test passages. It found Signal
4 (HuggingFace) returned nothing on every input and diagnosed the root cause: the
`api-inference.huggingface.co` endpoint was deprecated (its DNS no longer resolves; the
model had moved behind an auth token). It offered two paths — repoint + add a token, or
remove the signal. **I overrode toward removal** (the token wiring was too unreliable to
depend on), and directed it to rebalance the ensemble. It produced the full removal,
reweighted the three remaining signals to 0.45 / 0.35 / 0.20, and updated the tests.

**Instance 2 — Overriding a spec signature to avoid duplicated logic.**
I directed the AI to build the transparency label generator per the spec, which described
`label_generator(combined_score, confidence_level)` — i.e., the generator would
*re-derive* the AI/Human/Uncertain decision from the raw score. The AI flagged that this
would duplicate the 0.75/0.30 threshold logic already living in `confidence_scorer`
(two copies that could drift apart). It proposed a **pure-presentation** design instead:
map from the verdict the scorer already produced. **I approved that divergence**, so the
threshold logic now lives in exactly one place.

**Instance 3 — Overriding proposed defaults on the audit log.**
When I directed the AI to complete the audit log, it surfaced four design decisions with
recommended defaults. **I overrode/confirmed each deliberately:** flag disagreement on our
recalibrated LOW-confidence line (not the spec's raw 0.40), *do* log rejected injection
attempts as flagged entries, *defer* the rate-limit-violation flag, and **recreate the
database fresh** rather than migrate — my reasoning being that the DB should reflect the
current design, not carry vestiges of the deviated-from plan.
