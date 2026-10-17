"""Simple HTTP wrapper; 429/timeout/failure causes rejection, never default approval."""
from __future__ import annotations
import json, math, urllib.request, urllib.error
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

class RemoteError(RuntimeError):
    """HTTP/remote failure; preserve status and Retry-After for safe backoff."""
    def __init__(self, message, *, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _retry_after_seconds(value):
    """Accept either numeric seconds or RFC 7231 HTTP-date, never an infinite wait."""
    if not value:
        return None
    try:
        seconds=float(value)
        return max(0.0,seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError, OverflowError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0.0, (target-datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError, IndexError):
            return None

def get_json(url: str, timeout: float=6) -> dict | list:
    try:
        req=urllib.request.Request(url, headers={'User-Agent': 'SolanaHunterV1/0.1', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read(5_000_000).decode())
    except urllib.error.HTTPError as exc:
        raise RemoteError(f'GET HTTP {exc.code}',status=exc.code,
                          retry_after=_retry_after_seconds(exc.headers.get('Retry-After'))) from exc
    except urllib.error.URLError as exc:
        status=403 if '403 Forbidden' in str(exc.reason) else None
        raise RemoteError('GET network/proxy failure',status=status) from exc
    except Exception as exc:
        raise RemoteError(f'GET failed: {type(exc).__name__}') from exc

def post_json(url: str, payload: dict, timeout: float=6) -> dict:
    try:
        req=urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'User-Agent':'SolanaHunterV1/0.1','Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data=json.loads(response.read(5_000_000).decode())
        if 'error' in data:
            error=data['error']
            code=error.get('code') if isinstance(error,dict) else None
            raise RemoteError(f'RPC error {code}',status=429 if code in (429,-32429) else None)
        return data
    except RemoteError:
        raise
    except urllib.error.HTTPError as exc:
        # Solana's shared RPC endpoints send HTTP 429 and sometimes Retry-After.
        # Avoid retry storms and preserve the status for the RPC client's
        # per-method cooldown. Never include the endpoint/API key in diagnostics.
        raise RemoteError(f'POST HTTP {exc.code}', status=exc.code,
                          retry_after=_retry_after_seconds(exc.headers.get('Retry-After') if exc.headers else None)) from exc
    except urllib.error.URLError as exc:
        status=403 if '403 Forbidden' in str(exc.reason) else None
        raise RemoteError('POST network/proxy failure',status=status) from exc
    except Exception as exc:
        raise RemoteError(f'POST failed: {type(exc).__name__}') from exc
