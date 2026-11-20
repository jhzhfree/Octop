"""Exercise an Octop workload's OIDC credential against the gateway HTTP API."""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlsplit

import httpx


def trusted_url(value: str) -> str:
    url = urlsplit(value)
    if (
        not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or (
            url.scheme != "https"
            and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})
        )
    ):
        raise ValueError("Identity and gateway endpoints require HTTPS or loopback HTTP")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--gateway")
    parser.add_argument("--expect-gateway-status", type=int, default=200)
    args = parser.parse_args()

    try:
        issuer = trusted_url(args.issuer)
        gateway = trusted_url(args.gateway) if args.gateway else None
        secret = os.environ.get("OCTOP_GATEWAY_OIDC_CLIENT_SECRET")
        if not secret:
            raise ValueError("Set OCTOP_GATEWAY_OIDC_CLIENT_SECRET in the workload environment")
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            discovery = client.get(issuer.rstrip("/") + "/.well-known/openid-configuration")
            discovery.raise_for_status()
            metadata = discovery.json()
            if metadata["issuer"] != issuer:
                raise ValueError("Discovery issuer differs from the configured issuer")
            token_endpoint = trusted_url(metadata["token_endpoint"])
            if urlsplit(token_endpoint).netloc != urlsplit(issuer).netloc or (
                urlsplit(token_endpoint).scheme != urlsplit(issuer).scheme
            ):
                raise ValueError("Token endpoint differs from the configured issuer origin")
            issued = client.post(
                token_endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": args.client_id,
                    "client_secret": secret,
                },
            )
            issued.raise_for_status()
            payload = issued.json()
            token = payload["access_token"]
            if payload["token_type"].lower() != "bearer" or not isinstance(token, str) or not token:
                raise ValueError("Identity platform did not return a Bearer access token")
            print(
                "PASS Octop workload obtained an access token from the independent identity platform"
            )
            if gateway:
                response = client.get(
                    gateway.rstrip("/") + "/v1/agents",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status_code != args.expect_gateway_status:
                    raise ValueError(
                        f"Gateway returned HTTP {response.status_code}; "
                        f"expected {args.expect_gateway_status}"
                    )
                if response.status_code == 200 and not isinstance(response.json(), list):
                    raise ValueError("Gateway did not return the Agent discovery list")
                print(f"PASS gateway HTTP /v1/agents returned {response.status_code}")
    except httpx.HTTPStatusError as exc:
        print(f"Identity platform returned HTTP {exc.response.status_code}", file=sys.stderr)
        return 1
    except (httpx.RequestError, KeyError, TypeError, ValueError) as exc:
        message = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(f"Octop workload identity smoke failed ({message})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
