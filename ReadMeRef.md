# README Reference — ProvenanceGuard

This file is your **answer key + study guide** for writing `README.md`. Part 1 is a
complete reference draft of every required section (written so you understand *why*,
not just *what* — lift phrasing freely, but put it in your own voice). Part 2 is a
bare **skeleton** with prompts you fill in yourself.

All numbers below are real, pulled from the Milestone-4 test runs
(`test_signals.py`, `test_labeled_examples.py`). Note: the Groq (LLM) signal is
mildly stochastic, so its exact value shifts a little run-to-run — the *pattern*
(agreement vs. disagreement, high vs. low confidence) is what's stable.

---

# PART 1 — Reference answers

## 1. Architecture overview — the path a submission takes

A submission travels through a single pipeline from raw text to a transparency label:

1. **`POST /submit`** receives JSON `{ "text": ..., "creator_id": <optional> }`.
2. **Rate limiter** (Flask-Limiter, 10/min & 100/day). Over the limit → `429` with a
   clean JSON body. Otherwise continue.
3. **Input validator + injection defense** (`validate_input`) — the gate:
   - empty / over 20,000 chars / under 15 words → `400`.
   - **high-severity injection** (e.g. "ignore all previous instructions") → logged as
     a flagged, rejected audit entry, then `400`.
   - **low-severity injection** (e.g. "you are now") → allowed through but flagged.
4. **Three detection signals** run on the text (details in §2):
   Groq LLM, Stylometric heuristics, Burstiness (which may abstain on short text).
5. **Confidence scorer** (`confidence_scorer`) combines them into a single
   `combined_score`, measures signal **agreement** (spread) to get a `confidence_level`,
   and applies the asymmetric rule to pick an **attribution** (AI / Human / Uncertain).
6. **Transparency label generator** (`label_generator`) maps the attribution to the
   plain-language `label_text` a user reads and an `appeal_available` flag.
7. **Audit log write** (SQLite) happens *before* the response, so nothing is returned
   unlogged — signals, scores, verdict, a 200-char content preview, and any monitoring
   flags.
8. **JSON response** back to the client with the verdict, scores, label, and appeal flag.

Supporting endpoints: **`POST /appeal`** (contest a verdict → recorded for review),
**`GET /log`** (audit trail), **`GET /flagged`** (human-review queue of flagged rows).

## 2. Detection signals — what each measures, why chosen, what it misses

The system uses a **3-signal ensemble**. Each signal independently returns an
AI-likelihood in `0.0` (human) → `1.0` (AI). Using several *different kinds* of
evidence means no single blind spot decides the verdict.

### Signal 1 — Groq LLM semantic classifier · weight 0.45
- **Measures:** holistic "humanness" — tone, hedging language, structural uniformity,
  lexical variety. It reads the text the way a person would judge "this feels
  AI-written."
- **Why chosen:** an LLM captures *gestalt* qualities that hand-written math can't —
  voice, naturalness, the intangibles. It carries the most weight because it's the most
  semantically informed. (It's also hardened against prompt injection: the text is
  wrapped in `<content>` tags and framed as data, never instructions.)
- **What it misses:** it can be fooled by text deliberately made messy, opinionated, or
  ungrammatical; it's a *generative* model judging generation (a known circularity); and
  it's slightly non-deterministic.

### Signal 2 — Stylometric heuristics bundle · weight 0.35
- **Measures:** three cheap, deterministic sub-metrics, averaged: (a) **sentence-length
  variance** (coefficient of variation — humans vary, AI is uniform), (b) **type-token
  ratio** (vocabulary richness — humans are more lexically varied), (c) **punctuation
  density** (humans punctuate more expressively).
- **Why chosen:** no API, instant, fully explainable, and it targets AI's tell-tale
  *uniformity* directly. It's a good counterweight to the LLM — pure math, no black box.
- **What it misses:** it penalizes *clean, uniform human writing* — formal/academic prose
  and non-native English both look "AI-like" to it. It also mis-flags repetitive poetry
  (see §6).

### Signal 3 — Burstiness score · weight 0.20 (abstains on short text)
- **Measures:** the Goh-Barabási burstiness of sentence lengths — do lengths arrive in
  human-like *bursts* (long, long, short, tiny) or spread out with robotic evenness?
