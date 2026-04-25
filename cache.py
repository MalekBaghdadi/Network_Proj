# In-memory cache with LRU eviction and TTL support

import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from logger import log_cache_hit, log_cache_miss, logger

DEFAULT_TTL       = 60                   
MAX_CACHE_BYTES   = 50 * 1024 * 1024     
MAX_ENTRY_BYTES   = 10 * 1024 * 1024      
CACHEABLE_METHODS = {"GET"}               


# Entry record
class _CacheEntry:
    """A single object in the cache."""

    def __init__(self, response_bytes: bytes, ttl: int):
        now                 = time.time()
        self.response_bytes = response_bytes
        self.stored_at      = now
        self.expires_at     = now + ttl
        self.size           = len(response_bytes)

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def ttl_remaining(self) -> int:
        """Seconds until this entry expires (0 if already stale)."""
        return max(0, int(self.expires_at - time.time()))


# Internal state
# OrderedDict is used to get cheap LRU behaviour:
#   - move_to_end(key)      → mark an entry as most-recently-used on a hit
#   - popitem(last=False)   → evict the oldest (least-recently-used) entry
_store: "OrderedDict[str, _CacheEntry]" = OrderedDict()
_store_lock = threading.Lock()

# Stats counters — all reads/writes are protected by _store_lock so that
# the /stats page on the admin dashboard sees a consistent snapshot.
_stats = {
    "hits":      0,
    "misses":    0,
    "stores":    0,
    "evictions": 0,
    "bytes":     0,  
}


# Helpers
def _cache_key(host: str, url: str) -> str:
    """Standardizes the URL to use as a key."""
    if url.startswith(("http://", "https://")):
        return url
    return f"http://{host}{url}"


def _parse_response_head(response_bytes: bytes):
    """Splits the status and headers from raw bytes."""
    try:
        head, _, _body = response_bytes.partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
        if not lines:
            return None, {}

        # Status line: "HTTP/1.1 200 OK"
        status_parts = lines[0].split(" ", 2)
        if len(status_parts) < 2:
            return None, {}
        status_code = int(status_parts[1])

        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
        return status_code, headers
    except Exception:
        return None, {}


def _extract_ttl(headers: dict):
    # cc: Cache-Control
    cc = headers.get("cache-control", "").lower()

    if cc:
        if "no-store" in cc or "private" in cc:
            return None

        m = re.search(r"s-maxage\s*=\s*(\d+)", cc)
        if m:
            return int(m.group(1))

        m = re.search(r"max-age\s*=\s*(\d+)", cc)
        if m:
            return int(m.group(1))

        if "no-cache" in cc:
            return None

    expires = headers.get("expires")
    if expires:
        try:
            exp_dt = parsedate_to_datetime(expires)
            if exp_dt is not None:
                # An HTTP-date without tzinfo is defined as GMT — treat as UTC.
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                delta = (exp_dt - datetime.now(timezone.utc)).total_seconds()
                if delta > 0:
                    return int(delta)
                return 0   # Expires already in the past → uncacheable
        except Exception:
            pass

    return -1   # nothing said — caller falls back to DEFAULT_TTL


def _evict_until_fits(incoming_size: int) -> None:
    """Pops oldest items from OrderedDict until there is room."""
    while _store and (_stats["bytes"] + incoming_size) > MAX_CACHE_BYTES:
        url, entry = _store.popitem(last=False)   # last=False → pop oldest
        _stats["bytes"]     -= entry.size
        _stats["evictions"] += 1
        logger.debug(f"CACHE EVICT | {url} ({entry.size} bytes)")


# Public API
def get(host: str, url: str, method: str):
    """Public API to fetch from cache."""
    if method not in CACHEABLE_METHODS:
        return None

    key = _cache_key(host, url)
    with _store_lock:
        entry = _store.get(key)

        if entry is None:
            _stats["misses"] += 1
            log_cache_miss(key)
            return None

        if entry.is_expired():
            # Stale → evict now and report as a miss so caller refetches.
            _store.pop(key, None)
            _stats["bytes"]  -= entry.size
            _stats["misses"] += 1
            log_cache_miss(key)
            return None

        # Fresh hit → promote to MRU (end of OrderedDict) for LRU eviction.
        _store.move_to_end(key)
        _stats["hits"] += 1
        log_cache_hit(key)
        return entry.response_bytes


def store(host: str, url: str, method: str, status_code, response_bytes: bytes) -> bool:
    """Public API to put into cache."""
    if method not in CACHEABLE_METHODS:
        return False
    if status_code is None or not (200 <= status_code < 300):
        key = _cache_key(host, url)
        logger.debug(f"CACHE SKIP (status={status_code}) | {key}")
        return False
    if len(response_bytes) > MAX_ENTRY_BYTES:
        key = _cache_key(host, url)
        logger.debug(f"CACHE SKIP (oversized {len(response_bytes)}B) | {key}")
        return False

    _, headers = _parse_response_head(response_bytes)
    ttl = _extract_ttl(headers)
    # ttl is None  → origin said no-store / no-cache / private
    # ttl == -1    → no caching directive at all
    # ttl <= 0     → origin said max-age=0 or Expires already past
    # In all these cases fall back to DEFAULT_TTL so repeated requests get a hit.
    if ttl is None or ttl <= 0:
        ttl = DEFAULT_TTL

    key   = _cache_key(host, url)
    entry = _CacheEntry(response_bytes, ttl)

    with _store_lock:
        # If this URL was already cached, drop the old copy first.
        old = _store.pop(key, None)
        if old is not None:
            _stats["bytes"] -= old.size

        _evict_until_fits(entry.size)

        _store[key] = entry
        _stats["bytes"]  += entry.size
        _stats["stores"] += 1

    logger.debug(f"CACHE STORE | {key} | ttl={ttl}s | size={entry.size}B")
    return True


# Admin helpers
def list_entries() -> list:
    """
    Snapshot of every cached entry, for the /cache page on the admin UI.
    Each dict is JSON-safe so Flask can jsonify it directly.
    """
    with _store_lock:
        return [
            {
                "url":           url,
                "size_bytes":    entry.size,
                "stored_at":     entry.stored_at,
                "ttl_remaining": entry.ttl_remaining(),
                "expired":       entry.is_expired(),
            }
            for url, entry in _store.items()
        ]


def purge(full_url: str) -> bool:
    """
    Remove a single cached entry by its full URL. Returns True if something
    was actually removed, False if the URL was not in the cache.
    """
    with _store_lock:
        entry = _store.pop(full_url, None)
        if entry is None:
            return False
        _stats["bytes"] -= entry.size
    logger.debug(f"CACHE PURGE | {full_url}")
    return True


def purge_all() -> int:
    """
    Clear every entry from the cache. Returns the number of entries removed.
    """
    with _store_lock:
        count = len(_store)
        _store.clear()
        _stats["bytes"] = 0
    logger.debug(f"CACHE PURGE ALL | removed={count}")
    return count


def stats() -> dict:
    """
    Current cache statistics, used by the dashboard's hit-rate chart.
    Returns a plain dict with all counters plus a derived hit_rate.
    """
    with _store_lock:
        total_lookups = _stats["hits"] + _stats["misses"]
        hit_rate = (_stats["hits"] / total_lookups) if total_lookups else 0.0
        return {
            "hits":      _stats["hits"],
            "misses":    _stats["misses"],
            "stores":    _stats["stores"],
            "evictions": _stats["evictions"],
            "entries":   len(_store),
            "bytes":     _stats["bytes"],
            "bytes_cap": MAX_CACHE_BYTES,
            "hit_rate":  hit_rate,
        }
