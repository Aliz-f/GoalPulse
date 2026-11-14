"""Deterministic, offline extractor (D2-GI-02 "mock provider").

A small rule-based parser for English goal descriptions. It exists so tests
and CI never call a model, and so the demo still works without an API key.
It is deliberately conservative: when a phrase is ambiguous it leaves the field
empty and the compiler asks. It never invents a budget, product or country.
"""

from __future__ import annotations

import re

from ..domain.extraction import ExtractionPatch, ExtractionRequest, ItemPatch

_WORD_NUMBERS = {
    "one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a couple of": 2, "a pair of": 2,
}
_NUM_WORD_RE = r"(?:one|two|three|four|five|six|seven|eight|nine|ten)"

_UNSUPPORTED = [
    (r"\b(flights?|fly to|airfare|hotels?|trip|travel|holiday|vacation|train tickets?|cruise)\b", "Travel"),
    (r"\b(job|jobs|hiring|employment|apartment to rent|rental flat)\b", "Job or housing search"),
    (r"\b(stocks?|shares|crypto|bitcoin|invest(ing|ment)?)\b", "Investing"),
    (r"\b(concert|tickets? for|event tickets?)\b", "Event tickets"),
]
_PROCUREMENT = r"\b(monthly|every month|per month|each month|a month|/month|supplies|restock|re-?order|caf[eé]|restaurant|our (shop|kitchen|office))\b"
_PRODUCT = r"\b(buy|purchase|get me|looking for|want|need|find me|shopping for|i'?d like)\b"

_CURRENCY = {
    "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "e": "EUR",
    "$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD",
    "£": "GBP", "gbp": "GBP", "pound": "GBP", "pounds": "GBP",
    "sek": "SEK", "kr": "SEK", "nok": "NOK",
}
_AMOUNT = r"\d{1,3}(?:[ ,.]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_CUR_BEFORE = re.compile(r"(€|\$|£|\beur\b|\busd\b|\bgbp\b|\bsek\b|\bnok\b)\s*(" + _AMOUNT + r")", re.I)
_CUR_AFTER = re.compile(
    r"(?<![\d.,])(" + _AMOUNT + r")\s*(€|\$|£|eur\b|euros?\b|e\b|usd\b|dollars?\b|gbp\b|pounds?\b|sek\b|kr\b|nok\b)",
    re.I,
)
_CURRENT_CTX = re.compile(r"(currently|right now|now|spend|spending|pay|paying|costs? us|at the moment|today)\W*\w*\W*\w*\W*$")
_MAX_CTX = re.compile(r"(under|below|max(imum)?|at most|budget|target|no more than|up to|less than|cap|within)\b")

_COUNTRIES = [
    (r"\b(finland|suomi|helsinki|espoo|vantaa|tampere|turku|oulu|jyv[aä]skyl[aä])\b", "FI"),
    (r"\b(sweden|stockholm)\b", "SE"), (r"\b(estonia|tallinn)\b", "EE"),
    (r"\b(germany|berlin)\b", "DE"), (r"\b(norway|oslo)\b", "NO"),
    (r"\b(usa|united states|america)\b", "US"), (r"\b(uk|united kingdom|england|london)\b", "GB"),
]

_UNITS = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "l": "L", "ltr": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L",
    "pcs": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs", "x": "pcs", "units": "pcs",
}
_AMBIGUOUS_UNITS = r"packs?|packages?|bags?|boxes?|bottles?|cases?|cartons?|crates?"
_ITEM_RE = re.compile(
    r"(?<![€$£\d.,])(\d+(?:[.,]\d+)?)\s*(kgs?|kilos?|kilograms?|ltr|l|litres?|liters?|pcs|pc|pieces?|units|x|"
    + _AMBIGUOUS_UNITS
    + r")?\.?\s+(?:of\s+)?([a-z][a-z\-' ]{1,60}?)(?=\s*(?:,|;|\.|!|\?|\band\b|\bper\b|\beach\b|\bevery\b|\ba month\b|"
    r"\bmonthly\b|\bfrom\b|\bfor\b|\bwe\b|\bat\b|\bunder\b|$))",
    re.I,
)
_NOT_ITEMS = re.compile(
    r"^(euros?|eur|percent|months?|days?|weeks?|hours?|minutes?|sellers?|shops?|times?|%)\b|"
    r"^(kgs?|kilos?|kilograms?|ltr|l|litres?|liters?|pcs|pc|pieces?|units)$"
)