- **Why chosen:** it asks a *different* question than Signal 2. Signal 2 asks "how much
  variance overall?"; Signal 3 asks "does the variance come in clusters or evenly?" —
  sequential structure, not just aggregate spread.
- **What it misses:** it needs **≥ 8 sentences** to be meaningful, so it **abstains
  (returns nothing)** on short text — and it's one-directional (loud when flagging
  uniform AI, weak at confirming humans), so it stays silent unless it has a real
  opinion. When it abstains, its weight is redistributed to Signals 1 & 2.

> **Note on Signal 4:** the original design had a 4th signal (a HuggingFace RoBERTa
> detector). It was removed — its hosted endpoint was deprecated and reliably wiring an
> auth token proved impractical, so it could never actually run. See §7.

## 3. Confidence scoring — how signals combine, and proof it's meaningful

**Two separate numbers, two different questions.** This is the core idea:
- **Score** (`0.0`–`1.0`): *how AI-like does the writing look?* → the weighted average of
  the signals.
- **Confidence** (high / medium / low): *how much do the signals agree with each other?*
  → derived from the **spread** (highest signal − lowest signal), NOT from the score.

**Combining into the score:** weighted average — Groq 0.45, Stylometric 0.35,
Burstiness 0.20. If Burstiness abstains, the remaining weights renormalize to sum to 1
(so Groq 0.5625 / Stylometric 0.4375).

**Confidence from spread:**
| Spread (gap between highest & lowest signal) | Confidence |
|---|---|
| ≤ 0.30 | high |
| ≤ 0.45 | medium |
| > 0.45 | low |

**Final verdict (asymmetric — deliberately harder to accuse than to clear):**
| Verdict | Requires |
|---|---|
| AI-generated | score ≥ 0.75 **AND** high confidence |
| Human-written | score ≤ 0.30 **AND** high confidence |
| Uncertain | everything else |

