"""Opt-in live probe that prints coverage, never raw pages, keys or product values."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json

from .config import ConfigurationError, read_context_api_key
from .context_provider import ContextProvider, ProviderError, ScrapeResult


def coverage(result: ScrapeResult) -> dict:
    """A redacted capability report, not checkout verification or normalized evidence."""
    data = result.product.data if result.product.success else {}
    product = data.get("product") if data.get("isProductPage") is True else None
    product = product if isinstance(product, dict) else {}
    return {
        "product_success": result.product.success,
        "markdown_success": result.markdown.success,
        "product_name_present": bool(product.get("name")),
        "price_present": product.get("price") is not None,
        "currency_present": product.get("currency") is not None,
        "availability_present": product.get("availability") is not None,
        "cache_age_ms": result.cache_age_ms,
        "observed_at": result.observed_at.isoformat() if result.observed_at else None,
        "provider_request_id_present": result.request_id is not None,
        "limitations": ["Provider product data does not verify FI shipping, tax or checkout stock."],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product_url", help="A public HTTPS product page that permits access")
    args = parser.parse_args()
    report: dict = {"kind": "live", "run_at": datetime.now(timezone.utc).isoformat(), "checks": []}
    try:
        api_key = read_context_api_key()
    except ConfigurationError as error:
        print(json.dumps({**report, "status": "NOT RUN", "reason": str(error)}))
        return 2
    if not api_key:
        print(json.dumps({**report, "status": "NOT RUN", "reason": "CONTEXT_DEV_API_KEY is not configured"}))
        return 2
    provider = ContextProvider(api_key)
    passed = True
    for label, url in (("auth_smoke", "https://example.com"), ("product", args.product_url)):
        try:
            result = provider.scrape(url)
            item = {"check": label, "coverage": coverage(result)}
            has_product = item["coverage"]["product_name_present"] and item["coverage"]["price_present"]
            passed = passed and (result.markdown.success if label == "auth_smoke" else has_product)
        except ProviderError as error:
            item = {"check": label, "error": error.code, "http_status": error.status}
            passed = False
        report["checks"].append(item)
    report["status"] = "completed" if passed else "extraction_limited_or_failed"
    print(json.dumps(report, default=lambda obj: str(obj) if isinstance(obj, Decimal) else None))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
