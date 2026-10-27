"""Touchline: live football you can hear.

An audio-first match companion for blind and low-vision fans. A team of agents
turns a live event stream into spatial, timed commentary:

    State tracker  -> what is true right now (score, ball, players, win probability)
    Describer      -> turns events into short spatial lines ("left wing, 30 yards out")
    Director       -> decides what gets said within a speaking budget, and what is dropped
    Colour agent   -> fills stoppages with context (LLM, or templates offline)
    Verifier       -> checks every number and name the Colour agent says against the facts

Everything is causal: at any moment the agents only know what has already happened.
"""

__version__ = "0.7.0"


def _load_dotenv():
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if v.strip() and not v.strip().startswith("<"):
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
