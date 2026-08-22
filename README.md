# Arkhashom Deals Bot — GitHub Actions Edition

Automated Amazon.eg deals scraper that posts to **@arkhashomoffers** on Telegram.

## Bots included
| Key | Category | Scraper |
|-----|----------|---------|
| fashion | Clothing, shoes, accessories | scraper_fashion.py |
| electronics | Laptops, phones, gaming, PC hardware | scraper_electronics.py |
| supermarket | Grocery, snacks, drinks | scraper_supermarket.py |
| home | Refrigerators, TVs, ACs, kitchen | scraper_home.py |
| beauty | Skincare, makeup, perfumes | scraper_beauty.py |
| promotions | Amazon PSP promotion pages | scraper_supermarket.py |

## Setup

### 1. Create a new GitHub repo
Go to github.com → New repository → name it `arkhashom-scraper` (private recommended).

### 2. Push this code
```bash
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USER/arkhashom-scraper.git
git push -u origin main
```

### 3. Add secrets
Go to repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `GEMINI_API_KEY` | `AIzaSyD2m4f43T4U9jTGigcOMkSI32q28aJSq6Y` |
| `BOT_TOKEN_FASHION` | `8765488684:AAHqf_5IlQTZZjTs9G6soR8ODoWLIRJaIsA` |
| `BOT_TOKEN_ELECTRONICS` | `8596878046:AAFNSlHMJkEVuCiuPESSmjvDTgmTWOvrtnM` |
| `BOT_TOKEN_SUPERMARKET` | `8743448704:AAHRUALIpuQGWAauFJO0XACuwIEMtozhjV8` |
| `BOT_TOKEN_HOME` | `8596878046:AAFNSlHMJkEVuCiuPESSmjvDTgmTWOvrtnM` |
| `BOT_TOKEN_BEAUTY` | `8882846026:AAGief-MJt_sxR8oEeqaOcchAiw8jMWMOTY` |
| `BOT_TOKEN_PROMOTIONS` | _(leave empty or add when ready)_ |
| `MAX_POSTS_PER_BOT` | `3` _(optional, default 3)_ |

### 4. Add cron-job.org trigger
URL:
```
https://api.github.com/repos/YOUR_USER/arkhashom-scraper/actions/workflows/arkhashom.yml/dispatches
```
Method: `POST`
Headers:
```
Authorization: Bearer YOUR_GITHUB_PAT
Accept: application/vnd.github+json
Content-Type: application/json
```
Body: `{"ref":"main"}`
Interval: every 10 minutes

### 5. Grant Actions write permission
Repo → Settings → Actions → General → Workflow permissions → **Read and write permissions** → Save

This allows the bot to commit state files back to the repo.

## How it works
1. Cron-job.org triggers the workflow every 10 min
2. `run_cloud.py` loops through all bots in `bots.json`
3. Each bot: discovers deals → posts up to MAX_POSTS_PER_BOT items → saves state
4. State files (posted history) are committed back to `data/`
5. Next run skips already-posted ASINs

## State files (in data/)
- `posted_history_{bot_key}.json` — tracks posted products
- `events_store_shared.json` — shared events
- `buttons_store_shared.json` — inline button config
- `promotions_{key}.json` — promotion tracking
