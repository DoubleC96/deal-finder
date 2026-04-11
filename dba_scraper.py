"""
DBA Scraper — henter annoncer fra dba.dk via Playwright.
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

log = logging.getLogger(__name__)
BASE_URL = "https://www.dba.dk/soeg/?soeg={query}"


@dataclass
class Listing:
    title: str
    price: int
    location: str
    url: str
    source: str = "dba"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


def _parse_price(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0


async def scrape(query: str, max_pages: int = 3) -> list[Listing]:
    results: list[Listing] = []
    log.info(f"DBA: starter scraping af '{query}'")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"]
        )
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()

        for page_num in range(1, max_pages + 1):
            url = BASE_URL.format(query=query.replace(" ", "+"))
            if page_num > 1:
                url += f"&side={page_num}"

            try:
                log.info(f"DBA: henter side {page_num} — {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector(".listingContainer, .listing-item", timeout=12_000)
            except PWTimeout:
                log.warning(f"DBA: timeout på side {page_num}")
                break
            except Exception as e:
                log.warning(f"DBA: fejl på side {page_num}: {e}")
                break

            # Prøv begge selector-varianter (DBA har ændret sig over tid)
            items = await page.query_selector_all(".listingContainer")
            if not items:
                items = await page.query_selector_all("article.listing-item")
            if not items:
                log.warning(f"DBA: ingen annoncer fundet på side {page_num}")
                break

            log.info(f"DBA: fandt {len(items)} annoncer på side {page_num}")

            for item in items:
                try:
                    # Prøv flere mulige selectors
                    title_el = await item.query_selector(".listing-heading, h2, .title")
                    price_el = await item.query_selector(".listing-price, .price")
                    loc_el   = await item.query_selector(".listing-region, .region, .location")
                    link_el  = await item.query_selector("a.listing-inner-container, a[href*='/annonce/']")

                    title    = (await title_el.inner_text()).strip() if title_el else ""
                    price    = _parse_price(await price_el.inner_text() if price_el else "0")
                    location = (await loc_el.inner_text()).strip() if loc_el else ""
                    href     = await link_el.get_attribute("href") if link_el else ""
                    url_full = f"https://www.dba.dk{href}" if href and href.startswith("/") else href

                    if title and price:
                        results.append(Listing(title=title, price=price, location=location, url=url_full))
                except Exception as e:
                    log.debug(f"DBA: kunne ikke parse annonce: {e}")
                    continue

        await browser.close()

    log.info(f"DBA: færdig — {len(results)} annoncer fundet")
    return results
