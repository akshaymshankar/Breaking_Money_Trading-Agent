"""Paper-only guard.

This program must never place a real-money order. Three layers enforce that:

1. No order code exists. The engine fills orders against an in-memory virtual
   account; nothing in this package calls a broker order endpoint.
2. Network allow-list. Every HTTP call goes through `SafeHTTP`, which only
   permits read-only market-data and login URLs. Anything that looks like an
   order, trade, GTT, margin-pledge or funds-transfer call raises
   `RealMoneyBlocked` before a socket is opened.
3. Import block. Broker trading SDKs (which bundle order APIs) cannot be
   imported while the arena runs.

`PAPER_ONLY` is a constant, not a setting. A config that tries to switch it
off makes the program refuse to start.
"""
from __future__ import annotations

import importlib.abc
import re
import sys
from urllib.parse import urlparse

import requests

PAPER_ONLY = True


class RealMoneyBlocked(RuntimeError):
    """Raised whenever anything tries to reach a real order path."""


# Read-only endpoints we actually use. (host, path regex, allowed methods)
_ALLOWED = [
    ("api.upstox.com", r"^/v[23]/market-quote/", {"GET"}),
    ("api.upstox.com", r"^/v[23]/historical-candle/", {"GET"}),
    ("api.upstox.com", r"^/v2/login/authorization/token$", {"POST"}),
    ("api.upstox.com", r"^/v2/user/profile$", {"GET"}),
    ("assets.upstox.com", r"^/market-quote/instruments/", {"GET"}),
    # Gemini, for the Claude vs Gemini battle: the model list and text generation only.
    ("generativelanguage.googleapis.com", r"^/v1beta/models(/[\w.\-]+:generateContent)?$", {"GET", "POST"}),
]

# Belt and braces: even an allowed host is refused if the path smells like trading.
_FORBIDDEN_PATH = re.compile(
    r"(order|trade|gtt|position|holding|margin|fund|pledge|payout|basket|mtf|convert)",
    re.IGNORECASE,
)

# Broker SDKs that ship order-placement functions. We use plain HTTP instead.
BLOCKED_MODULES = {
    "kiteconnect",
    "upstox_client",
    "SmartApi",
    "smartapi",
    "dhanhq",
    "fyers_apiv3",
    "breeze_connect",
    "py5paisa",
    "alice_blue",
    "pya3",
    "growwapi",
}


# Known real-order endpoints, used by `python -m arena check` to prove they are blocked.
SELF_TEST_URLS = [
    "https://api.upstox.com/v2/order/place",
    "https://api.upstox.com/v3/order/place",
    "https://api.kite.trade/orders/regular",
    "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/placeOrder",
]


def check_url(method: str, url: str) -> None:
    parsed = urlparse(url)
    host, path = parsed.hostname or "", parsed.path or "/"
    if _FORBIDDEN_PATH.search(path) and not path.startswith("/market-quote/instruments/"):
        raise RealMoneyBlocked(f"Blocked {method} {url}: trading endpoints are never called.")
    for allowed_host, pattern, methods in _ALLOWED:
        if host == allowed_host and re.search(pattern, path) and method.upper() in methods:
            return
    raise RealMoneyBlocked(f"Blocked {method} {url}: not on the read-only allow-list.")


class SafeHTTP:
    """The only HTTP client broker adapters may use."""

    def __init__(self, timeout: float = 15.0):
        self._session = requests.Session()
        self._timeout = timeout

    def get(self, url: str, **kw) -> requests.Response:
        check_url("GET", url)
        kw.setdefault("timeout", self._timeout)
        return self._session.get(url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        check_url("POST", url)
        kw.setdefault("timeout", self._timeout)
        return self._session.post(url, **kw)


class _BrokerSDKBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        root = fullname.split(".")[0]
        if root in BLOCKED_MODULES:
            raise RealMoneyBlocked(
                f"Import of '{fullname}' blocked: broker trading SDKs are not allowed in the paper arena."
            )
        return None


def install_guards(config: dict | None = None) -> None:
    """Call once at startup."""
    if config is not None:
        flag = config.get("safety", {}).get("paper_only", True)
        if flag is not True:
            raise RealMoneyBlocked("paper_only cannot be disabled. This software only simulates trades.")
    if not any(isinstance(f, _BrokerSDKBlocker) for f in sys.meta_path):
        sys.meta_path.insert(0, _BrokerSDKBlocker())
    already = BLOCKED_MODULES.intersection(m.split(".")[0] for m in sys.modules)
    if already:
        raise RealMoneyBlocked(f"Broker SDK already loaded: {sorted(already)}. Refusing to start.")
