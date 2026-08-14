# Pinterest Fetch Bot

Telegram bot jo Pinterest se photos/videos search karke deta hai — do tareeke se:

1. **Direct search** — koi bhi text bhejo (ya `/search <query>`), bot pehla
   result photo/video ke roop mein bhejta hai, saath mein **⬅️ Prev / Next ➡️**
   aur **📥 Original** (best quality) buttons.
2. **Inline mode** — kisi bhi chat mein `@your_bot_username query` type karo,
   Telegram ek native gallery-style picker dikhata hai; tap karte hi wahi
   media us chat mein bhej diya jaata hai.

Search [`py3-pinterest`](https://github.com/bstoilov/py3-pinterest) library
se hoti hai — ye actively maintained hai (v2.0.0, search bugs explicitly fix
kiye gaye hain). Pagination se ek se zyada page fetch karke usable results
collect kiye jaate hain, aur photo/video dono automatically results mein aa
jaate hain (Pinterest videos ko bhi "pins" hi treat karta hai).

## Files

| File | Purpose |
|---|---|
| `bot.py` | Entry point — handlers + webhook server |
| `config.py` | Sab env vars yahin se read hote hain |
| `database.py` | MongoDB (Motor) — users, search sessions |
| `pinterest_service.py` | Pinterest search wrapper — schema-agnostic URL extraction + pagination |
| `login_service.py` | Real Pinterest email/password login (owner-only `/login`) |
| `Dockerfile` | Render Web Service ke liye |
| `requirements.txt` | Dependencies |
| `.env.example` | Sab env vars ka reference |

## Deploy on Render (Docker Web Service)

1. Is folder ko apne GitHub repo mein push karo.
2. Render → **New +** → **Web Service** → apna repo select karo → environment
   **Docker** choose karo.
3. Environment tab mein set karo:
   - `BOT_TOKEN` — BotFather se
   - `MONGO_URI` — MongoDB connection string
   - Baaki optional (`.env.example` dekho)
4. Deploy karo. Boot hote hi bot apna webhook Telegram par set kar leta hai.
5. BotFather mein **Inline Mode** on karo (`/setinline`) taaki `@bot query`
   wala picker kaam kare.

## Real login (`/login`, owner-only)

Search **login ke bina fully kaam karta hai**. `/login` sirf tab use karo
agar tumhe authenticated session chahiye (jaise private/personalized
results). Ye `py3-pinterest` ke built-in Selenium (headless Chrome) login ka
use karta hai — **iske liye Chrome container mein hona zaroori hai**, jo
default Dockerfile mein install nahi hai (image size chhota rakhne ke liye).

Agar `/login` chalana hai:
1. `Dockerfile` mein commented-out Chrome install block ko uncomment karo.
2. `PINTEREST_EMAIL`, `PINTEREST_PASSWORD`, `PINTEREST_USERNAME` env vars set
   karo.
3. Redeploy karke owner account se `/login` bhejo.

Cookies `cred_root` mein cache hote hain (~15 din tak valid), lekin Render
ka filesystem ephemeral hai — har naye deploy/restart ke baad dobara login
karna padega.

## Admin commands (owner-only)

Hardcoded owner IDs: `6518065496`, `1598576202` (extend via `EXTRA_OWNER_IDS`).

- `/stats` — total users, total searches
- `/login` — Pinterest login try karta hai (upar dekho)

## Debugging

Agar search results khaali aayein, Render env vars mein `LOG_LEVEL=DEBUG`
set karo aur redeploy karo — logs mein "First raw pin keys" line dikhegi
jisse pata chalega Pinterest asal mein kya bhej raha hai.

## Notes

- Media Telegram ko seedha URL se serve kiya jaata hai — Render instance par
  CPU/RAM/disk load kam rehta hai.
- Search sessions MongoDB mein 24 ghante TTL cache hote hain taaki Prev/Next/
  Download baar-baar Pinterest ko na hit karein.
- Ye ek unofficial Pinterest client use karta hai — Pinterest ke Terms of
  Service se conflict ho sakta hai; apni responsibility par use karo.
