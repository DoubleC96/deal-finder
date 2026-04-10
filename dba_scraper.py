"""
DBA Scraper — henter annoncer fra dba.dk via Playwright.
"""
import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from playwright.async_api import async_playwright

BASE_URL = "https://www.dba.dk/soeg/?soeg={query}&pris_fra=&pris_til="


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
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        for page_num in range(1, max_pages + 1):
            url = BASE_URL.format(query=query.replace(" ", "+"))
            if page_num > 1:
                url += f"&side={page_num}"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector(".listingContainer", timeout=10_000)
            except Exception:
                break

            items = await page.query_selector_all(".listingContainer")
            if not items:
                break

            for item in items:
                try:
                    title_el = await item.query_selector(".listing-heading")
                    price_el = await item.query_selector(".listing-price")
                    loc_el   = await item.query_selector(".listing-region")
                    link_el  = await item.query_selector("a.listing-inner-container")

                    title    = (await title_el.inner_text()).strip() if title_el else ""
                    price    = _parse_price(await price_el.inner_text() if price_el else "0")
                    location = (await loc_el.inner_text()).strip() if loc_el else ""
                    href     = await link_el.get_attribute("href") if link_el else ""
                    url_full = f"https://www.dba.dk{href}" if href and href.startswith("/") else href

                    if title and price:
                        results.append(Listing(title=title, price=price, location=location, url=url_full))
                except Exception:
                    continue

        await browser.close()
    return results
