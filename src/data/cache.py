import json
import os
from pathlib import Path

# Default location for the persistent cache file
_CACHE_FILE = Path(os.environ.get("CACHE_FILE", Path(__file__).parent.parent.parent / ".cache" / "financial_data.json"))


class Cache:
    """In-memory + JSON-persistent cache for API responses."""

    def __init__(self, cache_file: Path = _CACHE_FILE):
        self._cache_file = cache_file
        self._prices_cache: dict[str, list[dict[str, any]]] = {}
        self._financial_metrics_cache: dict[str, list[dict[str, any]]] = {}
        self._line_items_cache: dict[str, list[dict[str, any]]] = {}
        self._insider_trades_cache: dict[str, list[dict[str, any]]] = {}
        self._company_news_cache: dict[str, list[dict[str, any]]] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self):
        """Load cache from JSON file if it exists."""
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._prices_cache = data.get("prices", {})
                self._financial_metrics_cache = data.get("financial_metrics", {})
                self._line_items_cache = data.get("line_items", {})
                self._insider_trades_cache = data.get("insider_trades", {})
                self._company_news_cache = data.get("company_news", {})
                print(f"[cache] Loaded from {self._cache_file}")
            except Exception as e:
                print(f"[cache] Warning: could not load cache: {e}")

    def save(self):
        """Persist current in-memory cache to JSON file."""
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "prices": self._prices_cache,
            "financial_metrics": self._financial_metrics_cache,
            "line_items": self._line_items_cache,
            "insider_trades": self._insider_trades_cache,
            "company_news": self._company_news_cache,
        }
        with open(self._cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        print(f"[cache] Saved to {self._cache_file}")

    def clear(self):
        """Clear all cached data (in-memory and on disk)."""
        self._prices_cache = {}
        self._financial_metrics_cache = {}
        self._line_items_cache = {}
        self._insider_trades_cache = {}
        self._company_news_cache = {}
        if self._cache_file.exists():
            self._cache_file.unlink()
        print("[cache] Cache cleared.")

    def _merge_data(self, existing: list[dict] | None, new_data: list[dict], key_field: str) -> list[dict]:
        """Merge existing and new data, avoiding duplicates based on a key field."""
        if not existing:
            return new_data

        # Create a set of existing keys for O(1) lookup
        existing_keys = {item[key_field] for item in existing}

        # Only add items that don't exist yet
        merged = existing.copy()
        merged.extend([item for item in new_data if item[key_field] not in existing_keys])
        return merged

    def get_prices(self, ticker: str) -> list[dict[str, any]] | None:
        """Get cached price data if available."""
        return self._prices_cache.get(ticker)

    def set_prices(self, ticker: str, data: list[dict[str, any]]):
        """Append new price data to cache."""
        self._prices_cache[ticker] = self._merge_data(self._prices_cache.get(ticker), data, key_field="time")
        self.save()

    def get_financial_metrics(self, ticker: str) -> list[dict[str, any]]:
        """Get cached financial metrics if available."""
        return self._financial_metrics_cache.get(ticker)

    def set_financial_metrics(self, ticker: str, data: list[dict[str, any]]):
        """Append new financial metrics to cache."""
        self._financial_metrics_cache[ticker] = self._merge_data(self._financial_metrics_cache.get(ticker), data, key_field="report_period")
        self.save()

    def get_line_items(self, ticker: str) -> list[dict[str, any]] | None:
        """Get cached line items if available."""
        return self._line_items_cache.get(ticker)

    def set_line_items(self, ticker: str, data: list[dict[str, any]]):
        """Append new line items to cache."""
        self._line_items_cache[ticker] = self._merge_data(self._line_items_cache.get(ticker), data, key_field="report_period")
        self.save()

    def get_insider_trades(self, ticker: str) -> list[dict[str, any]] | None:
        """Get cached insider trades if available."""
        return self._insider_trades_cache.get(ticker)

    def set_insider_trades(self, ticker: str, data: list[dict[str, any]]):
        """Append new insider trades to cache."""
        self._insider_trades_cache[ticker] = self._merge_data(self._insider_trades_cache.get(ticker), data, key_field="filing_date")  # Could also use transaction_date if preferred
        self.save()

    def get_company_news(self, ticker: str) -> list[dict[str, any]] | None:
        """Get cached company news if available."""
        return self._company_news_cache.get(ticker)

    def set_company_news(self, ticker: str, data: list[dict[str, any]]):
        """Append new company news to cache."""
        self._company_news_cache[ticker] = self._merge_data(self._company_news_cache.get(ticker), data, key_field="date")
        self.save()


# Global cache instance
_cache = Cache()


def get_cache() -> Cache:
    """Get the global cache instance."""
    return _cache
