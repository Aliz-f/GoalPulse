# Context.dev feasibility experiment — Developer 3

Official docs as of Nov 2026:
- https://docs.context.dev/quickstart
- https://docs.context.dev/monitors/overview

## Before building any provider adapter

1. Choose two legitimate public merchant pages with observable product descriptions.
2. Call `web.scrape` with `formats={"markdown": True}` using the **server-side** API key. Run optional `scripts/context_probe.py` (requires installing the Context.dev SDK and a key).
3. Identify whether actual **date- and location-relevant** price, stock and shipping to Finland are visible. Record each separately as true, false or unknown.
4. Record the underlying source URL and provider request ID. Inspect the documented `cache_metadata`; an HTTP 200 alone is not a reliable content success indicator. The `markdown.success` field also needs checking.
5. Confirm whether requested freshness meets Goal constraints. Context.dev may use cached data unless a fresh fetch is explicitly requested where supported.
6. Decide which pages can be tracked using a `page` monitor or a `search` monitor. Its initial run is a **baseline**, not a change event. Never wait for a change webhook to perform initial evaluation.
7. Report observed issues: dynamic content, missing delivery quote, login-only stock, anti-bot rejection, wrong variants, unknown taxes, inconsistent currency.

## Mapping to Observation v1

| Context/page field | Observation field | Missing-data policy |
|---|---|---|
| canonical page URL | `source_url` | Reject if no credible source URL |
| page scrape title/product metadata | `product_name`, `model` | Model null if not verifiable |
| visible price | `unit_price_eur_cents` | Cannot create price-bearing Observation if absent; record extraction failure elsewhere |
| stock message | `availability` | `unknown` if not seen |
| shipping to FI | `delivery_to_fi` | `unknown` if not explicit |
| delivery price | `shipping_eur_cents` | `null` if unknown (do not substitute zero) |
| taxes or VAT | `tax_eur_cents` | `null` if unknown (do not substitute zero) |
| request timestamp/provider metadata | `observed_at`, `provider_request_id` | Record source timestamp and provider metadata |
| excerpt or screenshot URL | `evidence` | No verified claim without supporting evidence |

Note: This v1 Observation schema is deliberately scoped to price-bearing product offers. Non-price evidence and travel-specific inventory need a separate v2 schema. Do **not** fabricate product offers from arbitrary pages.

## Go/no-go assessment

- **GO:** At least one genuinely accessible merchant gives a real product price and identifiable product variant; shipping and availability conditions can be conservatively marked `unknown` if needed.
- **CAUTION:** An apparently cheap price is only a search snippet or cached marketing copy — not verified checkout price.
- **NO-GO for this merchant:** No date-specific/product-specific price is accessible; choose another merchant or shift demo to a page type with verifiable signals.
