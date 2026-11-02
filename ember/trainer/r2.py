"""Presigned Cloudflare R2 URLs, signed with a key typed into the page.

The 🎭 Face LoRA page's Publish uploads the trained file to the private
builds bucket itself, because the licence server's R2 key is read-only and
stays that way (license-validator/src/r2.js). The key it signs with is
typed in for one publish and never stored, logged or read from the
environment — so this module takes it as an argument and nothing else.

A third implementation of the same SigV4 query signing, beside
scripts/r2_presign.py (the build machine's) and the licence server's. It
is not shared with the first because scripts/ is not part of the shipped
binary, and not with the second because that one is JavaScript; each
holds a different credential, which is the point of keeping them apart.
"""

import hashlib
import hmac
import time
import urllib.parse

ALGORITHM = "AWS4-HMAC-SHA256"
REGION = "auto"
SERVICE = "s3"


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode(), hashlib.sha256).digest()


def _encode(value: str, keep_slash: bool = False) -> str:
    """RFC 3986, which is what SigV4 signs over ("/" kept only in paths)."""
    return urllib.parse.quote(value, safe="/" if keep_slash else "")


def presign(method: str, key: str, expires: int, *, account: str,
            access_key: str, secret: str, bucket: str,
            now: str | None = None) -> str:
    """A presigned URL for one object in `bucket`."""
    host = f"{account}.r2.cloudflarestorage.com"
    amz_date = now or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    date_stamp = amz_date[:8]
    scope = f"{date_stamp}/{REGION}/{SERVICE}/aws4_request"
    query = sorted(
        (_encode(name), _encode(value))
        for name, value in (
            ("X-Amz-Algorithm", ALGORITHM),
            ("X-Amz-Credential", f"{access_key}/{scope}"),
            ("X-Amz-Date", amz_date),
            ("X-Amz-Expires", str(expires)),
            ("X-Amz-SignedHeaders", "host"),
        )
    )
    canonical_query = "&".join(f"{name}={value}" for name, value in query)
    # Path style: R2 carries the bucket in the path, not the hostname.
    canonical_path = f"/{_encode(bucket)}/{_encode(key, keep_slash=True)}"
    canonical_request = "\n".join([
        method, canonical_path, canonical_query, f"host:{host}", "",
        "host", "UNSIGNED-PAYLOAD",
    ])
    string_to_sign = "\n".join([
        ALGORITHM, amz_date, scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signing_key = _hmac(f"AWS4{secret}".encode(), date_stamp)
    for part in (REGION, SERVICE, "aws4_request"):
        signing_key = _hmac(signing_key, part)
    signature = hmac.new(signing_key, string_to_sign.encode(),
                         hashlib.sha256).hexdigest()
    return (f"https://{host}{canonical_path}?{canonical_query}"
            f"&X-Amz-Signature={signature}")
