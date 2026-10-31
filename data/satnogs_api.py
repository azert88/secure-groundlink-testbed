"""Polite SatNOGS API client shared by the dataset scripts.

Practices:
- on-disk cache of every response, keyed by the SHA-256 of the request URL
  (the token is sent as a header, never part of the URL or the cache key);
- a minimum interval between real network requests (config: min_request_interval_s);
- exponential backoff on HTTP 429 / 5xx / connection errors, honouring Retry-After;
- cursor pagination by following the Link: <...>; rel="next" header;
- optional DB token read from a gitignored .env file, overridden by the environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}


def load_token(env_file: str | Path, var: str) -> str:
    """Return the token from the environment, else from a KEY=VALUE .env file, else ''."""
    if os.environ.get(var):
        return os.environ[var].strip()
    path = Path(env_file)
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == var:
            return value.strip().strip('"').strip("'")
    return ""


def next_link(link_header: str | None) -> str | None:
    """Extract the rel="next" URL from an RFC 8288 Link header."""
    if not link_header:
        return None
    for part in link_header.split(","):
        url_part, _, rel_part = part.partition(";")
        if 'rel="next"' in rel_part:
            return url_part.strip().strip("<>")
    return None


def build_url(base: str, params: dict | None = None) -> str:
    """Build a URL with sorted query parameters so the cache key is stable."""
    if not params:
        return base
    return base + "?" + urlencode(sorted((k, str(v)) for k, v in params.items()))


class SatnogsClient:
    """Rate-limited, cached HTTP client for the SatNOGS Network and DB APIs."""

    def __init__(self, cfg: dict, cache_dir: str | Path, token: str = "", session=None,
                 offline: bool = False):
        self.offline = offline  # True: serve only from the cache, never touch the network
        self.min_interval = float(cfg["min_request_interval_s"])
        self.max_retries = int(cfg["max_retries"])
        self.backoff_base = float(cfg["backoff_base_s"])
        self.backoff_max = float(cfg["backoff_max_s"])
        self.timeout = float(cfg["timeout_s"])
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = cfg["user_agent"]
        self._last_request = 0.0
        self.n_network_requests = 0
        self.n_cache_hits = 0

    # ---- low level -------------------------------------------------------------------------

    def _wait_turn(self) -> None:
        delay = self.min_interval - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)

    def _request(self, url: str, auth: bool, stream: bool = False) -> requests.Response:
        """GET with throttling and exponential backoff. Returns the final response (any status)."""
        headers = {"Authorization": f"Token {self.token}"} if auth and self.token else {}
        for attempt in range(self.max_retries + 1):
            self._wait_turn()
            try:
                resp = self.session.get(url, headers=headers, timeout=self.timeout, stream=stream)
            except requests.RequestException:
                resp = None
            self._last_request = time.monotonic()
            self.n_network_requests += 1
            if resp is not None and resp.status_code not in RETRY_STATUS:
                return resp
            if attempt == self.max_retries:
                if resp is None:
                    raise ConnectionError(f"no response after {attempt + 1} attempts: {url}")
                return resp
            delay = min(self.backoff_base * 2 ** attempt, self.backoff_max)
            retry_after = resp.headers.get("Retry-After") if resp is not None else None
            if retry_after and retry_after.isdigit():
                delay = max(delay, float(retry_after))
            time.sleep(delay)
        raise AssertionError("unreachable")

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def get(self, url: str, auth: bool = False, use_cache: bool = True) -> dict:
        """Cached GET. Returns {url, status, fetched_utc, next, text}.

        Successful responses and 404s are cached; other errors are not, so they are retried
        on the next run.
        """
        path = self._cache_path(url)
        if use_cache and path.exists():
            self.n_cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            raise RuntimeError(f"offline mode and not cached: {url}")
        resp = self._request(url, auth)
        entry = {
            "url": url,
            "status": resp.status_code,
            "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "next": next_link(resp.headers.get("Link")),
            "text": resp.text,
        }
        if use_cache and (resp.ok or resp.status_code == 404):
            path.write_text(json.dumps(entry), encoding="utf-8")
        return entry

    def get_json(self, url: str, params: dict | None = None, auth: bool = False):
        """Cached GET of a JSON endpoint; raises on HTTP errors."""
        entry = self.get(build_url(url, params), auth=auth)
        if entry["status"] != 200:
            raise RuntimeError(f"HTTP {entry['status']} for {entry['url']}")
        return json.loads(entry["text"])

    def iter_pages(self, url: str, params: dict | None = None, max_pages: int = 1,
                   auth: bool = False) -> Iterator[tuple[list, bool]]:
        """Yield (page_items, has_more) following Link rel="next", at most max_pages pages."""
        next_url = build_url(url, params)
        for _ in range(max_pages):
            entry = self.get(next_url, auth=auth)
            if entry["status"] != 200:
                raise RuntimeError(f"HTTP {entry['status']} for {entry['url']}")
            items = json.loads(entry["text"])
            next_url = entry["next"]
            yield items, next_url is not None
            if next_url is None:
                return

    def download(self, url: str, dest: str | Path, auth: bool = False) -> Path:
        """Stream a file to dest (written to a .part file first, then renamed)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = self._request(url, auth, stream=True)
        resp.raise_for_status()
        tmp = dest.with_name(dest.name + ".part")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
        tmp.replace(dest)
        return dest
