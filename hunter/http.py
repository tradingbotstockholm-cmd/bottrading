"""Simple HTTP wrapper; 429/timeout/failure causes rejection, never default approval."""
from __future__ import annotations
import json, urllib.request, urllib.error
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
        return max(0.0, min(300.0, float(value)))
    except (TypeError, ValueError, OverflowError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0.0, min(300.0, (target-datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError, IndexError):
            return None

def get_json(url: str, timeout: float=6) -> dict | list:
    try:
        req=urllib.request.Request(url, headers={'User-Agent': 'SolanaHunterV1/0.1', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read(5_000_000).decode())
    except Exception as exc:
        raise RemoteError(f'GET failed: {type(exc).__name__}: {exc}') from exc

def post_json(url: str, payload: dict, timeout: float=6) -> dict:
    try:
        req=urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'User-Agent':'SolanaHunterV1/0.1','Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data=json.loads(response.read(5_000_000).decode())
        if 'error' in data:
            raise RemoteError(f'RPC returned error: {data["error"]}')
        return data
    except RemoteError:
        raise
    except urllib.error.HTTPError as exc:
        # Solana's shared RPC endpoints send HTTP 429 and sometimes Retry-After.
        # Avoid retry storms and preserve the status for the RPC client's
        # per-method cooldown. Never include the endpoint/API key in diagnostics.
        try:
            body = exc.read(256).decode('utf-8', errors='replace')
        except Exception:
            body = ''
        raise RemoteError(f'POST HTTP {exc.code}: {body}', status=exc.code,
                          retry_after=_retry_after_seconds(exc.headers.get('Retry-After') if exc.headers else None)) from exc
    except Exception as exc:
        raise RemoteError(f'POST failed: {type(exc).__name__}: {exc}') from exc
