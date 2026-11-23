"""The trading desk: small agents with one job each, run in order by the Desk.

    News Scout -> Emotion Analyst -> Regime Analyst -> Strategist -> Risk Officer -> Executor
    (news_scout)  (emotion)          (regime)          (brain.py)    (risk_officer)  (engine.py)

Each agent reads what the ones before it wrote and hands on plain data. A failed
agent is skipped and reported; the Risk Officer and Executor are deterministic
code and always run, so no model output reaches the paper account unchecked.
"""
from .desk import Desk, market_intel

__all__ = ["Desk", "market_intel"]
