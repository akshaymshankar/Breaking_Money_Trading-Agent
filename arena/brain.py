"""Decision makers. Each returns {"market_view": str, "decisions": [...]}.

- ClaudeAPIBrain: Anthropic API (ANTHROPIC_API_KEY or `ant auth login`).
- ClaudeCLIBrain: the Claude Code CLI (`claude -p`), so a Claude subscription works.
- RulesBrain: deterministic SMA/RSI/VWAP rules; offline baseline and fallback.

Claude only says what to do and why. Quantities, cash checks and fills are
done by the engine.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "market_view": {"type": "string"},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "action": {"type": "string", "enum": ["BUY", "SHORT", "SELL", "HOLD"]},
                    "allocation_pct": {"type": "number"},
                    "confidence": {"type": "number"},
                    "stop_loss_pct": {"type": "number"},
                    "target_pct": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["symbol", "action", "allocation_pct", "confidence", "stop_loss_pct", "target_pct", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["market_view", "decisions"],
    "additionalProperties": False,
}

SYSTEM = """You are the strategy brain of a PAPER-TRADING simulator for Indian equities (NSE cash market).
All money is virtual. A separate deterministic engine executes your calls with slippage and real Indian charges,
so only trade when the expected move clearly beats roughly 0.1% round-trip costs.

Rules of the arena:
- {sides}
- allocation_pct is the % of total equity for a BUY or SHORT (the engine caps it at {max_pos}%{lev_note}). Use 0 for SELL/HOLD.
- stop_loss_pct and target_pct are distances from entry in %, for BUY or SHORT. Use 0 for SELL/HOLD.
- confidence is 0 to 1.
- Product: {product}. {product_note}
- Max {max_positions} open positions. Do not open new positions after {cutoff} IST.
- Give a decision for every symbol in the snapshot. Keep each reason to one or two specific sentences
  that cite the numbers you used (trend, RSI, VWAP, momentum, volume, P&L).
