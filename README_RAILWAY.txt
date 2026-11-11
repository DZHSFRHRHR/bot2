Railway one-service fix: Telegram Local Bot API + Telegram bot
=============================================================

This package fixes "Cannot access uploaded file. Check the local API shared
volume" by running both processes in ONE Docker container with a common
filesystem. Your bot's interface remains in English with Search and Clean.
The bot excludes credential-like and other sensitive content from exports.

FILES TO DEPLOY TO RAILWAY:
  Dockerfile
  start.sh
  bot.py
  requirements.txt

1. Edit bot.py: put a NEW, private token in TOKEN = "...".
   OWNER_ID is set to your Telegram user ID.
   Never publish a bot token in a public repo; use private code storage.

2. Railway: select your bot service and deploy a GitHub repository that
   contains all four files at the REPOSITORY ROOT (or deploy from CLI).
   Confirm Railway says "Using detected Dockerfile" in its build logs.

3. Railway -> Service -> Variables: set TELEGRAM_API_ID and
   TELEGRAM_API_HASH. Get them privately at https://my.telegram.org/apps.
   These are not your bot token and cannot be replaced by it.

4. In Railway Settings, remove any old custom Start Command such as
   "python bot.py". Let Dockerfile use start.sh, which starts both processes.
   Do not generate a public domain for this worker.

5. For large files attach a Railway Volume to THIS SAME service at /data.
   Set its capacity large enough to hold the uploaded file, temporary data,
   and result file simultaneously; e.g. >= 10 GB recommended for 2GB inputs
   and similarly sized results if your account supports this capacity.
   Railway's plan/storage limits still apply.

6. Redeploy; in logs, expect:
   "Local Bot API listening on 127.0.0.1:8081"
   "Starting owner-only bot with embedded Local Bot API"

7. If switching this bot from Telegram's cloud Bot API to your own local
   server, first call logOut on the cloud Bot API ONCE using your current
   token. Do this privately, NOT in a public URL/log, e.g. with curl on
   your own terminal.

NOTE:
- Telegram local Bot API supports up to 2000MB per outgoing document and
  no API-imposed file download limit; available disk space and hosting plan
  limits still matter. This project splits large result files.
- With normal cloud Bot API, getFile downloads are limited to 20MB.
- This bundle is designed for Railway Dockerfile builds, NOT `python bot.py`
  alone, and NOT Railway auto-import of docker-compose.yml.
- Never run secret-bearing scripts or uploads from unknown sources.
