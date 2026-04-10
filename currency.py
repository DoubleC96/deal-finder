"""
Live valutaomregning EUR → DKK via frankfurter.app (gratis, ingen API-nøgle).
Kursen caches i 6 timer.
"""
import time
import logging
import urllib.request
import json

log = logging.getLogger(__name__)

_cache: dict = {"rate": 7.46, "fetched_at": 0.0}
CACHE_TTL = 6 * 3600


def get_eur_dkk() -> float:
    now = time.time()
    if now - _cache["fetched_at"] < CACHE_TTL:
        return _cache["rate"]
    try:
        url = "https://api.frankfurter.app/latest?from=EUR&to=DKK"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        rate = float(data["rates"]["DKK"])
        _cache["rate"] = rate
        _cache["fetched_at"] = now
        log.info(f"Valutakurs opdateret: 1 EUR = {rate:.4f} DKK")
        return rate
    except Exception as e:
        log.warning(f"Kunne ikke hente valutakurs ({e}) — bruger {_cache['rate']:.4f}")
        return _cache["rate"]
