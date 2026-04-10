"""
Scheduler — scraper hvert 30. min, sender emails og gemmer prishistorik.

Miljøvariabler (Railway → Variables):
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
"""
import asyncio, logging, os, smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import database as db
from currency import get_eur_dkk
from dba_scraper import scrape as dba_scrape
from kleinanzeigen_scraper import scrape as ka_scrape

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM     = os.getenv("SMTP_FROM", SMTP_USER)


def _deal_score(price, ka_avg_eur, rate):
    if ka_avg_eur <= 0 or price <= 0: return 0.0
    return round(max(0.0, min(100.0, (ka_avg_eur * rate - price) / (ka_avg_eur * rate) * 100)), 1)


def send_email(to, subject, html):
    if not SMTP_USER or not SMTP_PASSWORD:
        log.warning("SMTP ikke konfigureret."); return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject; msg["From"] = SMTP_FROM; msg["To"] = to
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls(); s.login(SMTP_USER, SMTP_PASSWORD); s.sendmail(SMTP_FROM, to, msg.as_string())
        log.info(f"Email → {to}"); return True
    except Exception as e:
        log.error(f"Email fejl: {e}"); return False


def build_email(query, deals, ka_avg_eur, rate):
    rows = "".join(f"""<tr>
      <td style="padding:10px;border-bottom:1px solid #2a2d3e">{d['title']}</td>
      <td style="padding:10px;border-bottom:1px solid #2a2d3e"><strong style="color:#22c55e">{d['price']:,} kr</strong></td>
      <td style="padding:10px;border-bottom:1px solid #2a2d3e">🔥 {d['deal_score']:.0f}% under snit</td>
      <td style="padding:10px;border-bottom:1px solid #2a2d3e"><a href="{d['url']}" style="color:#6c63ff">Se →</a></td>
    </tr>""" for d in deals[:10])
    return f"""<html><body style="background:#0f1117;color:#e2e8f0;font-family:sans-serif;padding:24px">
      <h2 style="color:#6c63ff">🔍 {len(deals)} nye deals — {query}</h2>
      <p>KA-reference: <strong>{ka_avg_eur:.0f} EUR ({round(ka_avg_eur*rate):,} kr)</strong> | Kurs: 1 EUR = {rate:.4f} DKK</p>
      <table style="width:100%;border-collapse:collapse;background:#1a1d27">
        <thead><tr style="background:#22263a">
          <th style="padding:10px;text-align:left">Titel</th><th style="padding:10px;text-align:left">Pris</th>
          <th style="padding:10px;text-align:left">Deal</th><th style="padding:10px;text-align:left">Link</th>
        </tr></thead><tbody>{rows}</tbody>
      </table>
    </body></html>"""


async def run_all_searches():
    rate = get_eur_dkk()
    log.info(f"Kurs: 1 EUR = {rate:.4f} DKK")
    seen = set()
    for search in db.get_searches():
        query = search["query"]
        if query in seen: continue
        seen.add(query)
        log.info(f"Scraper: '{query}'")
        dba_res, ka_res = await asyncio.gather(dba_scrape(query), ka_scrape(query), return_exceptions=True)
        dba_items = dba_res if not isinstance(dba_res, Exception) else []
        ka_items  = ka_res  if not isinstance(ka_res,  Exception) else []
        ka_prices = [l.price for l in ka_items if l.price > 0]
        ka_avg    = sum(ka_prices)/len(ka_prices) if ka_prices else 0
        dba_prices, new = [], 0
        for item in dba_items:
            row = {"title": item.title, "price": item.price, "currency": "DKK",
                   "location": item.location, "url": item.url, "source": "dba",
                   "query": query, "deal_score": _deal_score(item.price, ka_avg, rate),
                   "created_at": item.created_at}
            if db.upsert_listing(row): new += 1
            if item.price > 0: dba_prices.append(item.price)
        db.record_price_history(query, dba_prices, ka_avg, rate)
        log.info(f"  → {new} nye | KA: {ka_avg:.0f} EUR")
        with db.get_conn() as conn:
            conn.execute("UPDATE searches SET last_run = ? WHERE id = ?", (datetime.now().isoformat(), search["id"]))

    for notif in db.get_active_notifications():
        deals = db.get_unnotified_deals(notif["query"], notif["min_discount"], notif["max_price"])
        if not deals: continue
        ka_est = deals[0]["price"] / max(1 - deals[0]["deal_score"]/100, 0.01) / rate
        if send_email(notif["email"], f"🔥 {len(deals)} nye deals — {notif['query']}",
                      build_email(notif["query"], deals, ka_est, rate)):
            db.mark_notified([d["id"] for d in deals])


async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_all_searches, "interval", minutes=30)
    scheduler.start()
    log.info("Scheduler kører — hvert 30. min. Ctrl+C for at stoppe.")
    await run_all_searches()
    try:
        while True: await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
