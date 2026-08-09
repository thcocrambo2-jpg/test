#!/usr/bin/env python3
"""Print a presigned Cloudflare R2 URL. Used by build.sh to upload a build.

R2's time-limited-URL API is its S3-compatible one, so this is AWS SigV4.
The name is the protocol's: nothing here talks to AWS and no AWS account
is involved. Region is always the literal "auto".

This is deliberately a *second* implementation of the same algorithm that
license-validator/src/r2.js has, rather than something shared. The two
sides hold different credentials on purpose:

    this one    R2 token with Object Read & Write, on the build machine
    the API's   R2 token with Object Read only, on the public web service

Sharing code would be a small win; sharing the credential would mean the
public service could overwrite the binary customers download. Keeping them
apart is what makes the read-only half actually read-only.

    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUILDS_BUCKET

all come from the environment. Usage:

    r2_presign.py --key builds/<sha256>/krea2app [--method PUT]
                  [--expires 3600]
"""

import argparse
import hashlib
import hmac
import os
import sys
import time
import urllib.parse

ALGORITHM = "AWS4-HMAC-SHA256"
REGION = "auto"
SERVICE = "s3"


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode(), hashlib.sha256).digest()


def _uri_encode(value: str, keep_slash: bool = False) -> str:
    """RFC 3986 percent-encoding, which is what SigV4 signs over.

    quote()'s default safe set is "/" and it leaves the unreserved
    characters alone, which is exactly the rule — but the safe set has to
    be emptied for query components, where "/" is escaped like anything
    else. Getting this wrong yields SignatureDoesNotMatch and no hint.
    """
    return urllib.parse.quote(value, safe="/" if keep_slash else "")


def presign(method: str, key: str, expires: int, now: str | None = None,
            bucket: str | None = None) -> str:
    """A presigned URL for one object.

    `bucket` defaults to R2_BUILDS_BUCKET, which is what build.sh wants and
    is the only caller that existed first. It is a parameter because the
    builds bucket is not the only one: the showcase images live in their
    own public bucket, and that uploader signs with this same credential
    rather than growing a second copy of SigV4 (see r2_upload_showcase.py).
    """
    required = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    if bucket is None:
        required.append("R2_BUILDS_BUCKET")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"r2_presign: unset: {', '.join(missing)}")

    account = os.environ["R2_ACCOUNT_ID"]
    access_key = os.environ["R2_ACCESS_KEY_ID"]
    secret = os.environ["R2_SECRET_ACCESS_KEY"]
    bucket = bucket or os.environ["R2_BUILDS_BUCKET"]

    host = f"{account}.r2.cloudflarestorage.com"
    amz_date = now or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    date_stamp = amz_date[:8]
    scope = f"{date_stamp}/{REGION}/{SERVICE}/aws4_request"

    # Sorted by encoded name, as SigV4 requires.
    query = sorted(
        (_uri_encode(name), _uri_encode(value))
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
    canonical_path = f"/{_uri_encode(bucket)}/{_uri_encode(key, keep_slash=True)}"

    canonical_request = "\n".join(
        [
            method,
            canonical_path,
            canonical_query,
            f"host:{host}",
            "",            # blank line closing the canonical headers block
            "host",
            "UNSIGNED-PAYLOAD",
        ]
    )

    string_to_sign = "\n".join(
        [ALGORITHM, amz_date, scope, _sha256_hex(canonical_request)]
    )

    signing_key = _hmac(f"AWS4{secret}".encode(), date_stamp)
    for part in (REGION, SERVICE, "aws4_request"):
        signing_key = _hmac(signing_key, part)
    signature = hmac.new(
        signing_key, string_to_sign.encode(), hashlib.sha256
    ).hexdigest()

    return (
        f"https://{host}{canonical_path}?{canonical_query}"
        f"&X-Amz-Signature={signature}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, help="object key in the bucket")
    parser.add_argument("--method", default="PUT", choices=("GET", "PUT"))
    parser.add_argument("--expires", type=int, default=3600, help="seconds")
    parser.add_argument("--bucket",
                        help="bucket name (default: $R2_BUILDS_BUCKET)")
    # Only for checking this signer against the JS one, which cannot agree
    # on a signature unless both are handed the same timestamp.
    parser.add_argument("--date", help=argparse.SUPPRESS)
    args = parser.parse_args()

    print(presign(args.method, args.key, args.expires, args.date,
                  bucket=args.bucket))
    return 0


if __name__ == "__main__":
    sys.exit(main())
