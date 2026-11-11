#!/usr/bin/env python3
"""Owner-only, large-file Telegram TXT search & ordinary text cleaner.

Deploy alongside Telegram Local Bot API on the same VPS. Files are processed
line-by-line; output files split at Telegram's 2000 MB upload ceiling.
No code execution, downloader, credential extraction, or exfiltration.
"""
import asyncio
import hashlib
import logging
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Put the NEW token issued by @BotFather here. Do not use an exposed/revoked token.
TOKEN = "8641691193:AAEZQAUUzMEA6PvU5sayAFlHimQ2yB3nSc4"
OWNER_ID = 7665891917
API_URL = os.getenv("LOCAL_BOT_API_URL", "http://127.0.0.1:8081").rstrip("/")
SHARED = Path(os.getenv("SHARED_DIR", "/data/output"))
# Telegram Local Bot API upload limit: 2000 MB. Leave margin for each part.
PART_BYTES = 1_900_000_000
LOCK = asyncio.Lock()
LOG = logging.getLogger("text-bot")

# Avoid exporting material that looks like a password, access key, or login pair.
SENSITIVE = re.compile(
    r"(?i)(?:\b(?:password|passwd|passcode|pwd|token|secret|api[_-]?key|"
    r"authorization|bearer|private[_-]?key|credential)\s*[:=]"
    r"|https?://[^\s/@]+:[^\s/@]+@"
    r"|[^\s:@/]+@[^\s:@/]+:[^\s:|/]+"
    r"|(?:https?://)?[^\s|]+\|[^\s|]+\|[^\s|]+"
    r"|:[^\s:/]+:[^\s:/]+$)"
)
SIMPLE_PAIR = re.compile(r"^\S+[:|]\S+$")


def safe(line: str) -> bool:
    return bool(line and not SENSITIVE.search(line) and not SIMPLE_PAIR.fullmatch(line))


def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Search", callback_data="search")],
        [InlineKeyboardButton("🧹 Clean", callback_data="clean")],
    ])


def allowed(update: Update) -> bool:
    return bool(update.effective_user and update.effective_chat
                and update.effective_user.id == OWNER_ID
                and update.effective_chat.type == "private")


class Parts:
    def __init__(self, folder: Path, prefix: str):
        self.folder, self.prefix = folder, prefix
        self.files = []
        self.file = None
        self.size = 0

    def write(self, line: str):
        raw = (line + "\n").encode("utf-8")
        # Almost every normal TXT record is much smaller than 1.9 GB.
        if len(raw) > PART_BYTES:
            raise ValueError("One text line exceeds Telegram's single-file limit")
        if self.file is None or self.size + len(raw) > PART_BYTES:
            self._new_file()
        self.file.write(raw)
        self.size += len(raw)

    def _new_file(self):
        if self.file is not None:
            self.file.close()
        path = self.folder / f"{self.prefix}_part_{len(self.files)+1:03}.txt"
        self.file = path.open("wb")
        self.files.append(path)
        self.size = 0

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
        # The separate local API server container must read these result files.
        for path in self.files:
            path.chmod(0o644)


def search_file(source: Path, term: str, output_dir: Path):
    parts = Parts(output_dir, "results")
    matches = safe_matches = hidden = 0
    try:
        with source.open("r", encoding="utf-8", errors="replace") as inp:
            for line_no, original in enumerate(inp, 1):
                if term.casefold() not in original.casefold():
                    continue
                matches += 1
                line = original.strip().replace("\x00", "")
                if not safe(line):
                    hidden += 1
                    continue
                parts.write(f"Line {line_no}: {line}")
                safe_matches += 1
    finally:
        parts.close()
    return parts.files, matches, safe_matches, hidden


def clean_file(source: Path, output_dir: Path):
    parts = Parts(output_dir, "cleaned")
    total = kept = discarded = 0
    # Disk-based deduplication instead of a RAM-sized Python set.
    db = sqlite3.connect(output_dir / "seen.sqlite3")
    try:
        db.execute("CREATE TABLE seen (digest BLOB PRIMARY KEY) WITHOUT ROWID")
        with source.open("r", encoding="utf-8", errors="replace") as inp:
            for original in inp:
                total += 1
                line = original.strip().replace("\x00", "")
                if not safe(line):
                    discarded += 1
                    continue
                digest = hashlib.blake2b(line.encode("utf-8"), digest_size=16).digest()
                cursor = db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (digest,))
                if cursor.rowcount == 0:
                    discarded += 1
                    continue
                parts.write(line)
                kept += 1
            db.commit()
    finally:
        db.close()
        parts.close()
    return parts.files, total, kept, discarded


