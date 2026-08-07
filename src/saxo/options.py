"""
Options strategy module — bear put spreads and other defined-risk structures.

Supported strategies:
  - bear_put_spread: Buy higher strike put, sell lower strike put (debit spread)

Usage example:
    from src.saxo.options import build_bear_put_spread, execute_option_strategy

    strategy = build_bear_put_spread(
        saxo=saxo_client,
        ticker="QQQ",
        expiry="2026-09-18",
        long_strike=685.0,
        short_strike=680.0,
        quantity=1,
    )
    result = execute_option_strategy(strategy, saxo, account_key, dry_run=True)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from colorama import Fore, Style, init

from src.saxo.client import SaxoClient

init(autoreset=True)


# ------------------------------------------------------------------ #
#  Data classes                                                        #
# ------------------------------------------------------------------ #

@dataclass
class OptionLeg:
    uic: int
    ticker: str
    expiry: str
    strike: float
    put_call: str        # "Put" or "Call"
    buy_sell: str        # "Buy" or "Sell"
    quantity: int
    description: str = ""
    ask: Optional[float] = None
    bid: Optional[float] = None
    delta: Optional[float] = None


@dataclass
class OptionStrategy:
    name: str                          # e.g. "Bear Put Spread"
    ticker: str
    legs: list[OptionLeg] = field(default_factory=list)
    max_risk: Optional[float] = None   # debit paid × 100 × qty
    max_profit: Optional[float] = None
    breakeven: Optional[float] = None
    net_debit: Optional[float] = None  # per share
    notes: str = ""


# ------------------------------------------------------------------ #
#  Strategy builders                                                   #
# ------------------------------------------------------------------ #

def build_bear_put_spread(
    saxo: SaxoClient,
    ticker: str,
    expiry: str,
    long_strike: float,
    short_strike: float,
    quantity: int = 1,
) -> Optional[OptionStrategy]:
    """
    Bear put spread: buy long_strike put, sell short_strike put.
    long_strike > short_strike (e.g. 685/680).
    Returns an OptionStrategy or None if contracts not found.
    """
    root = saxo.get_option_root(ticker)
    if not root:
        print(Fore.RED + f"[options] Option root not found for {ticker}")
        return None

    root_id = root["Identifier"]
    print(Fore.CYAN + f"[options] {ticker} option root ID: {root_id}")

    long_opt = saxo.find_option(root_id, expiry, long_strike, "Put")
    if not long_opt:
        print(Fore.RED + f"[options] Long put {ticker} {expiry} {long_strike} not found")
        return None

    short_opt = saxo.find_option(root_id, expiry, short_strike, "Put")
    if not short_opt:
        print(Fore.RED + f"[options] Short put {ticker} {expiry} {short_strike} not found")
        return None

    # Fetch prices
    long_price_data = saxo.get_option_price(long_opt["Uic"])
    short_price_data = saxo.get_option_price(short_opt["Uic"])

    def _mid(data):
        if not data:
            return None
        q = data.get("Quote", {})
        ask, bid = q.get("Ask"), q.get("Bid")
        if ask and bid:
            return round((ask + bid) / 2, 2)
        return ask or bid

    long_mid = _mid(long_price_data)
    short_mid = _mid(short_price_data)

    net_debit = None
    if long_mid is not None and short_mid is not None:
        net_debit = round(long_mid - short_mid, 2)

    spread_width = long_strike - short_strike
    max_risk = round(net_debit * 100 * quantity, 2) if net_debit else None
    max_profit = round((spread_width - net_debit) * 100 * quantity, 2) if net_debit else None
    breakeven = round(long_strike - net_debit, 2) if net_debit else None

    long_leg = OptionLeg(
        uic=long_opt["Uic"],
        ticker=ticker,
        expiry=expiry,
        strike=long_strike,
        put_call="Put",
        buy_sell="Buy",
        quantity=quantity,
        description=f"Long {long_strike}P",
        ask=long_price_data.get("Quote", {}).get("Ask") if long_price_data else None,
        bid=long_price_data.get("Quote", {}).get("Bid") if long_price_data else None,
        delta=long_price_data.get("Greeks", {}).get("Delta") if long_price_data else None,
    )
    short_leg = OptionLeg(
        uic=short_opt["Uic"],
        ticker=ticker,
        expiry=expiry,
        strike=short_strike,
        put_call="Put",
        buy_sell="Sell",
        quantity=quantity,
        description=f"Short {short_strike}P",
        ask=short_price_data.get("Quote", {}).get("Ask") if short_price_data else None,
        bid=short_price_data.get("Quote", {}).get("Bid") if short_price_data else None,
        delta=short_price_data.get("Greeks", {}).get("Delta") if short_price_data else None,
    )

    return OptionStrategy(
        name=f"Bear Put Spread {long_strike}/{short_strike}",
        ticker=ticker,
        legs=[long_leg, short_leg],
        max_risk=max_risk,
        max_profit=max_profit,
        breakeven=breakeven,
        net_debit=net_debit,
        notes=f"Expiry: {expiry} | Width: ${spread_width} | R:R ~1:{round(max_profit/max_risk, 1) if max_risk and max_profit else '?'}",
    )


# ------------------------------------------------------------------ #
#  Display                                                             #
# ------------------------------------------------------------------ #

def print_strategy(strategy: OptionStrategy) -> None:
    print(f"\n{Fore.CYAN}{'=' * 60}")
    print(f"  Options Strategy: {strategy.name}")
    print(f"  Ticker: {strategy.ticker}")
    print(f"{'=' * 60}{Style.RESET_ALL}")
    print(f"  Net Debit   : ${strategy.net_debit}/share" if strategy.net_debit else "  Net Debit   : N/A")
    print(f"  Max Risk    : ${strategy.max_risk}" if strategy.max_risk else "  Max Risk    : N/A")
    print(f"  Max Profit  : ${strategy.max_profit}" if strategy.max_profit else "  Max Profit  : N/A")
    print(f"  Breakeven   : ${strategy.breakeven}" if strategy.breakeven else "  Breakeven   : N/A")
    print(f"  Notes       : {strategy.notes}")
    print("\n  Legs:")
    for leg in strategy.legs:
        delta_str = f"  delta={leg.delta:.2f}" if leg.delta else ""
        bid_ask = f"  bid={leg.bid}  ask={leg.ask}" if leg.bid or leg.ask else ""
        color = Fore.GREEN if leg.buy_sell == "Buy" else Fore.RED
        print(f"    {color}{leg.buy_sell:4} {leg.put_call:4} {leg.ticker} {leg.expiry} ${leg.strike:.2f}  UIC={leg.uic}{bid_ask}{delta_str}")
    print()


# ------------------------------------------------------------------ #
#  Execution                                                           #
# ------------------------------------------------------------------ #

def execute_option_strategy(
    strategy: OptionStrategy,
    saxo: SaxoClient,
    account_key: str,
    dry_run: bool = True,
    auto_approve: bool = False,
) -> dict:
    """
    Execute (or pre-check) a 2-leg option strategy.
    Returns result dict with status and response.
    """
    print_strategy(strategy)

    if not auto_approve:
        print(f"{Fore.YELLOW}Execute options strategy: {strategy.name}?")
        print(f"  Max risk: ${strategy.max_risk}  |  {strategy.notes}")
        if dry_run:
            print("  [DRY RUN] Pre-check only — no real order")
        ans = input(f"{Fore.CYAN}  Approve? [y/N]: {Style.RESET_ALL}").strip().lower()
        if ans != "y":
            print(Fore.RED + "  Rejected.")
            return {"status": "rejected"}
    else:
        print(Fore.YELLOW + f"  [AUTO-APPROVE] Executing: {strategy.name}")

    if len(strategy.legs) != 2:
        return {"status": "error", "error": "Only 2-leg strategies supported"}

    leg1, leg2 = strategy.legs[0], strategy.legs[1]

    time.sleep(0.5)  # avoid rate limiting

    try:
        if dry_run:
            resp = saxo.precheck_option_spread(
                account_key=account_key,
                leg1_uic=leg1.uic,
                leg1_buy_sell=leg1.buy_sell,
                leg2_uic=leg2.uic,
                leg2_buy_sell=leg2.buy_sell,
                quantity=leg1.quantity,
                net_price=strategy.net_debit,
            )
            result = {"status": "precheck_ok", "response": resp}
            print(Fore.GREEN + f"  ✓ Pre-check OK: {resp}")
        else:
            resp = saxo.place_option_spread(
                account_key=account_key,
                leg1_uic=leg1.uic,
                leg1_buy_sell=leg1.buy_sell,
                leg2_uic=leg2.uic,
                leg2_buy_sell=leg2.buy_sell,
                quantity=leg1.quantity,
                net_price=strategy.net_debit,
            )
            order_id = resp.get("OrderId", resp.get("Orders", [{}])[0].get("OrderId", "?"))
            result = {"status": "placed", "order_id": order_id, "response": resp}
            print(Fore.GREEN + f"  ✓ Order placed — OrderId: {order_id}")
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}
        print(Fore.RED + f"  ✗ Failed: {exc}")

    return result
