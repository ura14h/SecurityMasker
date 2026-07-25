"""Gateway readiness probe shared by ``run`` and ``doctor`` (doc/06 P2-1).

One implementation, so the wrapper and the diagnostic can never disagree about
whether the gateway is usable. The probe is deliberately strict: an HTTP 200 is
not enough, because the transparent development mode also answers requests. Only
``ready: true`` — which the gateway returns after checking that a masking engine
is configured AND the session store responds — counts as protected.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

_TIMEOUT = 5.0


@dataclass(frozen=True)
class Readiness:
    ok: bool
    detail: str  # secret-free, safe to print and log


def check_readiness(gateway: str, *, timeout: float = _TIMEOUT) -> Readiness:
    """Probe ``<gateway>/ready``. Never raises; failures are reported as ``ok=False``."""
    url = gateway.rstrip("/") + "/ready"
    try:
        resp = httpx.get(url, timeout=timeout)
    except httpx.HTTPError as exc:
        return Readiness(False, f"gateway unreachable at {gateway} ({type(exc).__name__})")

    if resp.status_code != 200:
        reason = ""
        try:
            body = resp.json()
            if isinstance(body, dict) and isinstance(body.get("reason"), str):
                reason = f": {body['reason']}"
        except ValueError:
            reason = ""
        return Readiness(False, f"gateway not ready (HTTP {resp.status_code}{reason})")

    try:
        body = resp.json()
    except ValueError:
        return Readiness(False, "gateway /ready did not return JSON")
    if not (isinstance(body, dict) and body.get("ready") is True):
        # Includes the dev transparent mode, which is up but masks nothing.
        return Readiness(False, "gateway is running but reports it is not ready to mask")
    return Readiness(True, f"gateway ready at {gateway}")