_YES = re.compile(r"^\s*(yes|yep|yeah|yup|sure|ok|okay|sounds good|looks good|perfect|confirm(ed)?|go ahead|do it|correct|that'?s right|great|fine|approved?)\b", re.I)
_NO = re.compile(r"^\s*(no|nope|nah|not really)\b", re.I)
_AUTO_BUY = re.compile(
    r"(auto(matic(ally)?)?[- ]?(buy|purchase|order|checkout)|(buy|order|purchase) it (for me|automatically)|"
    r"place the order|check ?out for me|just buy|go ahead and (buy|order))",
    re.I,
)

_QUERY_RE = re.compile(
    r"\b(?:buy|purchase|get(?: me)?|find(?: me)?|looking for|shopping for|want|need|track|like)\s+"
    r"(?:to\s+(?:buy|get|purchase|find)\s+)?"
    r"(?:(?:a|an|the|one|some|a new|a used|" + _NUM_WORD_RE + r"|\d{1,4})\s+(?:x\s+)?)?"
    r"(.+?)(?=\s+(?:for|under|below|at|max|maximum|with|that|which|delivered|shipped|to|in|if|when|but|by|"
    r"costing|priced|and\s+(?:it|i|deliver|ship|want|need))\b|\s+(?:€|\$|£)|[,;!?]|\.(?:\s|$)|$)",
    re.I,
)


def _num(token: str) -> int | None:
    token = token.lower().strip()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token)


