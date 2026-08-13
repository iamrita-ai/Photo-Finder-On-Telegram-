# Pinterest Fetch Bot

Telegram bot jo Pinterest se photos/videos search karke deta hai — do tareeke se:

1. **Direct search** — koi bhi text bhejo (ya `/search <query>`), bot pehla
   result photo/video ke roop mein bhejta hai, saath mein **⬅️ Prev / Next ➡️**
   aur **📥 Original** (best quality) buttons.
2. **Inline mode** — kisi bhi chat mein `@your_bot_username query` type karo,
   Telegram ek native gallery-style picker dikhata hai; tap karte hi wahi
   media us chat mein bhej diya jaata hai. Ye "web mein results, user choose
   kare" wala behaviour hai — bina custom webapp banaye, purely Telegram ke
   built-in inline results se.

Search public Pinterest endpoints se hoti hai (login ki zaroorat nahi) via
the [`pinterest-downloader`](https://github.com/x7007x/PinterestDownloader)
library. Ek optional, best-effort email/password `/login` command bhi diya
gaya hai (owner-only) — details neeche.

## Files

| File | Purpose |
|---|---|
| `bot.py` | Entry point — handlers + webhook server |
| `config.py` | Sab env vars yahin se read hote hain |
| `database.py` | MongoDB (Motor) — users, search sessions, cookies |
| `pinterest_service.py` | Pinterest search/fetch wrapper |
| `login_service.py` | Optional best-effort Pinterest email/password login |
| `Dockerfile` | Render Web Service ke liye |
| `requirements.txt` | Dependencies |
| `.env.example` | Sab env vars ka reference |

## Deploy on Render (Docker Web Service)

1. Is folder ko apne GitHub repo mein push karo.
2. Render → **New +** → **Web Service** → apna repo select karo → environment
   **Docker** choose karo (Dockerfile auto-detect ho jaayega).
3. Environment tab mein ye variables set karo:
   - `BOT_TOKEN` — BotFather se
   - `MONGO_URI` — MongoDB Atlas (ya kahin bhi) ka connection string
   - `MONGO_DB_NAME` (optional, default `pinterest_bot`)
   - `PINTEREST_EMAIL` / `PINTEREST_PASSWORD` (optional, sirf `/login` ke liye)
   - `EXTRA_OWNER_IDS` (optional, comma-separated extra admin IDs)

   `PORT` aur `WEBHOOK_URL` (via `RENDER_EXTERNAL_HOSTNAME`) Render khud set
   kar deta hai — inhe manually set karne ki zaroorat nahi.
4. Deploy karo. Boot hote hi bot apna webhook Telegram par set kar leta hai.
5. BotFather mein apne bot ke liye **Inline Mode** on karna mat bhoolna
   (`/setinline` in @BotFather) — warna `@bot query` wala picker kaam nahi
   karega.

## Admin commands (owner-only)

Hardcoded owner IDs: `6518065496`, `1598576202` (extend via `EXTRA_OWNER_IDS`).

- `/stats` — total users, total searches
- `/login` — Pinterest email/password se login try karta hai (neeche note dekho)

## Pinterest login — important note

Core search/fetch **login ke bina fully kaam karta hai** — Pinterest ke
public/unauthenticated endpoints use hote hain. `PINTEREST_EMAIL` /
`PINTEREST_PASSWORD` sirf tab set karo agar tum owner-only `/login` command
try karna chahte ho.

Ye login **best-effort** hai: Pinterest automated logins ko CSRF checks,
device fingerprinting, CAPTCHA aur 2FA se actively block karta hai, aur
unka internal login endpoint bina notice ke badal sakta hai. Agar `/login`
fail ho, bot crash nahi hota — bas fail message dikhata hai aur baaki sab
kaam karta rehta hai. Agar Pinterest apna endpoint change kar de, sirf
`login_service.py` update karna hoga.

## Notes

- Media Telegram ko seedha URL se serve kiya jaata hai (download-then-upload
  nahi) — isse Render instance par CPU/RAM/disk load kam rehta hai.
- Search sessions MongoDB mein 24 ghante ke liye cache hote hain (TTL index)
  taaki Prev/Next/Download baar-baar Pinterest ko na hit karein.
- Ye ek unofficial Pinterest scraper library use karta hai — Pinterest ke
  Terms of Service se conflict ho sakta hai; apni responsibility par use
  karo.
