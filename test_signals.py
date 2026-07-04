"""M4 verification: all four signals + confidence scorer.

Part A — run every signal on the same 4 inputs from Signal 1 and compare.
Part B — verify confidence_scorer against the planning.md thresholds using
         forced scores (the checks the milestone explicitly calls for).

Run:  .venv/Scripts/python.exe test_signals.py
"""
from app import (
    groq_signal, stylometric_signal, burstiness_signal,
    confidence_scorer,
)
from test_groq_signal import INPUTS


def part_a():
    print("=" * 78)
    print("PART A — signals on the same 4 inputs (higher = more AI-like)")
    print("=" * 78)
    header = f"{'input':22s} {'S1_groq':>8s} {'S2_style':>9s} {'S3_burst':>9s} | {'combined':>8s} {'conf':>7s}  attribution"
    print(header)
    print("-" * len(header))
    for name, text in INPUTS.items():
        s1, _ = groq_signal(text)
        s2 = stylometric_signal(text)
        s3 = burstiness_signal(text)
        r = confidence_scorer(s1, s2, s3)
        s3_str = f"{s3:>9.2f}" if s3 is not None else f"{'None':>9s}"
        print(f"{name:22s} {s1:>8.2f} {s2:>9.2f} {s3_str} | "
              f"{r['combined_score']:>8.2f} {r['confidence_level']:>7s}  {r['attribution']}")


# Longer inputs (8+ sentences) so Signal 3 (burstiness) is meaningful and the
# pipeline can actually reach HIGH agreement — proving all 3 labels are
# reachable on REAL text, not just forced scores.
LONG_INPUTS = {
    "long_ai_uniform": (
        "Effective time management is an essential skill for students. It allows "
        "them to balance their academic and personal responsibilities. A well "
        "structured schedule helps students allocate their time efficiently. "
        "Setting clear goals is an important part of this process. Students should "
        "also prioritize their tasks based on importance. Regular breaks can "
        "improve focus and productivity. Avoiding distractions is another key "
        "factor in managing time well. Finally, reviewing progress helps students "
        "stay on track. Consistent effort leads to better academic outcomes."
    ),
    "long_human_irregular": (
        "So here's the thing about my grandmother's kitchen. It was tiny. Barely "
        "enough room for two people, and yet somehow, every Sunday, the entire "
        "family crammed in there, elbows knocking, everyone talking over each "
        "other while she stirred a pot of something that had been simmering since "
        "dawn. I never learned the recipe. God, I wish I had. She'd just throw "
        "things in, a handful of this, a pinch of that, tasting as she went, "
        "never measuring anything. When she passed, we tried to recreate it. We "
        "failed, obviously. Some things you can't write down."
    ),
}


def part_a2():
    print("\n" + "=" * 78)
    print("PART A2 — longer, clearly-different real inputs (labels should differ)")
    print("=" * 78)
    header = f"{'input':22s} {'S1_groq':>8s} {'S2_style':>9s} {'S3_burst':>9s} | {'combined':>8s} {'conf':>7s}  attribution"
    print(header)
    print("-" * len(header))
    for name, text in LONG_INPUTS.items():
        s1, _ = groq_signal(text)
        s2 = stylometric_signal(text)
        s3 = burstiness_signal(text)
        r = confidence_scorer(s1, s2, s3)
        s3_str = f"{s3:>9.2f}" if s3 is not None else f"{'None':>9s}"
        print(f"{name:22s} {s1:>8.2f} {s2:>9.2f} {s3_str} | "
              f"{r['combined_score']:>8.2f} {r['confidence_level']:>7s}  {r['attribution']}")


def check(label, got, expected):
    ok = "PASS" if got == expected else "FAIL"
    print(f"  [{ok}] {label}: got {got!r}, expected {expected!r}")


def part_b():
    print("\n" + "=" * 78)
    print("PART B — confidence_scorer vs planning.md thresholds (forced scores)")
    print("=" * 78)

    # 1) Forced disagreement -> LOW confidence, Uncertain (not an AI label).
    r = confidence_scorer(0.9, 0.2, 0.8)
    print(f"disagreement [0.9,0.2,0.8] -> combined={r['combined_score']}, spread={r['spread']}")
    check("confidence_level", r["confidence_level"], "low")
    check("attribution", r["attribution"], "Uncertain")

    # 2) Same-ish mid score, different confidence -> both Uncertain.
    high = confidence_scorer(0.51, 0.51, 0.51)   # spread 0 -> HIGH
    low = confidence_scorer(0.7, 0.2, 0.7)        # mid combined, spread 0.5 -> LOW
    print(f"0.51 HIGH -> {high['attribution']} ({high['confidence_level']}) | "
          f"mid LOW combined={low['combined_score']} -> {low['attribution']} ({low['confidence_level']})")
    check("0.51 HIGH attribution", high["attribution"], "Uncertain")
    check("mid LOW attribution", low["attribution"], "Uncertain")

    # 3) Label-coverage: all three attributions reachable + HIGH gate on strong score.
    ai = confidence_scorer(0.82, 0.82, 0.82)     # HIGH, >=0.75
    human = confidence_scorer(0.21, 0.21, 0.21)  # HIGH, <=0.30
    mid = confidence_scorer(0.60, 0.60, 0.60)    # HIGH, mid-range
    ai_lowconf = confidence_scorer(0.95, 0.4, 0.95)  # >=0.75 but spread 0.55 -> LOW
    print(f"0.82 HIGH -> {ai['attribution']} | 0.21 HIGH -> {human['attribution']} | "
          f"0.60 HIGH -> {mid['attribution']} | strong-but-LOW (combined={ai_lowconf['combined_score']}) -> {ai_lowconf['attribution']}")
    check("0.82 HIGH", ai["attribution"], "AI-generated")
    check("0.21 HIGH", human["attribution"], "Human-written")
    check("0.60 HIGH", mid["attribution"], "Uncertain")
    check("strong score + LOW conf", ai_lowconf["attribution"], "Uncertain")

    # 4) S3 abstains -> rebalanced weights, only 2 signals used.
    reb = confidence_scorer(0.4, 0.4, None)
    print(f"S3=None -> combined={reb['combined_score']}, signals_used={reb['signals_used']}")
    check("rebalanced signal count", len(reb["signals_used"]), 2)


if __name__ == "__main__":
    part_a()
    part_a2()
    part_b()