class FixtureExtractor:
    model_id = "fixture-rules-v1"

    def extract(self, request: ExtractionRequest, *, timeout_s: float = 0) -> ExtractionPatch:  # noqa: ARG002
        msg = request.message.strip()
        t = msg.lower()
        asked = set(request.asked_fields)
        p: dict = {}

        self._classify(t, request, asked, p)
        goal_type = p.get("classification") if p.get("classification") in (
            "product_purchase", "recurring_procurement") else request.goal_type

        if _AUTO_BUY.search(t):
            p["auto_purchase_requested"] = True

        self._money(t, asked, goal_type, p)
        self._delivery(t, asked, p)

        if goal_type == "product_purchase":
            self._product(msg, t, asked, p)
        elif goal_type == "recurring_procurement":
            self._procurement(t, asked, request.item_names, p)

        self._monitoring(t, p)
        if "summary_approval" in asked and _YES.search(t) and not re.search(r"\b(but|except|change|instead)\b", t):
            p["approve_summary"] = True
        return ExtractionPatch.model_validate(p)

    # ------------------------------------------------------------------ pieces

    @staticmethod
    def _classify(t: str, request: ExtractionRequest, asked: set[str], p: dict) -> None:
        if request.goal_type in ("product_purchase", "recurring_procurement"):
            return
        for pattern, reason in _UNSUPPORTED:
            if re.search(pattern, t):
                p["classification"], p["unsupported_reason"] = "unsupported", reason
                return
        if re.search(_PROCUREMENT, t) or ("supported_goal_type" in asked and re.search(r"\bsuppl", t)):
            p["classification"] = "recurring_procurement"
        elif re.search(_PRODUCT, t) or ("supported_goal_type" in asked and re.search(r"\bproduct\b", t)):
            p["classification"] = "product_purchase"
        else:
            p["classification"] = "unclear"

    @staticmethod
    def _money(t: str, asked: set[str], goal_type: str | None, p: dict) -> None:
        hits: list[tuple[int, str, str]] = []  # (position, amount, currency)
        for m in _CUR_BEFORE.finditer(t):
            hits.append((m.start(), m.group(2), _CURRENCY.get(m.group(1).lower(), m.group(1).upper())))
        for m in _CUR_AFTER.finditer(t):
            if any(abs(m.start() - h[0]) < 3 for h in hits):
                continue
            hits.append((m.start(), m.group(1), _CURRENCY.get(m.group(2).lower(), m.group(2).upper())))
        money_asked = asked & {"max_total_eur_cents", "current_total_eur_cents"}
        if not hits and money_asked:
            # A bare number answers a question that explicitly asked for euros.
            m = re.search(r"(?<![\d%])(" + _AMOUNT + r")(?!\s*%)", t)
            if m and not re.search(r"\b(kg|l|litres?|pcs)\b", t):
                hits.append((m.start(), m.group(1), "EUR"))
        non_eur = sorted({c for _, _, c in hits if c != "EUR"})
        if non_eur:
            p["currency_seen"] = non_eur[0]
        for pos, amount, currency in sorted(hits):
            if currency != "EUR":
                p.setdefault("max_total_eur", amount)  # flagged by currency_seen; compiler refuses it
                continue
            ctx = t[max(0, pos - 32):pos]
            is_current = bool(_CURRENT_CTX.search(ctx)) and not _MAX_CTX.search(ctx)
            if not is_current and not _MAX_CTX.search(ctx) and money_asked == {"current_total_eur_cents"}:
                is_current = True
            if goal_type == "recurring_procurement" and is_current:
                p.setdefault("current_total_eur", amount)
            else:
                p.setdefault("max_total_eur", amount)
        m = re.search(r"(?:save|cut|reduce|lower|trim)\D{0,30}?(\d+(?:[.,]\d+)?)\s*(?:%|percent)", t) or re.search(
            r"(\d+(?:[.,]\d+)?)\s*(?:%|percent)\s*(?:less|cheaper|savings?|lower|off)", t
        )
        if m:
            p["savings_percent"] = m.group(1).replace(",", ".")

    @staticmethod
    def _delivery(t: str, asked: set[str], p: dict) -> None:
        for pattern, code in _COUNTRIES:
            if re.search(pattern, t):
                p["delivery_country"] = code
                break
        if re.search(r"\b(excl(\.|uding)?|without|plus|\+|not including)\s+(the\s+)?(shipping|delivery|postage|taxe?s?|vat)\b", t):
            p["include_shipping_and_tax"] = False
        elif re.search(
            r"\b(incl(\.|uding)?|with|includes?)\s+(the\s+)?(shipping|delivery|postage|taxe?s?|vat)\b|\bdelivered\b|"
            r"(shipping|delivery|tax(es)?|vat)\s+(and\s+\w+\s+)?included|all[- ]in",
            t,
        ):
            p["include_shipping_and_tax"] = True
        if "include_shipping_and_tax" in asked and "include_shipping_and_tax" not in p:
            if _YES.search(t):
                p["include_shipping_and_tax"] = True
                p.setdefault("delivery_country", "FI")  # the question named Finland explicitly
            elif _NO.search(t):
                p["include_shipping_and_tax"] = False

    @staticmethod
    def _product(msg: str, t: str, asked: set[str], p: dict) -> None:
        # what
        query = None
        if "product_query" in asked:
            query = re.sub(r"^(i want|i need|i'?d like|it'?s|it is|the|a|an)\s+", "", msg.strip(), flags=re.I)
            query = re.split(r"\s+(?:for|under|below|max)\s+|[,;!?]|\.(?:\s|$)", query)[0]
        else:
            m = _QUERY_RE.search(msg)
            if m:
                query = m.group(1)
        if query:
            query = query.strip(" .'\"")
            if len(query) >= 3 and not re.fullmatch(r"[\d\s.,€$£]+", query) and not re.match(r"^(to|it|that|this|one)\b", query, re.I):
                p["product_query"] = query

        # how many
        m = re.search(r"\b(?:buy|purchase|get|need|want|find)\s+(?:me\s+)?(\d{1,4}|" + _NUM_WORD_RE + r")\s+(?:x\s+)?\w", t)
        if m:
            p["quantity"] = _num(m.group(1))
        elif re.search(r"\b(?:buy|purchase|get|need|want|find|like)\s+(?:me\s+)?(?:a|an|one|a new|a used)\s+\w", t):
            p["quantity"] = 1
        if "quantity" in asked and "quantity" not in p:
            m = re.search(r"\b(\d{1,4}|" + _NUM_WORD_RE + r"|single)\b", t)
            if m:
                p["quantity"] = _num(m.group(1))

        # condition
        if re.search(r"\b(used|refurb(ished)?|second[- ]hand|pre[- ]owned)\b[^.]{0,20}\b(fine|ok|okay|good|too|also|works|acceptable)\b|any condition|condition doesn'?t matter|new or used", t):
            p["condition"] = "any"
        elif re.search(r"\b(brand[- ]new|new only|only new|must be new|has to be new|new condition|sealed)\b", t):
            p["condition"] = "new"
        elif "product_condition" in asked:
            if re.search(r"\b(used|refurb|either|any|doesn'?t matter|whatever)\b", t):
                p["condition"] = "any"
            elif re.search(r"\bnew\b", t):
                p["condition"] = "new"

        # exact vs equivalent
        if re.search(r"\b(equivalents?|similar|comparable|or better|any brand|alternatives?)\b", t):
            p["match_policy"] = "equivalent_allowed"
        elif re.search(r"\b(exact(ly)?( this| that| the same)? model|only (this|that) model|exactly)\b", t):
            p["match_policy"] = "exact_model"
        elif "match_policy" in asked:
            if re.search(r"\b(exact|only|precisely)\b", t):
                p["match_policy"] = "exact_model"
            elif re.search(r"\b(either|any|whatever|fine|ok|okay)\b", t):
                p["match_policy"] = "equivalent_allowed"

    @staticmethod
    def _procurement(t: str, asked: set[str], item_names: list[str], p: dict) -> None:
        items: list[ItemPatch] = []
        for m in _ITEM_RE.finditer(t):
            name = re.sub(r"\s+", " ", m.group(3)).strip(" -'")
            if _NOT_ITEMS.match(name) or len(name) < 2:
                continue
            tail = t[m.end():m.end() + 6]
            if re.match(r"\s*(€|eur|%)", tail):
                continue
            raw_unit = (m.group(2) or "").lower()
            unit = _UNITS.get(raw_unit)
            items.append(ItemPatch(name=name[0].upper() + name[1:], quantity=m.group(1), unit=unit))

        # "milk 20 L" style answers for items we already know
        if "items.quantity_unit" in asked:
            for known in item_names:
                key = known.lower().split()[-1]
                m = re.search(re.escape(key) + r"\D{0,20}?(\d+(?:[.,]\d+)?)\s*(kgs?|kilos?|ltr|l|litres?|liters?|pcs|pieces?|units)?\b", t)
                if m and not any(i.name.lower().endswith(key) for i in items):
                    items.append(ItemPatch(name=known, quantity=m.group(1), unit=_UNITS.get((m.group(2) or "").lower())))
        if items:
            p["items"] = items

        if "items.requirements" in asked:
            if re.fullmatch(r"\s*(none|no|nope|nothing|no requirements|nothing special|any|anything)\W*\s*", t):
                p["item_requirements"] = {"*": []}
            else:
                p["item_requirements"] = _requirements(t, item_names)

        m = re.search(r"(?:at most|max(?:imum)?|up to|no more than)\s+(\d+|" + _NUM_WORD_RE + r")\s+(?:different\s+)?(?:sellers|shops|suppliers|stores|vendors)", t)
        if m:
            p["max_distinct_sellers"] = _num(m.group(1))
        elif re.search(r"\b(one|single|a single)\s+(seller|supplier|shop|store|vendor)\b", t):
            p["max_distinct_sellers"] = 1

    @staticmethod
    def _monitoring(t: str, p: dict) -> None:
        m = re.search(r"\b(?:check|look|scan|monitor|search)\w*\b[^.]{0,20}?\bevery\s+(\d+)?\s*(minute|min|hour|hr|day)s?\b", t)
        if m:
            n = int(m.group(1) or 1)
            p["frequency_minutes"] = n * {"minute": 1, "min": 1, "hour": 60, "hr": 60, "day": 1440}[m.group(2)]
        elif re.search(r"\b(check|look|monitor)\w*\b[^.]{0,20}\bhourly\b", t):
            p["frequency_minutes"] = 60
        elif re.search(r"\btwice a day\b", t):
            p["frequency_minutes"] = 720
        m = re.search(r"\b(?:evidence|data|prices?)\b[^.]{0,25}?(?:older than|at most|max(?:imum)?|up to|within)\s+(\d+)\s*(minute|min|hour|hr|day)s?\b", t)
        if m:
            p["max_evidence_age_minutes"] = int(m.group(1)) * {"minute": 1, "min": 1, "hour": 60, "hr": 60, "day": 1440}[m.group(2)]
        if re.search(r"\b(don'?t|do not|no need to)\s+notify\b|\bno notifications?\b", t):
            p["notify_user"] = False


_FILLER = re.compile(r"\b(for|the|should|must|be|has|have|to|needs?|it|they|are|is|we|want|would|like|please|only)\b")


def _requirements(t: str, item_names: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for segment in re.split(r"[;\n]|\.(?:\s|$)", t):
        segment = segment.strip()
        if not segment:
            continue
        key = "*"
        body = segment
        if ":" in segment:
            left, _, body = segment.partition(":")
            key = left.strip()
        else:
            for name in item_names:
                words = [w for w in re.findall(r"[a-z]+", name.lower()) if len(w) >= 3]
                hit = next((w for w in words if re.search(rf"\b{w}\b", segment)), None)
                if hit:
                    key = name
                    body = re.sub(rf"\b{hit}\b", " ", segment)
                    break
        body = _FILLER.sub(" ", body)
        reqs = [r.strip(" -") for r in re.split(r",|\band\b", body) if r.strip(" -")]
        if reqs:
            out.setdefault(key, []).extend(" ".join(r.split()) for r in reqs)
    return out
