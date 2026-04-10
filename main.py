"""
FastAPI backend med login, live valutakurs, prishistorik og email-notifikationer.

Lokalt:  uvicorn main:app --reload --port 8000
Docs:    http://localhost:8000/docs

Miljøvariabler:
    LOGIN_USERNAME  — dit brugernavn (standard: admin)
    LOGIN_PASSWORD  — dit kodeord (SKAL sættes i Railway!)
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD — til emails
"""
import asyncio
import os
import secrets
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Query, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import database as db
from currency import get_eur_dkk
from dba_scraper import scrape as dba_scrape
from kleinanzeigen_scraper import scrape as ka_scrape

ALLOWED_ORIGIN   = os.getenv("ALLOWED_ORIGIN", "*")
LOGIN_USERNAME   = os.getenv("LOGIN_USERNAME", "admin")
LOGIN_PASSWORD   = os.getenv("LOGIN_PASSWORD", "skift-mig!")   # SKAL ændres i Railway
COOKIE_NAME      = "deal_session"

# Gyldige session-tokens (i hukommelsen — nulstilles ved genstart)
_valid_tokens: set[str] = set()

app = FastAPI(title="Deal Finder", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Auth-hjælpefunktioner ───────────────────────────────────────────────────

def _get_token(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME)


def require_auth(request: Request):
    """Bruges som dependency på beskyttede endpoints."""
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
    ka_avg_price: Optional[float]
    ka_avg_dkk: Optional[float]
    ka_sample_size: int
    eur_dkk_rate: float
    listings: list[dict]


class NotificationRequest(BaseModel):
    email: str
    query: str
    min_discount: float = 25.0
    max_price: Optional[int] = None


# ─── Hjælpefunktioner ────────────────────────────────────────────────────────

def _deal_score(dba_price_dkk: int, ka_avg_eur: float, rate: float) -> float:
    if ka_avg_eur <= 0 or dba_price_dkk <= 0:
        return 0.0
    ka_dkk = ka_avg_eur * rate
    return round(max(0.0, min(100.0, (ka_dkk - dba_price_dkk) / ka_dkk * 100)), 1)


def _avg(prices: list[int]) -> float:
    return sum(prices) / len(prices) if prices else 0.0


# ─── Login endpoints ─────────────────────────────────────────────────────────

@app.get("/login")
async def login_page():
    return FileResponse("login.html")


@app.post("/api/login")
async def login(req: LoginRequest, response: Response):
    if req.username != LOGIN_USERNAME or req.password != LOGIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Forkert brugernavn eller kodeord")
    token = secrets.token_urlsafe(32)
    _valid_tokens.add(token)
    response.set_cookie(
        key=COOKIE_NAME, value=token,
        httponly=True,       # Ikke tilgængeligt fra JavaScript
        samesite="lax",
        secure=False,        # Sæt til True hvis du bruger HTTPS (Railway gør det automatisk)
        max_age=60 * 60 * 24 * 30,  # 30 dage
    )
    return {"ok": True}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = _get_token(request)
    if token:
        _valid_tokens.discard(token)
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


# ─── Beskyttede endpoints ────────────────────────────────────────────────────

@app.get("/")
async def root(request: Request):
    token = _get_token(request)
    if not token or token not in _valid_tokens:
        return RedirectResponse("/login")
    return FileResponse("index.html")


@app.get("/api/rate", dependencies=[Depends(require_auth)])
async def exchange_rate():
    rate = get_eur_dkk()
    return {"eur_dkk": rate, "updated_at": datetime.now().isoformat()}


@app.post("/api/search", response_model=SearchResponse, dependencies=[Depends(require_auth)])
async def search(req: SearchRequest):
    rate = get_eur_dkk()

    dba_result, ka_result = await asyncio.gather(
        dba_scrape(req.query),
        ka_scrape(req.query),
        return_exceptions=True,
    )
    dba_items = dba_result if not isinstance(dba_result, Exception) else []
    ka_items  = ka_result  if not isinstance(ka_result,  Exception) else []

    ka_prices = [l.price for l in ka_items if l.price > 0]
    ka_avg    = _avg(ka_prices)
    ka_sample = len(ka_prices)

    if ka_avg == 0:
        ka_avg  = _avg([l.price for l in dba_items if l.price > 0]) / rate
        ka_sample = 0

    listings: list[dict] = []
    for item in dba_items:
        score = _deal_score(item.price, ka_avg, rate)
        row = {
            "title": item.title, "price": item.price, "currency": "DKK",
            "location": item.location, "url": item.url, "source": "dba",
            "query": req.query, "deal_score": score, "created_at": item.created_at,
        }
        listings.append(row)
        db.upsert_listing(row)

    if req.max_price:
        listings = [l for l in listings if l["price"] <= req.max_price]

    db.save_search(req.query, "dba+kleinanzeigen_ref", req.max_price, req.min_discount)
    db.record_price_history(req.query, [l["price"] for l in listings if l["price"] > 0], ka_avg, rate)

    deals = [l for l in listings if l["deal_score"] >= req.min_discount]
    return SearchResponse(
        query=req.query, total=len(listings), deals=len(deals),
        ka_avg_price=round(ka_avg, 2) if ka_avg else None,
        ka_avg_dkk=round(ka_avg * rate) if ka_avg else None,
        ka_sample_size=ka_sample, eur_dkk_rate=rate,
        listings=sorted(listings, key=lambda x: x["deal_score"], reverse=True),
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
    return db.get_listings(query=query, source="dba", max_price=max_price,
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