- Protecting capital matters more than activity. HOLD is a fine answer.
{strategy_notes}"""


def build_prompt(ctx: dict, cfg: dict) -> tuple[str, str]:
    st = cfg["strategy"]
    product_note = ("Intraday positions are force-closed at " + st["square_off_time"] + " IST."
                    if st["product"] == "intraday" else "Positions may be carried overnight.")
    system = SYSTEM.format(
        sides=("BUY opens a long in a stock you do not hold. SHORT sells a stock you do not hold to buy it back "
               "lower the same day (profits when price falls; stop is above entry, target below; positions show negative qty). "
               "SELL closes any open position, long or short. HOLD means do nothing."
               if st.get("allow_short") and st["product"] == "intraday" else
               "Long-only. BUY opens a position in a stock you do not hold. SELL closes a position you hold. HOLD means do nothing."),
        lev_note=(f", then multiplies by {st['leverage']}x intraday leverage" if st.get("leverage", 1) > 1 and st["product"] == "intraday" else ""),
        max_pos=st["max_position_pct"], product=st["product"], product_note=product_note,
        max_positions=st["max_positions"], cutoff=st["no_new_entries_after"],
        strategy_notes="\n".join(x for x in [
            st.get("profile_notes", ""),
            ("Strategy notes from the user:\n" + st["notes"]) if st.get("notes") else "",
            (f"BUY/SHORT calls below confidence {st['min_confidence']} are rejected by the engine." if st.get("min_confidence") else ""),
        ] if x),
    )
    user = "Current state (JSON):\n" + json.dumps(ctx, indent=1, default=str) + "\n\nReturn your decisions."
    return system, user


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


class ClaudeAPIBrain:
    name = "claude-api"

    def __init__(self, cfg: dict):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.cfg = cfg
        self.model = cfg["strategy"]["model"]
        self.effort = cfg["strategy"].get("effort", "medium")
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def _call(self, system: str, user: str, schema: dict | None, max_tokens: int = 16000) -> str:
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}],
                      output_config={"effort": self.effort, **({"format": {"type": "json_schema", "schema": schema}} if schema else {})})
        resp = self.client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        self.usage["input_tokens"] += resp.usage.input_tokens
        self.usage["output_tokens"] += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            raise RuntimeError("model declined this request")
        return next(b.text for b in resp.content if b.type == "text")

    def decide(self, ctx: dict) -> dict:
        system, user = build_prompt(ctx, self.cfg)
        return json.loads(self._call(system, user, DECISION_SCHEMA))

    def write(self, system: str, user: str) -> str:
        return self._call(system, user, None, max_tokens=8000)


class ClaudeCLIBrain:
    name = "claude-cli"

    def __init__(self, cfg: dict):
        self.exe = shutil.which("claude")
        if not self.exe:
            raise RuntimeError("Claude Code CLI not found on PATH. Install it or use brain = 'claude-api'.")
        self.cfg = cfg
        self.model = cfg["strategy"].get("cli_model") or ""

    def _run(self, prompt: str) -> str:
        cmd = [self.exe, "-p", prompt, "--output-format", "json"]
        if self.model:
            cmd += ["--model", self.model]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[:500])
        return json.loads(out.stdout).get("result", "")

    def decide(self, ctx: dict) -> dict:
        system, user = build_prompt(ctx, self.cfg)
        schema_hint = "Reply with ONLY a JSON object matching this schema, no prose:\n" + json.dumps(DECISION_SCHEMA)
        return _extract_json(self._run(system + "\n\n" + user + "\n\n" + schema_hint))

    def write(self, system: str, user: str) -> str:
        return self._run(system + "\n\n" + user)


class RulesBrain:
    """Trend + RSI + VWAP baseline. Also the safety net when Claude is unreachable."""

    name = "rules"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def decide(self, ctx: dict) -> dict:
        st = self.cfg["strategy"]
        held = {p["symbol"]: p for p in ctx["positions"]}
        out = []
        for sym, s in ctx["market"].items():
            d = {"symbol": sym, "action": "HOLD", "allocation_pct": 0, "confidence": 0.5,
                 "stop_loss_pct": 0, "target_pct": 0, "reason": "No clear setup."}
            if "sma9" not in s:
                d["reason"] = "Not enough bars yet."
            elif sym in held and held[sym]["qty"] < 0:
                if s["sma9"] > s["sma21"] or s["above_vwap"] or (s.get("or_low") and s["ltp"] > s["or_low"]):
                    d.update(action="SELL", confidence=0.6,
                             reason=f"Short no longer working: SMA9 {s['sma9']} vs SMA21 {s['sma21']}, above VWAP={s['above_vwap']}.")
                else:
                    d["reason"] = f"Downtrend intact: SMA9 {s['sma9']} < SMA21 {s['sma21']}, RSI {s['rsi14']}."
            elif sym in held:
                back_in_range = st.get("profile") == "allin" and s.get("or_high") and s["ltp"] < s["or_high"]
                if s["sma9"] < s["sma21"] or s["rsi14"] > 75 or not s["above_vwap"] or back_in_range:
                    d.update(action="SELL", confidence=0.6,
                             reason=f"Trend weakening: SMA9 {s['sma9']} vs SMA21 {s['sma21']}, RSI {s['rsi14']}, above VWAP={s['above_vwap']}.")
                else:
                    d["reason"] = f"Trend intact: SMA9 {s['sma9']} > SMA21 {s['sma21']}, RSI {s['rsi14']}."
            elif ctx["entries_allowed"] and st.get("profile") == "allin":
                orh = s.get("or_high")
                if (orh and s["ltp"] > orh and s["sma9"] > s["sma21"] and 55 <= s["rsi14"] <= 75 and s["above_vwap"]
                        and (s.get("vol_ratio") or 0) >= 1.2):
                    d.update(action="BUY", allocation_pct=st["max_position_pct"], confidence=0.85,
                             stop_loss_pct=st["default_stop_loss_pct"], target_pct=st["default_target_pct"],
                             reason=f"Opening-range breakout: {s['ltp']} above OR high {orh}, volume {s['vol_ratio']}x, "
                                    f"RSI {s['rsi14']}, above VWAP {s['vwap']}.")
                orl = s.get("or_low")
                if (d["action"] == "HOLD" and st.get("allow_short") and orl and s["ltp"] < orl and s["sma9"] < s["sma21"]
                        and 25 <= s["rsi14"] <= 45 and not s["above_vwap"] and (s.get("vol_ratio") or 0) >= 1.2):
                    d.update(action="SHORT", allocation_pct=st["max_position_pct"], confidence=0.85,
                             stop_loss_pct=st["default_stop_loss_pct"], target_pct=st["default_target_pct"],
                             reason=f"Opening-range breakdown: {s['ltp']} below OR low {orl}, volume {s['vol_ratio']}x, "
                                    f"RSI {s['rsi14']}, below VWAP {s['vwap']}.")
            elif ctx["entries_allowed"] and st.get("profile") == "aggressive":
                near_high = s["ltp"] >= s["day_high"] * 0.995
                if (s["sma9"] > s["sma21"] and 55 <= s["rsi14"] <= 72 and s["above_vwap"] and near_high
                        and s["mom_30m_pct"] > 0.25 and (s.get("vol_ratio") or 0) >= 1.2):
                    stop = max(0.6, min(1.5 * s["atr_pct"], st["default_stop_loss_pct"]))
                    d.update(action="BUY", allocation_pct=st["max_position_pct"], confidence=0.8,
                             stop_loss_pct=round(stop, 2), target_pct=round(stop, 2),
                             reason=f"A+ breakout: at day high {s['day_high']}, volume {s['vol_ratio']}x, RSI {s['rsi14']}, "
                                    f"30m momentum {s['mom_30m_pct']}%, above VWAP {s['vwap']}.")
            elif ctx["entries_allowed"] and s["sma9"] > s["sma21"] and 52 <= s["rsi14"] <= 68 and s["above_vwap"] and s["mom_30m_pct"] > 0.15:
                stop = max(0.6, min(2 * s["atr_pct"], st["default_stop_loss_pct"]))
                d.update(action="BUY", allocation_pct=st["max_position_pct"], confidence=0.6,
                         stop_loss_pct=round(stop, 2), target_pct=round(stop * 2, 2),
                         reason=f"Uptrend (SMA9 {s['sma9']} > SMA21 {s['sma21']}), RSI {s['rsi14']}, above VWAP {s['vwap']}, 30m momentum {s['mom_30m_pct']}%.")
            out.append(d)
        return {"market_view": "Rules baseline: trade only aligned trend + momentum + VWAP setups.", "decisions": out}

    def write(self, system: str, user: str) -> str:
        raise RuntimeError("rules brain cannot write narrative insights")


def make_brain(cfg: dict):
    name = cfg["strategy"]["brain"]
    if name == "auto":  # Claude Code login if available, else API key, else free rules
        import os
        name = "claude-cli" if shutil.which("claude") else "claude-api" if os.environ.get("ANTHROPIC_API_KEY") else "rules"
        cfg["strategy"]["brain"] = name
    return {"claude-api": ClaudeAPIBrain, "claude-cli": ClaudeCLIBrain, "rules": RulesBrain}[name](cfg)
