# 🔍 Deal Finder — DBA & Kleinanzeigen

## Filer
| Fil | Beskrivelse |
|-----|-------------|
| `dba_scraper.py` | Scraper til dba.dk |
| `kleinanzeigen_scraper.py` | Prisreference fra kleinanzeigen.de |
| `currency.py` | Live EUR/DKK kurs |
| `database.py` | SQLite database |
| `main.py` | FastAPI backend |
| `scheduler.py` | Automatisk scraping + emails |
| `login.html` | Login-side |
| `index.html` | Hoved-frontend |
| `requirements.txt` | Python-pakker |
| `Dockerfile` | Til Railway deployment |

## Miljøvariabler (Railway → Variables)
| Variabel | Beskrivelse |
|----------|-------------|
| `LOGIN_USERNAME` | Dit brugernavn |
| `LOGIN_PASSWORD` | Dit kodeord |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | Din Gmail |
| `SMTP_PASSWORD` | Gmail app-password |

## Næste skridt
- [x] Email-notifikationer ved nye deals
- [x] Prishistorik og graf over pristrends
- [x] Automatisk valutaomregning (EUR → DKK)
- [x] Login så kun du kan tilgå siden
- [ ] Mobilapp (PWA)