async def send_parts(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     paths, caption: str):
    if not paths:
        await update.effective_message.reply_text("No non-sensitive lines to export.", reply_markup=menu())
        return
    for index, path in enumerate(paths, 1):
        # With local_mode=True, PTB sends the shared absolute file path to the
        # local API server instead of loading the whole file into Python RAM.
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=path,
            filename=path.name,
            caption=(caption if index == 1 else f"Result file {index}/{len(paths)}"),
            read_timeout=600,
            write_timeout=600,
            connect_timeout=60,
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    context.user_data.clear()
    await update.message.reply_text(
        "Welcome! Choose an operation:", reply_markup=menu()
    )


async def select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update) or not update.callback_query:
        return
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["mode"] = "search_upload" if query.data == "search" else "clean_upload"
    await query.message.reply_text(
        "🔎 Search: Upload a TXT, LOG, or CSV file." if query.data == "search"
        else "🧹 Clean: Upload a TXT, LOG, or CSV file."
    )


async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    mode = context.user_data.get("mode")
    if mode not in {"search_upload", "clean_upload"}:
        await update.message.reply_text("Select Search or Clean first.", reply_markup=menu())
        return
    doc = update.message.document
    if not doc or Path(doc.file_name or "").suffix.lower() not in {".txt", ".log", ".csv"}:
        await update.message.reply_text("Please upload a TXT, LOG, or CSV file.")
        return
    if LOCK.locked():
        await update.message.reply_text("Another operation is running. Try again shortly.")
        return
    try:
        tgfile = await context.bot.get_file(doc.file_id, read_timeout=300)
        file_path = tgfile.file_path
        if not file_path:
            raise FileNotFoundError("Telegram returned an empty file_path")
        source = Path(file_path)
        if not source.is_absolute() or not source.is_file():
            raise FileNotFoundError(
                "Local Bot API did not return a readable local file: " + str(file_path)
            )
    except Exception:
        LOG.exception("Unable to access uploaded file. Verify embedded Local Bot API is running.")
        await update.message.reply_text(
            "❌ Could not access the uploaded file. Please check the Railway logs "
            "and ensure the Local Bot API starts in the same container."
        )
        return

    if mode == "search_upload":
        context.user_data["source"] = str(source)
        context.user_data["mode"] = "search_term"
        await update.message.reply_text("✅ File received. What should I search for?")
        return

    async with LOCK:
        await update.message.reply_text("⏳ Cleaning file...")
        try:
            with tempfile.TemporaryDirectory(prefix="clean_", dir=SHARED) as tmp:
                Path(tmp).chmod(0o755)
                files, total, kept, discarded = await asyncio.to_thread(clean_file, source, Path(tmp))
                await send_parts(update, context, files,
                                 f"✅ Clean complete\nRead: {total}\nKept: {kept}\nSkipped: {discarded}")
        except Exception:
            LOG.exception("Clean failed")
            await update.message.reply_text("❌ Clean failed. Check server logs.")
        finally:
            context.user_data.clear()


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if context.user_data.get("mode") != "search_term":
        await update.message.reply_text("Select Search or Clean.", reply_markup=menu())
        return
    term = (update.message.text or "").strip()
    if not term or "\n" in term:
        await update.message.reply_text("Enter a non-empty search term.")
        return
    source = Path(context.user_data.get("source", ""))
    if not source.is_file():
        context.user_data.clear()
        await update.message.reply_text("The uploaded file is no longer available. Upload it again.", reply_markup=menu())
        return
    if LOCK.locked():
        await update.message.reply_text("Another operation is running. Try again shortly.")
        return
    async with LOCK:
        await update.message.reply_text("⏳ Searching...")
        try:
            with tempfile.TemporaryDirectory(prefix="search_", dir=SHARED) as tmp:
                Path(tmp).chmod(0o755)
                files, matches, exported, hidden = await asyncio.to_thread(search_file, source, term, Path(tmp))
                await send_parts(update, context, files,
                                 f"✅ Search complete\nMatches: {matches}\nExported: {exported}\nHidden: {hidden}")
                if files:
                    await update.message.reply_text("Enter another term to search the same file, or select another operation.", reply_markup=menu())
        except Exception:
            LOG.exception("Search failed")
            await update.message.reply_text("❌ Search failed. Check server logs.")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    if TOKEN == "PASTE_YOUR_NEW_BOT_TOKEN_HERE":
        raise RuntimeError("Replace TOKEN in bot.py with a newly issued bot token")
    SHARED.mkdir(parents=True, exist_ok=True)
    LOG.info("Bot API endpoint: %s", API_URL)
    LOG.info("Shared output directory: %s", SHARED)
    app = (Application.builder()
           .token(TOKEN)
           .base_url(API_URL + "/bot")
           .base_file_url(API_URL + "/file/bot")
           .local_mode(True)
           .read_timeout(600)
           .write_timeout(600)
           .build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(select, pattern=r"^(search|clean)$"))
    app.add_handler(MessageHandler(filters.Document.ALL, receive_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text))
    LOG.info("Starting owner-only bot with embedded Local Bot API")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
