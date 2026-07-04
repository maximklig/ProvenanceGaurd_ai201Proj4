"""Isolated smoke test for Signal 1 (groq_signal).

Runs the four M3 test inputs and prints each score. Expectation:
  - Input 1 (human) should score noticeably LOWER than Input 2 (ChatGPT).
  - Input 4 (anaphoric poem) may score surprisingly HIGH — documented blind spot,
    not a bug.
Run:  .venv/Scripts/python.exe test_groq_signal.py
"""
from app import groq_signal

INPUTS = {
    "1_human": (
        "So I tried making sourdough again this weekend and honestly it was a "
        "disaster. The starter looked fine, all bubbly and alive, but the dough "
        "just would not rise. I waited like six hours, poked it, waited more. "
        "Ended up with this dense sad brick of bread. My roommate ate a slice to "
        "be nice and I could tell she was struggling. Anyway, third time's the "
        "charm maybe?"
    ),
    "2_chatgpt": (
        "Baking sourdough bread is a rewarding endeavor that combines science and "
        "artistry. To achieve optimal results, it is essential to maintain a "
        "healthy starter, ensure proper hydration levels, and allow adequate time "
        "for fermentation. By carefully monitoring each stage of the process, "
        "bakers can consistently produce loaves with a beautiful crust and an "
        "airy, well-developed crumb. Patience and practice are key to mastering "
        "this timeless craft."
    ),
    "3_academic_ambiguous": (
        "The present study examines the relationship between sleep duration and "
        "cognitive performance in undergraduate students. Participants were "
        "assessed over a four-week period using standardized measures. Results "
        "indicate a statistically significant correlation between reduced sleep "
        "and diminished working-memory capacity. These findings are consistent "
        "with prior literature and suggest implications for academic scheduling."
    ),
    "4_anaphoric_poem": (
        "I have a dream that one day this nation will rise up. "
        "I have a dream that my four children will one day live in a nation. "
        "I have a dream that one day even the state of Mississippi will be "
        "transformed. I have a dream today. I have a dream that one day every "
        "valley shall be exalted. I have a dream today."
    ),
}

if __name__ == "__main__":
    for name, text in INPUTS.items():
        score, reason = groq_signal(text)
        print(f"{name:22s} score={score:.2f}  reason={reason}")
