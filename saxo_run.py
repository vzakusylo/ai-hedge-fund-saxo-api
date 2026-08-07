#!/usr/bin/env python
"""
saxo_run.py — Run the AI Hedge Fund and execute decisions via Saxo Bank API.

Usage:
    python saxo_run.py --ticker AAPL,MSFT --model gpt-4o
    python saxo_run.py --ticker AAPL --dry-run          # pre-check only
    python saxo_run.py --ticker AAPL --asset-type Stock

The script:
  1. Runs the ai-hedge-fund workflow to get trading signals.
  2. Shows the Portfolio Manager's decisions in a summary table.
  3. Asks for human approval (all / none / per-order).
  4. Executes approved orders through the Saxo Bank OpenAPI.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from colorama import Fore, Style, init

# Ensure the project root is on the path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

load_dotenv()
init(autoreset=True)

from src.main import run_hedge_fund  # noqa: E402
from src.saxo.client import SaxoClient  # noqa: E402
from src.saxo.execution import approve_and_execute  # noqa: E402
from src.data.cache import get_cache  # noqa: E402


def fetch_saxo_portfolio(saxo: "SaxoClient") -> dict:
    """Fetch current Saxo account state: balance, positions, open orders."""
    account_key = saxo.get_account_key()
    balance = saxo.get_balance(account_key)
    positions = saxo.get_positions(account_key)
    open_orders = saxo.get_open_orders(account_key)

    cash = balance.get("CashBalance", balance.get("TotalValue", 0.0))
    equity = balance.get("NetEquityForMargin", balance.get("TotalValue", cash))

    # Print summary
    print(f"\n{Fore.CYAN}{'=' * 60}")
    print(f"  Saxo Account: {account_key}")
    print(f"  Cash Balance : {cash:,.2f} {balance.get('Currency', '')}")
    print(f"  Equity       : {equity:,.2f} {balance.get('Currency', '')}")
    print(f"{'=' * 60}{Style.RESET_ALL}")

    if positions:
        print(Fore.YELLOW + f"\n  Open Positions ({len(positions)}):")
        for p in positions:
            sym = p.get("DisplayAndFormat", {}).get("Symbol", "?")
            qty = p.get("PositionBase", {}).get("Amount", 0)
            pnl = p.get("PositionView", {}).get("ProfitLossOnTrade", 0)
            print(f"    {sym:<12} qty={qty:>8}  PnL={pnl:>+10.2f}")
    else:
        print(Fore.WHITE + "  No open positions.")

    if open_orders:
        print(Fore.YELLOW + f"\n  Open Orders ({len(open_orders)}):")
        for o in open_orders:
            sym = o.get("DisplayAndFormat", {}).get("Symbol", "?")
            side = o.get("BuySell", "?")
            qty = o.get("Amount", 0)
            oid = o.get("OrderId", "?")
            print(f"    OrderId={oid}  {side} {qty} × {sym}")
    else:
        print(Fore.WHITE + "  No open orders.")
    print()

    # Build positions dict keyed by ticker prefix (e.g. "AAPL")
    pos_by_ticker: dict[str, dict] = {}
    for p in positions:
        sym = p.get("DisplayAndFormat", {}).get("Symbol", "").split(":")[0].upper()
        amt = p.get("PositionBase", {}).get("Amount", 0)
        cb = p.get("PositionBase", {}).get("OpenPrice", 0.0)
        if sym:
            pos_by_ticker[sym] = {
                "long": max(0, amt),
                "short": max(0, -amt),
                "long_cost_basis": cb if amt > 0 else 0.0,
                "short_cost_basis": cb if amt < 0 else 0.0,
                "short_margin_used": 0.0,
            }

    return {
        "account_key": account_key,
        "cash": float(cash),
        "equity": float(equity),
        "currency": balance.get("Currency", "EUR"),
        "positions_raw": positions,
        "open_orders_raw": open_orders,
        "positions_by_ticker": pos_by_ticker,
    }


def build_portfolio(tickers: list[str], cash: float = 100_000.0, saxo_state: dict = None) -> dict:
    """Build portfolio dict for agents, optionally seeded from live Saxo state."""
    if saxo_state:
        pos_by_ticker = saxo_state.get("positions_by_ticker", {})
        positions = {
            t: pos_by_ticker.get(t, {
                "long": 0, "short": 0, "long_cost_basis": 0.0,
                "short_cost_basis": 0.0, "short_margin_used": 0.0,
            })
            for t in tickers
        }
        return {
            "cash": saxo_state["cash"],
            "margin_requirement": 0.5,
            "margin_used": 0.0,
            "equity": saxo_state["equity"],
            "positions": positions,
            "realized_gains": {t: {"long": 0.0, "short": 0.0} for t in tickers},
        }
    return {
        "cash": cash,
        "margin_requirement": 0.5,
        "margin_used": 0.0,
        "positions": {
            t: {"long": 0, "short": 0, "long_cost_basis": 0.0,
                "short_cost_basis": 0.0, "short_margin_used": 0.0}
            for t in tickers
        },
        "realized_gains": {t: {"long": 0.0, "short": 0.0} for t in tickers},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Hedge Fund + Saxo Execution")
    parser.add_argument("--ticker", required=True, help="Comma-separated tickers, e.g. AAPL,MSFT")
    parser.add_argument("--cash", type=float, default=100_000.0, help="Starting cash (default 100000)")
    parser.add_argument("--model", default="gpt-4o", help="LLM model name (default gpt-4o)")
    parser.add_argument("--provider", default="OpenAI", help="LLM provider (default OpenAI)")
    parser.add_argument("--analysts", default="", help="Comma-separated analyst keys (default: all)")
    parser.add_argument("--show-reasoning", action="store_true", help="Print analyst reasoning")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use Saxo pre-check endpoint — no real orders placed")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Automatically approve all orders (for CI/cloud runs)")
    parser.add_argument("--refresh-cache", action="store_true",
                        help="Clear the financial data cache and re-fetch from API")
    parser.add_argument("--asset-type", default="Stock", help="Saxo asset type (default: Stock)")
    parser.add_argument("--days-back", type=int, default=90,
                        help="Days of historical data to analyse (default 90)")
    # Options trading
    parser.add_argument("--options", action="store_true",
                        help="Also run options strategies (bear put spreads) based on agent signals")
    parser.add_argument("--options-ticker", default="QQQ",
                        help="Ticker for options strategies (default: QQQ)")
    parser.add_argument("--options-expiry", default="",
                        help="Options expiry date YYYY-MM-DD (default: ~45 days out)")
    parser.add_argument("--options-budget", type=float, default=200.0,
                        help="Max debit budget per options strategy in USD (default: 200)")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.ticker.split(",")]
    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=args.days_back)).strftime("%Y-%m-%d")
    selected_analysts = [a.strip() for a in args.analysts.split(",") if a.strip()] or []

    print(f"\n{Fore.CYAN}{'=' * 60}")
    print("  AI Hedge Fund → Saxo Trader")
    print(f"  Tickers : {', '.join(tickers)}")
    print(f"  Period  : {start_date} → {end_date}")
    print(f"  Model   : {args.model} ({args.provider})")
    print(f"  Mode    : {'DRY RUN (pre-check)' if args.dry_run else 'LIVE (real orders)'}")
    print(f"  Cache   : {'REFRESH (cleared)' if args.refresh_cache else '.cache/financial_data.json'}")
    print(f"{'=' * 60}{Style.RESET_ALL}\n")

    if args.refresh_cache:
        get_cache().clear()
        print(Fore.YELLOW + "[cache] Cache cleared — will fetch fresh data from API.\n")

    # ---- Step 1: Connect to Saxo and fetch current portfolio state ---- #
    saxo_token = os.environ.get("SAXO_ACCESS_TOKEN", "")
    if not saxo_token:
        print(Fore.RED + "\nSAXO_ACCESS_TOKEN not set in environment. Cannot connect to Saxo API.")
        sys.exit(1)

    saxo = SaxoClient(saxo_token)
    print(Fore.CYAN + "Fetching current Saxo portfolio state…")
    saxo_state = fetch_saxo_portfolio(saxo)

    # ---- Step 2: Run the hedge fund workflow ---- #
    print(Fore.CYAN + "Running AI Hedge Fund analysis…\n")
    portfolio = build_portfolio(tickers, args.cash, saxo_state=saxo_state)

    result = run_hedge_fund(
        tickers=tickers,
        start_date=start_date,
        end_date=end_date,
        portfolio=portfolio,
        show_reasoning=args.show_reasoning,
        selected_analysts=selected_analysts,
        model_name=args.model,
        model_provider=args.provider,
    )

    decisions_raw = result.get("decisions") or {}
    if not decisions_raw:
        print(Fore.RED + "No decisions returned from the hedge fund. Exiting.")
        sys.exit(1)

    # Normalise: decisions may be PortfolioDecision objects or plain dicts
    decisions: dict[str, dict] = {}
    for ticker, dec in decisions_raw.items():
        if hasattr(dec, "model_dump"):
            decisions[ticker] = dec.model_dump()
        elif isinstance(dec, dict):
            decisions[ticker] = dec
        else:
            decisions[ticker] = {"action": str(dec), "quantity": 0, "confidence": 0, "reasoning": ""}

    print(Fore.CYAN + "\nPortfolio Manager decisions:")
    for ticker, dec in decisions.items():
        action = dec.get("action", "?")
        qty = dec.get("quantity", 0)
        conf = dec.get("confidence", 0)
        color = Fore.GREEN if action in ("buy", "cover") else Fore.RED if action in ("sell", "short") else Fore.WHITE
        print(f"  {color}{ticker}: {action.upper()} {qty} shares  (confidence {conf}%)")

    # ---- Step 3: Saxo approval + execution ---- #
    approve_and_execute(
        decisions=decisions,
        saxo_client=saxo,
        dry_run=args.dry_run,
        asset_type=args.asset_type,
        auto_approve=args.auto_approve,
    )

    # ---- Step 4: Options strategies (optional) ---- #
    if args.options:
        from src.saxo.options import build_bear_put_spread, execute_option_strategy
        from datetime import timedelta as td

        # Determine expiry: use provided or default to ~45 days out on 3rd Friday
        if args.options_expiry:
            expiry = args.options_expiry
        else:
            target = datetime.today() + td(days=45)
            # Find next 3rd Friday of the month
            from calendar import monthrange
            y, m = target.year, target.month
            fridays = [d for d in range(1, monthrange(y, m)[1] + 1)
                       if datetime(y, m, d).weekday() == 4]
            third_friday = datetime(y, m, fridays[2] if len(fridays) >= 3 else fridays[-1])
            expiry = third_friday.strftime("%Y-%m-%d")

        opt_ticker = args.options_ticker
        budget = args.options_budget
        account_key = saxo_state["account_key"]

        print(f"\n{Fore.CYAN}{'=' * 60}")
        print(f"  Options Strategies — {opt_ticker}  expiry={expiry}  budget=${budget}")
        print(f"{'=' * 60}{Style.RESET_ALL}")

        # Determine bearish signal strength from agent decisions
        # Count bearish signals across all analysed tickers
        bear_count = sum(
            1 for d in decisions.values()
            if d.get("action") in ("sell", "short")
        )
        hold_count = sum(1 for d in decisions.values() if d.get("action") == "hold")
        total = len(decisions)
        print(f"  Agent signals: {bear_count} bearish / {hold_count} hold / {total} total")

        if bear_count == 0 and hold_count == total:
            print(Fore.YELLOW + "  No bearish signals — skipping bear put spread.")
        else:
            # Build strategies based on budget
            # Strategy A: ATM bear put spread ($5 wide)
            # We'll need current QQQ price to set strikes — use Saxo price
            root = saxo.get_option_root(opt_ticker)
            if root:
                # Use Saxo underlying price as proxy
                underlying_uic = None
                stock_inst = saxo.find_instrument(opt_ticker, "Stock")
                if not stock_inst:
                    stock_inst = saxo.find_instrument(opt_ticker, "Etf")
                if stock_inst:
                    underlying_uic = stock_inst["Identifier"]
                    spot = saxo.get_instrument_price(underlying_uic, "Etf") or \
                           saxo.get_instrument_price(underlying_uic, "Stock")
                else:
                    spot = None

                if spot:
                    # Round to nearest 5 for ATM strike
                    import math
                    long_strike = round(math.floor(spot / 5) * 5, 0)
                    short_strike = long_strike - 5.0
                    print(f"  {opt_ticker} spot ~${spot:.2f}  →  {long_strike}/{short_strike} bear put spread")
                else:
                    # Fallback: use hardcoded strikes from the analysis
                    long_strike, short_strike = 685.0, 680.0
                    print(f"  Using default strikes {long_strike}/{short_strike}")

                strategy = build_bear_put_spread(
                    saxo=saxo,
                    ticker=opt_ticker,
                    expiry=expiry,
                    long_strike=long_strike,
                    short_strike=short_strike,
                    quantity=1,
                )
                if strategy:
                    if strategy.max_risk and strategy.max_risk > budget:
                        print(Fore.YELLOW +
                              f"  Max risk ${strategy.max_risk} exceeds budget ${budget} — skipping.")
                    else:
                        execute_option_strategy(
                            strategy=strategy,
                            saxo=saxo,
                            account_key=account_key,
                            dry_run=args.dry_run,
                            auto_approve=args.auto_approve,
                        )
            else:
                print(Fore.RED + f"  Option root not found for {opt_ticker}")


if __name__ == "__main__":
    main()
