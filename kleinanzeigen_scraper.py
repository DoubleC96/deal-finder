"""
Kleinanzeigen Scraper — bruges KUN til prisreference, ikke til køb.
Priser i EUR.
"""
import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from playwright.async_api import async_playwright

BASE_URL = "https://www.kleinanzeigen.de/s-{query}/k0"


@dataclass
class Listing:
    title: str
    price: int
    location: str
    url: str
    source: str = "kleinanzeigen"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


def _parse_price(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text.split(",")[0])
    return int(digits) if digits else 0


async def scrape(query: str, max_pages: int = 3) -> list[Listing]:
    results: list[Listing] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )

        for page_num in range(1, max_pages + 1):
            slug = query.replace(" ", "-").lower()
            url  = BASE_URL.format(query=slug)
            if page_num > 1:
                url = url.replace("/k0", f"/seite:{page_num}/k0")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector("article.aditem", timeout=10_000)
            except Exception:
                break

            items = await page.query_selector_all("article.aditem")
            if not items:
                break

            for item in items:
                try:
                    title_el = await item.query_selector("h2.text-module-begin")
                    price_el = await item.query_selector("p.aditem-main--middle--price-shipping--price")
                    loc_el   = await item.query_selector("div.aditem-main--top--left")
                    link_el  = await item.query_selector("a[href]")

                    title    = (await title_el.inner_text()).strip() if title_el else ""
                    price    = _parse_price(await price_el.inner_text() if price_el else "0")
                    location = (await loc_el.inner_text()).strip() if loc_el else ""
                    href     = await link_el.get_attribute("href") if link_el else ""
                    url_full = f"https://www.kleinanzeigen.de{href}" if href and href.startswith("/") else href

                    if title and price:
                        results.append(Listing(title=title, price=price, location=location, url=url_full))
                except Exception:
                    continue

        await browser.close()
    return results
