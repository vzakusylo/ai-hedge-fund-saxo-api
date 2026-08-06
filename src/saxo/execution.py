"""
Human approval gate + Saxo order execution.

Flow:
  1. Receive portfolio manager decisions (dict of ticker -> PortfolioDecision).
  2. Resolve each ticker to a Saxo instrument UIC.
  3. Display a summary table and ask the human to approve/reject each order.
  4. Execute approved orders via the Saxo API.
"""
from __future__ import annotations

import sys
from typing import Optional

from colorama import Fore, Style, init

from src.saxo.client import SaxoClient

init(autoreset=True)


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

ACTION_TO_SAXO = {
    "buy": "Buy",
    "sell": "Sell",
    "short": "Sell",   # short selling
    "cover": "Buy",    # cover a short position
}


def _print_header(title: str) -> None:
    print(f"\n{Fore.CYAN}{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}{Style.RESET_ALL}")


def _print_decision_table(decisions: dict, prices: dict, instruments: dict) -> None:
    """Pretty-print the pending decisions."""
    _print_header("AI Hedge Fund — Pending Orders")
    fmt = f"{'#':<4} {'Ticker':<8} {'Action':<8} {'Qty':>6} {'Price':>10} {'Conf':>6}  Reasoning"
    print(Fore.YELLOW + fmt)
    print("-" * 80)
    for i, (ticker, dec) in enumerate(decisions.items(), 1):
        action = dec.get("action", "hold")
        qty = dec.get("quantity", 0)
        conf = dec.get("confidence", 0)
        reasoning = dec.get("reasoning", "")[:60]
        price = prices.get(ticker)
        price_str = f"${price:.2f}" if price else "N/A"

        color = Fore.GREEN if action in ("buy", "cover") else Fore.RED if action in ("sell", "short") else Fore.WHITE
        print(f"{color}{i:<4} {ticker:<8} {action.upper():<8} {qty:>6} {price_str:>10} {conf:>5}%  {reasoning}")
    print(Style.RESET_ALL)


# ------------------------------------------------------------------ #
#  Main approval + execution function                                  #
# ------------------------------------------------------------------ #

