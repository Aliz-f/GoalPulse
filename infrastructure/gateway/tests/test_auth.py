import base64, hashlib, hmac, json, sys, uuid
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parents[1]))
from goalpulse_gateway.auth import AuthenticationError, HS256TokenVerifier, correlation_id, require_service_scope, AuthorizationError

SECRET = "a" * 32
SUBJECT = "ae000000-0000-4000-8000-000000000002"
def token(claims):
    header = {"alg":"HS256","typ":"JWT"}
    enc=lambda v: base64.urlsafe_b64encode(json.dumps(v,separators=(",",":")).encode()).rstrip(b"=").decode()
    head, body = enc(header), enc(claims)
    sig=base64.urlsafe_b64encode(hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"Bearer {head}.{body}.{sig}"
def verifier(kind="user"):
    return HS256TokenVerifier(secret=SECRET,issuer="goalpulse-dev",audience="goalpulse-api",token_kind=kind)
def test_valid_user_subject_is_trusted_not_header():
    principal=verifier().verify(token({"iss":"goalpulse-dev","aud":"goalpulse-api","sub":SUBJECT,"token_kind":"user","exp":200}),now=100)
    assert principal.subject == uuid.UUID(SUBJECT)
def test_invalid_expired_and_forged_tokens_are_rejected():
    claims={"iss":"goalpulse-dev","aud":"goalpulse-api","sub":SUBJECT,"token_kind":"user","exp":100}
    with pytest.raises(AuthenticationError): verifier().verify(token(claims),now=100)
    with pytest.raises(AuthenticationError): verifier().verify(token({**claims,"exp":200}) + "x",now=100)
    malformed_signature = token({**claims, "exp": 200}).rsplit(".", 1)[0] + ".a"
    with pytest.raises(AuthenticationError): verifier().verify(malformed_signature, now=100)
def test_service_scope_and_correlation_id_are_strict():
    service=verifier("service").verify(token({"iss":"goalpulse-dev","aud":"goalpulse-api","sub":SUBJECT,"token_kind":"service","scope":"gm.read","exp":200}),now=100)
    require_service_scope(service,"gm.read")
    with pytest.raises(AuthorizationError): require_service_scope(service,"gm.write")
    with pytest.raises(AuthenticationError): correlation_id("forged-header")
