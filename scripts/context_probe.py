"""Optional Developer 3 smoke test (requires live API key and connectivity).

Install separately: python -m pip install context.dev
Run: CONTEXT_DEV_API_KEY=... python scripts/context_probe.py https://example.com
Only use public product pages that permit access; don't commit responses with secrets.
"""
import os
import sys

if len(sys.argv) != 2 or not sys.argv[1].startswith('https://'):
    raise SystemExit('Usage: python scripts/context_probe.py https://example.com')
if not os.environ.get('CONTEXT_DEV_API_KEY'):
    raise SystemExit('Set CONTEXT_DEV_API_KEY locally; never put it in git.')
from context.dev import ContextDev

client = ContextDev(api_key=os.environ['CONTEXT_DEV_API_KEY'])
result = client.web.scrape(
    url=sys.argv[1],
    formats={'markdown': True},
    shared_params={'main_content_only': True},
)
if not result.markdown.success:
    raise SystemExit('Scrape markdown output failed; inspect API response and cache metadata.')
print(result.markdown.data[:2000])
print('NOTE: Evaluate cache age, item price, availability, FI shipping, taxes and source provenance manually.')
