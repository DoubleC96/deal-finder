"""
FastAPI backend — scraper DBA og Kleinanzeigen, viser resultater fra begge,
og beregner deal-score baseret på det samlede gennemsnit på tværs af begge platforme.

Lokalt:  uvicorn main:app --reload --port 8000
"""
import asyncio
import os
import secrets
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Query, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

import database as db
from currency import get_eur_dkk
from dba_scraper import scrape as dba_scrape
from kleinanzeigen_scraper import scrape as ka_scrape

log = logging.getLogger(__name__)

ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
LOGIN_USERNAME = os.getenv("LOGIN_USERNAME", "admin")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "skift-mig!")
COOKIE_NAME    = "deal_session"

_valid_tokens: set[str] = set()

app = FastAPI(title="Deal Finder", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Auth ────────────────────────────────────────────────────────────────────

def _get_token(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME)

def require_auth(request: Request):
    token = _get_token(request)
    if not token or token not in _valid_tokens:
        raise HTTPException(status_code=401, detail="Ikke logget ind")


# ─── Modeller ────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class SearchRequest(BaseModel):
    query: str
    max_price: Optional[int] = None
    min_discount: float = 25.0

class SearchResponse(BaseModel):
    query: str
    total: int
    deals: int
    combined_avg_dkk: Optional[float]
    eur_dkk_rate: float
    dba_count: int
    ka_count: int
    listings: list[dict]
    errors: list[str]

class NotificationRequest(BaseModel):
    email: str
    query: str
    min_discount: float = 25.0
    max_price: Optional[int] = None


# ─── Hjælpefunktioner ────────────────────────────────────────────────────────

def _deal_score(price_dkk: int, avg_dkk: float) -> float:
    """Hvor mange procent er prisen under det samlede gennemsnit (0-100)."""
    if avg_dkk <= 0 or price_dkk <= 0:
        return 0.0
    discount = (avg_dkk - price_dkk) / avg_dkk * 100
    return round(max(0.0, min(100.0, discount)), 1)


# ─── Login ───────────────────────────────────────────────────────────────────

@app.get("/login")
async def login_page():
    return FileResponse("login.html")

@app.post("/api/login")
async def login(req: LoginRequest, response: Response):
    if req.username != LOGIN_USERNAME or req.password != LOGIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Forkert brugernavn eller kodeord")
    token = secrets.token_urlsafe(32)
    _valid_tokens.add(token)
    response.set_cookie(key=COOKIE_NAME, value=token, httponly=True,
                        samesite="lax", secure=False, max_age=60*60*24*30)
    return {"ok": True}

@app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = _get_token(request)
    if token:
        _valid_tokens.discard(token)
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


# ─── Sider ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root(request: Request):
    token = _get_token(request)
    if not token or token not in _valid_tokens:
        return RedirectResponse("/login")
    return FileResponse("index.html")


# ─── API ─────────────────────────────────────────────────────────────────────

@app.get("/api/rate", dependencies=[Depends(require_auth)])
async def exchange_rate():
    rate = get_eur_dkk()
    return {"eur_dkk": rate, "updated_at": datetime.now().isoformat()}


@app.post("/api/search", response_model=SearchResponse, dependencies=[Depends(require_auth)])
async def search(req: SearchRequest):
    rate = get_eur_dkk()
    errors: list[str] = []

    # Scrape begge platforme parallelt
    dba_result, ka_result = await asyncio.gather(
        dba_scrape(req.query),
        ka_scrape(req.query),
        return_exceptions=True,
    )

    dba_items = []
    ka_items  = []

    if isinstance(dba_result, Exception):
        errors.append(f"DBA fejl: {str(dba_result)[:100]}")
        log.error(f"DBA scrape fejl: {dba_result}")
    else:
        dba_items = dba_result

    if isinstance(ka_result, Exception):
        errors.append(f"Kleinanzeigen fejl: {str(ka_result)[:100]}")
        log.error(f"KA scrape fejl: {ka_result}")
    else:
        ka_items = ka_result

    # Saml alle priser i DKK for at beregne fælles gennemsnit
    all_prices_dkk: list[float] = []
    for item in dba_items:
        if item.price > 0:
            all_prices_dkk.append(item.price)
    for item in ka_items:
        if item.price > 0:
            all_prices_dkk.append(item.price * rate)  # EUR → DKK

    combined_avg = sum(all_prices_dkk) / len(all_prices_dkk) if all_prices_dkk else 0.0

    # Byg resultatliste fra BEGGE platforme
    listings: list[dict] = []

    for item in dba_items:
        score = _deal_score(item.price, combined_avg)
        row = {
            "title":      item.title,
            "price":      item.price,
            "price_dkk":  item.price,
            "currency":   "DKK",
            "location":   item.location,
            "url":        item.url,
            "source":     "dba",
            "query":      req.query,
            "deal_score": score,
            "created_at": item.created_at,
        }
        listings.append(row)
        db.upsert_listing(row)

    for item in ka_items:
        price_dkk = round(item.price * rate)
        score = _deal_score(price_dkk, combined_avg)
        row = {
            "title":      item.title,
            "price":      item.price,
            "price_dkk":  price_dkk,
            "currency":   "EUR",
            "location":   item.location,
            "url":        item.url,
            "source":     "kleinanzeigen",
            "query":      req.query,
            "deal_score": score,
            "created_at": item.created_at,
        }
        listings.append(row)
        db.upsert_listing(row)

    # Filtrer på max_price (i DKK)
    if req.max_price:
        listings = [l for l in listings if l["price_dkk"] <= req.max_price]

    db.save_search(req.query, "dba+kleinanzeigen", req.max_price, req.min_discount)

    dba_prices = [l["price_dkk"] for l in listings if l["source"] == "dba" and l["price_dkk"] > 0]
    db.record_price_history(req.query, dba_prices, combined_avg / rate if combined_avg else 0, rate)

    deals = [l for l in listings if l["deal_score"] >= req.min_discount]
    listings_sorted = sorted(listings, key=lambda x: x["deal_score"], reverse=True)

    return SearchResponse(
        query=req.query,
        total=len(listings),
        deals=len(deals),
        combined_avg_dkk=round(combined_avg) if combined_avg else None,
        eur_dkk_rate=rate,
        dba_count=len(dba_items),
        ka_count=len(ka_items),
        listings=listings_sorted,
        errors=errors,
    )


@app.get("/api/history", dependencies=[Depends(require_auth)])
async def price_history(query: str, days: int = 30):
    return db.get_price_history(query, days)

@app.post("/api/notify", dependencies=[Depends(require_auth)])
async def set_notification(req: NotificationRequest):
    nid = db.save_notification(req.email, req.query, req.min_discount, req.max_price)
    return {"ok": True, "id": nid,
            "message": f"Du får email til {req.email} når der er nye deals på '{req.query}'"}

@app.get("/api/listings", dependencies=[Depends(require_auth)])
async def get_listings(
    query: Optional[str] = None,
    max_price: Optional[int] = None,
    only_deals: bool = False,
    limit: int = Query(default=100, le=500),
):
    return db.get_listings(query=query, source=None, max_price=max_price,
                           only_deals=only_deals, limit=limit)

@app.get("/api/searches", dependencies=[Depends(require_auth)])
async def get_searches():
    return db.get_searches()

@app.get("/api/stats", dependencies=[Depends(require_auth)])
async def stats():
    all_items = db.get_listings(limit=10_000)
    return {
        "total_listings": len(all_items),
        "total_deals":    sum(1 for l in all_items if l["deal_score"] > 25),
        "eur_dkk_rate":   get_eur_dkk(),
        "updated_at":     datetime.now().isoformat(),
    }
