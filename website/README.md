# Shop website (CAPONE.CC storefront)

Flask site that shows the Telegram shop bot's listings (cities → categories → products → variants → districts →
checkout) and lets visitors register, buy with a site balance and leave reviews. It reads and writes the bot's
`data.json`, so Global Admin and the bot see the same products, stock, purchases and site users.

## Run
```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
SITE_BOT_DIR=/path/to/bot SITE_BOT_NAME=test2 ./venv/bin/gunicorn -b 127.0.0.1:8090 -w 2 webapp:app
```
`SITE_BOT_DIR` must contain the bot's `data_store.py`, `settings_store.py`, `crypto_rates.py`, `data.json`,
`menu_config.json` and `.env`. Put it behind nginx with HTTPS and set `SITE_SECURE=1`.

Assets: `static/site.css`, `static/gift.svg` (placeholder product picture), `templates/*.html`.
The foil background is CSS only; upload real pictures through Global Admin (website settings / product form).
