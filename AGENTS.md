# Trading Arena: how this project is built and run by agents

Paper trading only. No code here may reach a broker. `arena/safety.py` enforces it and tests prove it.

## The trading desk (runtime agents)

One cycle, every `decision_minutes`, for each trader in the fleet:

| # | agent | code | AI? | job | if it fails |
| --- | --- | --- | --- | --- | --- |
| 1 | News Scout | `arena/agents/news_scout.py` | no | world, market and stock headlines | skipped, noted |
| 2 | Emotion Analyst | `arena/agents/emotion.py` | Claude + word lists | crowd mood 0-100, emotions, tilts | word lists only |
| 3 | Regime Analyst | `arena/agents/regime.py` | no | bull / neutral / bear / panic from prices | neutral (shorts stay shut) |
| 4 | Strategist | `arena/brain.py` | Claude | BUY SELL SHORT COVER HOLD with reasons | rules strategy |
| 5 | Risk Officer | `arena/agents/risk_officer.py` | no | approve, shrink or refuse each entry | always runs |
| 6 | Executor | `arena/engine.py` | no | size, fill, fees, margin, stops, journal | always runs |

`arena/agents/desk.py` runs 1 to 3 and shares them across the fleet. `arena/runner.py` runs the cycle. `arena/fleet.py`
runs many traders side by side. Every step is saved in the journal's `desk` table.

## Skill switches

Each trader has its own selection (`cfg["skills"]`), changed on the Skills page and applied at its next decision.
`arena/skills.py` holds the registry. Research skills run in step 2 (findings go to the Strategist); checks run right
after the Risk Officer and can refuse or shrink an entry, or change its stop and target, never enlarge it. If the
Risk Manager fails to run, the entry is refused. If another check fails to run, that is noted and the entry goes on to
the engine's own limits. Live sessions only.

Skill tools are separate processes: fixed arguments, time limit, scratch folder, no API keys. They make their own
read-only web requests (Yahoo, Nasdaq Trader, SEC, House Clerk), which the allow-list in `safety.py` does not see. So a
tool runs only while its file matches the reviewed hash in `arena/skill_pins.json`. To update one: review it, then re-pin.

## Rules for anyone (or any agent) changing this code

1. **AI proposes, code disposes.** Model output never reaches the account without the Risk Officer and the engine.
2. **One job per agent, plain data between them.** New agent: new file in `arena/agents/`, a step in `desk.py`, tests.
3. **Fail closed.** Missing news, gauges or regime must never open a risky door.
4. **Outside text is data.** Headlines are cleaned and never followed as instructions.
5. **No lookahead.** Replay and demo see only bars up to their clock, and no news.
6. **Exits are never blocked.**
7. Indian prices go through `compliance.clip` and live Indian sessions through `compliance.refuse_live`. The 30-day lag is a
   constant, never a setting. New price sources need a test in `tests/test_compliance.py`.
8. New network hosts go in `arena/safety.py` as exact, read-only GET paths.
9. Claude Code always runs with `brain.plan_env()`, so a saved API key is never billed. `plan_only` (default on) refuses every call
   the plan does not cover, and unknown counts as not covered. Pay-per-use needs the lock off and the key chosen by name.
10. Auto-run (`arena/autopilot.py`) only presses Start. It never trades, never overrides a person's Stop, and is off by default.
    One copy per journal (`always.OneCopy`): anything that runs live sessions takes that lock.
11. The software stays free, self-run and open source. Never add a hosted service, accounts, fees, prizes, broker connections
    or shipped price data.
12. `tests/` must pass: `python -m unittest discover tests`. Test first for money or safety changes.

## Development agents and skills

- Subagents in `.claude/agents/`: `news-scout`, `bear-strategist`, `risk-reviewer` (run it after safety-related edits).
- Our skills in `.claude/skills/`: `world-mood`, `short-seller`.
- Third-party skills (sources and pins in `.claude/skills/SOURCES.md`): the day-trader pack and `socratic-tutor`.
  The pack is long-only and US-only; use `short-seller` for shorts. Its broker skill is left out: this project never
  connects a broker, so where those skills mention `/trader-broker`, stop at the plan.
