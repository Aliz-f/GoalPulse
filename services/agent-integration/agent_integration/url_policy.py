"""Reject recognized credentials in retained source URLs; never fetch URLs."""
import re
import unicodedata
from urllib.parse import parse_qsl, unquote_plus, urlsplit

_SENSITIVE = {
    'token', 'accesstoken', 'refreshtoken', 'idtoken', 'authtoken', 'authorization', 'auth',
    'apikey', 'password', 'passwd', 'pwd', 'secret', 'clientsecret', 'credential', 'credentials',
    'accesskey', 'accesskeyid', 'secretkey', 'session', 'sessionid', 'sid', 'jsessionid', 'phpsessid',
    'signature', 'sig', 'oauthverifier', 'authorizationcode', 'paymenttoken', 'paymentmethodid',
    'cardnumber', 'ccnumber', 'cvv', 'cvc', 'cardsecuritycode',
}
_SUFFIXES = ('token', 'apikey', 'clientsecret', 'password', 'credential', 'credentials',
             'secretkey', 'securitykey', 'sessionid', 'signature')


def has_credentials(value: str) -> bool:
    """Reject rather than redact: changing a query could change the actual offer.

    Names are case/punctuation/Unicode normalized and decoded up to three times.
    Fragment parameters are checked too, including OAuth-style fragments. Product
    selectors such as sku, variant, size and color retain their exact values.
    This recognizes credential-bearing parameters; opaque secrets under arbitrary
    unrelated names still require D3 source/provenance controls.
    """
    try:
        parts = urlsplit(value)
        for component in (parts.query, parts.fragment):
            for key, parameter in parse_qsl(component, keep_blank_values=True, errors='strict'):
                for _ in range(3):
                    decoded = unquote_plus(key, errors='strict')
                    if decoded == key:
                        break
                    key = decoded
                if '%' in key or any(ord(c) < 32 for c in key):
                    return True
                name = re.sub('[^a-z0-9]', '', unicodedata.normalize('NFKC', key).casefold())
                if name in _SENSITIVE or name.endswith(_SUFFIXES) or parameter.lstrip().casefold().startswith('bearer '):
                    return True
        return False
    except (ValueError, UnicodeError):
        return True
