"""Sandbox-only Lithic ASA registration and HMAC secret retrieval.

Reads LITHIC_API_KEY from environment. Never stores it in files.
Do not print the ASA HMAC secret to terminal: use shell command substitution.
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import urlparse
from lithic_client import lithic_request, LithicError


def main():
    parser = argparse.ArgumentParser(description="Configure Lithic SANDBOX ASA")
    sub = parser.add_subparsers(dest="command", required=True)
    enroll = sub.add_parser("enroll", help="Enroll public HTTPS ASA endpoint")
    enroll.add_argument("url", help="https://<tunnel>.trycloudflare.com/lithic/asa")
    sub.add_parser("status", help="Check enrolled ASA URL")
    sub.add_parser("secret", help="Write ASA HMAC secret only to standard output for shell export")
    args = parser.parse_args()
    try:
        if args.command == "enroll":
            url = urlparse(args.url)
            if url.scheme != "https" or not url.netloc or url.username or url.password or url.fragment or url.path != "/lithic/asa":
                parser.error("Use public HTTPS URL ending exactly /lithic/asa")
            result = lithic_request("POST", "/responder_endpoints", payload={"type": "AUTH_STREAM_ACCESS", "url": args.url})
            print("Lithic sandbox ASA endpoint enrolled. Check with 'python lithic_setup.py status'.")
        elif args.command == "status":
            response = lithic_request("GET", "/responder_endpoints?type=AUTH_STREAM_ACCESS")
            print(json.dumps(response, indent=2))
        else:
            response = lithic_request("GET", "/auth_stream/secret")
            secret = response.get("secret", "")
            if not secret:
                raise LithicError("No ASA HMAC secret returned")
            sys.stdout.write(secret)
    except LithicError as e:
        print(f"Setup failed: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
