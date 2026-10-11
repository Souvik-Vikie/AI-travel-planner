"""Small helpers shared by tools: disk cache, deterministic randomness, geo."""
import hashlib
import json
import math
import os
import random
import time

from ..config import settings


def stable_rng(*parts) -> random.Random:
    """Deterministic RNG so mock data is reproducible across runs (important for eval)."""
    seed = int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cache_get(namespace: str, key: dict, ttl: float | None = None):
    """Cached JSON for `key`; if `ttl` (seconds) is given, older entries count as a miss."""
    path = _cache_path(namespace, key)
    if os.path.exists(path) and (ttl is None or time.time() - os.path.getmtime(path) < ttl):
        with open(path) as f:
            return json.load(f)
    return None


def cache_put(namespace: str, key: dict, value) -> None:
    path = _cache_path(namespace, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(value, f)


def _cache_path(namespace: str, key: dict) -> str:
    h = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
    return os.path.join(settings.cache_dir, namespace, f"{h}.json")