**How I validated it produces meaningful variation (not a constant):**
- **Forced-score unit tests** confirm the thresholds behave: a disagreeing input
  `[0.9, 0.2, 0.8]` → LOW confidence → "Uncertain" (never an AI label); a `0.82` agreeing
  input → "AI-generated"; a `0.21` agreeing input → "Human-written"; a `0.60` agreeing
  input → "Uncertain" (mid score can't earn a label).
- **Real inputs** spread across the range rather than clustering — see the two examples
  below, whose confidence differs sharply.

### Two example submissions with noticeably different confidence

**Example A — HIGH confidence** (signals agree)
> *"So here's the thing about my grandmother's kitchen. It was tiny…"* (an irregular,
> personal human anecdote)

| Signal | Score |
|---|---|
| Groq | 0.20 |
| Stylometric | 0.30 |
| Burstiness | (abstained) |
| **Combined** | **0.24** |
| **Spread** | **0.10** → **HIGH** |
| **Verdict** | **Human-written** |

Both signals land low and close together (0.10 apart) → high confidence → a confident
"Human-written."

**Example B — LOW confidence** (signals disagree)
> *"I have a dream that one day this nation will rise up. I have a dream that…"* (an
> anaphoric, repetitive poem)

| Signal | Score |
|---|---|
| Groq | 0.20 |
| Stylometric | 0.76 |
| Burstiness | (abstained) |
| **Combined** | **0.45** |
| **Spread** | **0.56** → **LOW** |
| **Verdict** | **Uncertain** |

Here the signals **flatly disagree**: the LLM reads it as clearly human (0.20), while the
stylometric math reads it as AI-like (0.76, because the poem repeats phrases and uses
uniform line lengths). A spread of 0.56 → low confidence → "Uncertain," which correctly
refuses to make a call and offers an appeal. Same system, wildly different confidence
(spread 0.10 vs 0.56) — that's the meaningful variation.

*(For reference, a confident AI case also exists: a uniform AI-style essay scored Groq
0.80 / Stylometric 0.67 / Burstiness 0.88 → combined **0.77**, spread 0.21 → HIGH →
"AI-generated.")*

## 4. Transparency label — the three variants (exact display text)

`label_generator` maps each verdict to one of exactly three labels. Verbatim text:

**Variant A — High-confidence AI** (verdict = AI-generated):
> "Our analysis suggests this content was likely generated with AI assistance. Multiple
> independent checks returned consistent results. If this is your original work, you can
> submit an appeal below."
> *appeal offered: yes*

**Variant B — High-confidence Human** (verdict = Human-written):
> "Our analysis suggests this content was written by a human author. The writing patterns
> we checked were consistent with human authorship."
> *appeal offered: no (a favorable verdict has nothing to contest)*

**Variant C — Uncertain** (everything else):
> "Our analysis returned mixed signals about this content's origin. This could mean the
> writing blends human and AI elements, or that our system isn't confident enough to make
> a determination. You can submit additional context through an appeal."
> *appeal offered: yes — this is where a false positive on a human's work lands safely.*

## 5. Rate limiting — the limits and the reasoning

**Chosen limits: 10 requests per minute, 100 per day**, applied to `/submit`.

- **10/min** ≈ one submission every 6 seconds sustained. That's comfortably *above* a
  real person editing and re-checking a draft (submit, tweak, resubmit), but well *below*
  scripted hammering — it blocks automated abuse without frustrating a genuine creator
  who's iterating.
- **100/day** caps sustained abuse over a whole day while staying generous for even a
  prolific legitimate user. It's the backstop for someone who stays just under the
  per-minute limit but grinds all day.
- **Over-limit response:** a Flask `429` handler returns clean JSON (not the default HTML
  error page), echoing which limit was hit.
- **Storage:** in-memory (fine for this single-process app). A real deployment would use
  a shared store (e.g. Redis) so limits hold across multiple workers/servers.

*Honest note:* the original plan imagined a two-tier "warn, then block" gate (gently warn
a fast-but-human user before hard-blocking a spammer). I implemented a **single hard
limit** for simplicity; the tiered warning is a possible future refinement.

## 6. Known limitations — a specific thing it gets wrong, and why

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

## 7. Spec reflection — one way it helped, one way I diverged

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

## 8. AI usage — specific instances (what I directed, what it produced, what I revised)

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

---

# PART 2 — README skeleton (write in your own voice)

Copy this into `README.md` and fill each section. Prompts in *italics* are reminders of
what the grader is looking for — delete them as you go.

```markdown
# ProvenanceGuard

*One or two sentences: what this project is and the problem it addresses.*

## Architecture Overview
*Walk the reader through the path a submission takes from POST /submit to the
transparency label. Mention the gate, the signals, the scorer, the label, the audit
write. A simple numbered list or arrow diagram works.*

## Detection Signals
*For EACH of the 3 signals: what it measures, why you chose it, and what it misses.
Don't just describe the code — explain the reasoning.*
- Signal 1 — Groq LLM:
- Signal 2 — Stylometric heuristics:
- Signal 3 — Burstiness:
*(Optionally note Signal 4 was removed and why.)*

## Confidence Scoring
*Explain the two-numbers idea (score = how AI-like; confidence = how much signals
agree). Give the weights, the spread→confidence bands, and the asymmetric verdict rule.
Explain WHY this approach (false-accusation aversion). Then show it produces meaningful
variation with your two examples:*
- High-confidence example: *text + the actual signal scores, combined, spread, verdict.*
- Lower-confidence example: *text + the actual signal scores, combined, spread, verdict.*

## Transparency Label
*Write out the exact display text for all three variants and note whether each offers an
appeal.*
- High-confidence AI:
- High-confidence Human:
- Uncertain:

## Rate Limiting
*State your limits (10/min, 100/day) and justify those specific numbers. Note the 429
JSON response and the in-memory storage choice.*

## Known Limitations
*Name at least one specific content type your system misclassifies and tie it to a
property of a signal (not "needs more data"). The poetry / Signal-2 case is your
strongest.*

## Spec Reflection
*One way the spec helped you. One way your implementation diverged and why.*

## AI Usage
*At least two concrete instances: what you directed the AI to do, what it produced, and
what you revised or overrode.*
1.
2.
```

---

*Keep this file for your own reference; it does not need to be submitted. When your
`README.md` is done, you can delete `ReadMeRef.md` and reset `audit_log.db` (delete the
file — it regenerates empty) for clean grading screenshots. Test files to keep:
`test_signals.py` (scoring proof), `test_groq_signal.py` (Signal 1), and `verify.py`
(endpoints).*