def approve_and_execute(
    decisions: dict,
    saxo_client: Optional[SaxoClient] = None,
    dry_run: bool = False,
    asset_type: str = "Stock",
) -> dict[str, dict]:
    """
    Show decisions to the human, get approval, then execute via Saxo API.

    Args:
        decisions:    dict[ticker, dict] — output of portfolio manager
        saxo_client:  SaxoClient instance (created if None)
        dry_run:      If True, run pre-check only — no real orders
        asset_type:   Saxo AssetType for all tickers (default: Stock)

    Returns:
        dict[ticker, {"status": "approved"|"rejected"|"skipped"|"error", "order": ...}]
    """
    if saxo_client is None:
        saxo_client = SaxoClient()

    # Filter out "hold" decisions — nothing to trade
    tradeable = {
        ticker: dec for ticker, dec in decisions.items()
        if isinstance(dec, dict) and dec.get("action") not in ("hold",)
        and dec.get("quantity", 0) > 0
    }

    if not tradeable:
        print(Fore.YELLOW + "\nNo actionable orders — all decisions are HOLD.")
        return {}

    # Resolve instruments and prices
    print(Fore.CYAN + "\nResolving instruments on Saxo...")
    instruments: dict[str, dict] = {}
    prices: dict[str, float] = {}

    for ticker in tradeable:
        inst = saxo_client.find_instrument(ticker, asset_type)
        if inst:
            instruments[ticker] = inst
            uic = inst["Identifier"]
            price = saxo_client.get_instrument_price(uic, asset_type)
            if price:
                prices[ticker] = price
            print(f"  {ticker}: UIC={uic}, Price={prices.get(ticker, 'N/A')}")
        else:
            print(Fore.RED + f"  {ticker}: instrument NOT FOUND on Saxo — will be skipped")

    # Get account key
    account_key = saxo_client.get_account_key()
    print(f"\n{Fore.CYAN}Account key: {account_key}{Style.RESET_ALL}")

    # Display table
    _print_decision_table(tradeable, prices, instruments)

    if dry_run:
        print(Fore.YELLOW + "[DRY RUN] No real orders will be placed — using pre-check only.\n")

    # ---- Human approval loop ---- #
    results: dict[str, dict] = {}

    approval_mode = _ask_approval_mode()

    for ticker, dec in tradeable.items():
        if ticker not in instruments:
            results[ticker] = {"status": "skipped", "reason": "instrument not found"}
            continue

        inst = instruments[ticker]
        uic = inst["Identifier"]
        action = dec.get("action", "hold")
        qty = dec.get("quantity", 0)
        saxo_side = ACTION_TO_SAXO.get(action)

        if not saxo_side or qty <= 0:
            results[ticker] = {"status": "skipped", "reason": "no valid action"}
            continue

        if approval_mode == "all":
            approved = True
        elif approval_mode == "none":
            approved = False
        else:
            # Per-order approval
            approved = _ask_single(ticker, action, qty, prices.get(ticker))

        if not approved:
            results[ticker] = {"status": "rejected"}
            print(Fore.RED + f"  ✗ {ticker} order rejected")
            continue

        # Execute or pre-check
        try:
            import time
            time.sleep(0.5)  # avoid Saxo rate limiting (429)
            if dry_run:
                resp = saxo_client.precheck_order(
                    account_key=account_key,
                    uic=uic,
                    asset_type=asset_type,
                    buy_sell=saxo_side,
                    quantity=qty,
                )
                results[ticker] = {"status": "approved", "precheck": resp}
                print(Fore.GREEN + f"  ✓ {ticker} pre-check OK: {resp}")
            else:
                resp = saxo_client.place_order(
                    account_key=account_key,
                    uic=uic,
                    asset_type=asset_type,
                    buy_sell=saxo_side,
                    quantity=qty,
                )
                order_id = resp.get("OrderId", "?")
                results[ticker] = {"status": "approved", "order_id": order_id, "response": resp}
                print(Fore.GREEN + f"  ✓ {ticker} order placed — OrderId: {order_id}")
        except Exception as exc:
            results[ticker] = {"status": "error", "error": str(exc)}
            print(Fore.RED + f"  ✗ {ticker} order FAILED: {exc}")

    _print_summary(results)
    return results


# ------------------------------------------------------------------ #
#  Interactive prompts                                                 #
# ------------------------------------------------------------------ #

def _ask_approval_mode() -> str:
    """Ask whether to approve all, none, or decide per order."""
    print(f"\n{Fore.YELLOW}How would you like to approve orders?")
    print("  [A] Approve ALL orders")
    print("  [N] Reject ALL orders")
    print("  [P] Approve Per-order (default)")
    choice = input(f"{Fore.CYAN}Your choice [A/N/P]: {Style.RESET_ALL}").strip().upper()
    if choice == "A":
        return "all"
    if choice == "N":
        return "none"
    return "per"


def _ask_single(ticker: str, action: str, qty: int, price: Optional[float]) -> bool:
    """Ask for approval of a single order."""
    price_str = f"@ ${price:.2f}" if price else ""
    prompt = (
        f"{Fore.YELLOW}  Approve {action.upper()} {qty} × {ticker} {price_str}? "
        f"[y/N]: {Style.RESET_ALL}"
    )
    ans = input(prompt).strip().lower()
    return ans == "y"


def _print_summary(results: dict) -> None:
    _print_header("Execution Summary")
    for ticker, res in results.items():
        status = res.get("status", "?")
        color = Fore.GREEN if status == "approved" else Fore.RED if status in ("rejected", "error") else Fore.YELLOW
        detail = res.get("order_id", res.get("reason", res.get("error", "")))
        print(f"  {color}{ticker:<8} {status.upper():<10} {detail}")
    print(Style.RESET_ALL)
