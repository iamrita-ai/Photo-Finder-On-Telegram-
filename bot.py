"""
Pinterest Fetch Bot - entry point.

Two ways users get media:
1. Direct search: send any text (or /search <query>) -> bot fetches from
   Pinterest and posts the result in-chat with Prev/Next/Download buttons.
2. Inline mode: type "@<bot_username> <query>" in any chat -> Telegram shows
   a native picker grid of results; tapping one sends it straight into that
   chat. This is the "web jaise results dikhe, user choose kare" behaviour.

Deployed as a Render Web Service via webhook (see Dockerfile / README).
"""
import asyncio
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultPhoto,
    InlineQueryResultVideo,
    InputMediaPhoto,
    InputMediaVideo,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

import config
import login_service
import media_utils
from database import Database
from pinterest_service import PinterestMedia, PinterestService

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=config.LOG_LEVEL,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("pinterest_bot")

db = Database(config.MONGO_URI, config.MONGO_DB_NAME)
pinterest = PinterestService(
    email=config.PINTEREST_EMAIL,
    password=config.PINTEREST_PASSWORD,
    username=config.PINTEREST_USERNAME,
)


def is_owner(user_id: int) -> bool:
    return user_id in config.OWNER_IDS


async def _get_video_media(pin: PinterestMedia):
    """Returns (source, local_path). `source` is a URL string, an open file
    object, or None if no playable video could be produced. `local_path` is
    set (and must be cleaned up) only when we remuxed HLS -> mp4 locally."""
    if pin.video_url:
        return pin.video_url, None
    if pin.video_hls_url:
        path = await media_utils.remux_hls_to_mp4(pin.video_hls_url)
        if path:
            return open(path, "rb"), path
    return None, None


def build_nav_keyboard(session_id: str, index: int, total: int, pin: PinterestMedia) -> InlineKeyboardMarkup:
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"nav:{session_id}:{index - 1}"))
    nav_row.append(InlineKeyboardButton(f"{index + 1}/{total}", callback_data="noop"))
    if index < total - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"nav:{session_id}:{index + 1}"))

    action_row = [
        InlineKeyboardButton("📥 Original", callback_data=f"dl:{session_id}:{index}"),
        InlineKeyboardButton("🔗 Open Pin", url=pin.pin_url),
    ]
    rows = [nav_row, action_row] if nav_row else [action_row]
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.upsert_user(user.id, user.username, user.first_name)
    text = (
        f"👋 Namaste {user.first_name}!\n\n"
        "Main *Pinterest Fetch Bot* hoon. Bas koi bhi keyword bhejo "
        "(jaise `sunset wallpaper` ya `anime aesthetic`), main Pinterest se "
        "photo/video dhoondh kar seedha yahin bhej dunga.\n\n"
        f"✨ Ek aur tareeka: kisi bhi chat mein `@{context.bot.username} tumhara_query` "
        "type karo — results ek gallery ki tarah dikhenge, jo pasand aaye tap karke "
        "seedha bhej do.\n\n"
        "Commands:\n"
        "/search <query> — search karo\n"
        "/help — madad"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bas apna search keyword bhej do ya /search <query> use karo.\n"
        "Result ke neeche buttons se Prev/Next browse karo, "
        "'📥 Original' se best quality file milegi.\n\n"
        f"Inline mode: kisi bhi chat mein @{context.bot.username} query likho, "
        "results ek picker mein dikhenge."
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    await _do_search(update, context, query)


async def text_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _do_search(update, context, update.message.text)


async def _do_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    user = update.effective_user
    query = query.strip()
    if not query:
        await update.message.reply_text("Search karne ke liye kuch keyword bhejo, jaise: cats aesthetic")
        return

    status_msg = await update.message.reply_text(f"🔎 Pinterest par '{query}' dhoondh raha hoon...")
    pins = await asyncio.to_thread(pinterest.search, query, config.DEFAULT_RESULT_LIMIT)

    if not pins:
        await status_msg.edit_text(
            "😕 Koi result nahi mila. Doosra keyword try karo, ya thodi der baad phir se try karo "
            "(Pinterest kabhi kabhi rate-limit kar deta hai)."
        )
        return

    await db.upsert_user(user.id, user.username, user.first_name)
    await db.increment_search_count(user.id)
    session_id = await db.create_session(user.id, query, [p.to_dict() for p in pins])

    await status_msg.delete()
    await _send_pin(update.effective_chat.id, context, session_id, 0, pins[0], query, len(pins))


async def _send_pin(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    session_id: str,
    index: int,
    pin: PinterestMedia,
    query: str,
    total: int,
):
    keyboard = build_nav_keyboard(session_id, index, total, pin)
    caption = f"🔎 {query}\n{pin.title}".strip()[:1024]

    if pin.is_video:
        status = None
        if pin.needs_remux:
            status = await context.bot.send_message(chat_id=chat_id, text="🎬 Video process ho raha hai, thoda ruko...")

        source, path = await _get_video_media(pin)
        if source:
            try:
                await context.bot.send_video(chat_id=chat_id, video=source, caption=caption, reply_markup=keyboard)
            finally:
                if path:
                    try:
                        source.close()
                    except Exception:
                        pass
                    media_utils.cleanup(path)
            if status:
                await status.delete()
            return

        if status:
            await status.edit_text("⚠️ Video process nahi ho paaya, photo bhej raha hoon.")
        # fall through to photo fallback below

    photo_url = pin.preview_url or pin.thumb_url
    if not photo_url:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Ye media load nahi ho paaya, agla try karo.")
        return
    await context.bot.send_photo(chat_id=chat_id, photo=photo_url, caption=caption, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Callback buttons (Prev / Next / Download original)
# ---------------------------------------------------------------------------
async def nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "noop":
        await query.answer()
        return

    _, session_id, index_str = query.data.split(":")
    index = int(index_str)

    doc = await db.get_session(session_id)
    if not doc:
        await query.answer("⏱ Session expire ho gaya, dobara search karo.", show_alert=True)
        return

    pins_raw = doc["pins"]
    if index < 0 or index >= len(pins_raw):
        await query.answer()
        return

    await db.update_session_index(session_id, index)
    pin = PinterestMedia.from_dict(pins_raw[index])
    keyboard = build_nav_keyboard(session_id, index, len(pins_raw), pin)
    caption = f"🔎 {doc['query']}\n{pin.title}".strip()[:1024]

    path = None
    source = None
    try:
        if pin.is_video:
            source, path = await _get_video_media(pin)
            if source:
                media = InputMediaVideo(source, caption=caption)
            else:
                photo_url = pin.preview_url or pin.thumb_url
                if not photo_url:
                    await query.answer("⚠️ Ye media load nahi ho paaya.", show_alert=True)
                    return
                media = InputMediaPhoto(photo_url, caption=caption)
        else:
            photo_url = pin.preview_url or pin.thumb_url
            if not photo_url:
                await query.answer("⚠️ Ye media load nahi ho paaya.", show_alert=True)
                return
            media = InputMediaPhoto(photo_url, caption=caption)

        await query.edit_message_media(media=media, reply_markup=keyboard)
        await query.answer()
    except Exception:
        logger.exception("Failed to edit message media for session=%s index=%s", session_id, index)
        await query.answer("⚠️ Load karne mein dikkat aayi, thodi der baad try karo.", show_alert=True)
    finally:
        if path:
            if source:
                try:
                    source.close()
                except Exception:
                    pass
            media_utils.cleanup(path)


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, session_id, index_str = query.data.split(":")
    index = int(index_str)

    doc = await db.get_session(session_id)
    if not doc:
        await query.answer("⏱ Session expire ho gaya.", show_alert=True)
        return

    pin = PinterestMedia.from_dict(doc["pins"][index])
    await query.answer("📤 Bhej raha hoon...")
    chat_id = query.message.chat_id

    if pin.is_video:
        source, path = await _get_video_media(pin)
        if source:
            try:
                await context.bot.send_document(chat_id=chat_id, document=source, caption="🎬 Original quality video")
            finally:
                if path:
                    try:
                        source.close()
                    except Exception:
                        pass
                    media_utils.cleanup(path)
        else:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Video download nahi ho paaya, thodi der baad try karo.")
    elif pin.original_url:
        await context.bot.send_document(chat_id=chat_id, document=pin.original_url, caption="🖼 Original quality image")
    else:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Original file uplabdh nahi hai is pin ke liye.")


# ---------------------------------------------------------------------------
# Inline mode - "web style" result picker
# ---------------------------------------------------------------------------
async def inline_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    iq = update.inline_query
    query_text = iq.query.strip()
    if not query_text:
        return

    pins = await asyncio.to_thread(pinterest.search, query_text, config.INLINE_RESULT_LIMIT)

    results = []
    for pin in pins:
        if pin.is_video and pin.video_url:
            results.append(
                InlineQueryResultVideo(
                    id=pin.id,
                    video_url=pin.video_url,
                    mime_type="video/mp4",
                    thumbnail_url=pin.video_poster or pin.thumb_url or pin.preview_url or "",
                    title=pin.title or "Pinterest video",
                    caption=pin.title or "",
                )
            )
        elif pin.preview_url:
            results.append(
                InlineQueryResultPhoto(
                    id=pin.id,
                    photo_url=pin.preview_url,
                    thumbnail_url=pin.thumb_url or pin.preview_url,
                    title=pin.title or "Pinterest image",
                    caption=pin.title or "",
                )
            )

    await iq.answer(results[: config.INLINE_RESULT_LIMIT], cache_time=300, is_personal=False)


# ---------------------------------------------------------------------------
# Owner-only admin commands
# ---------------------------------------------------------------------------
async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id):
        await update.message.reply_text("⛔ Ye command sirf owner use kar sakta hai.")
        return

    if not config.PINTEREST_EMAIL or not config.PINTEREST_PASSWORD:
        await update.message.reply_text("⚠️ PINTEREST_EMAIL / PINTEREST_PASSWORD env vars set nahi hain.")
        return

    msg = await update.message.reply_text(
        "🔐 Pinterest login try kar raha hoon (headless Chrome, thoda time lagega)..."
    )
    success = await asyncio.to_thread(login_service.attempt_login, pinterest)

    if success:
        await msg.edit_text("✅ Login successful.")
    else:
        await msg.edit_text(
            "❌ Login fail ho gaya. Common wajah: container mein Chrome install nahi hai — "
            "README mein Dockerfile ka commented-out Chrome install block dekho. "
            "Search feature iske bina bhi fully kaam karta hai."
        )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id):
        return
    total_users = await db.count_users()
    total_searches = await db.total_searches()
    await update.message.reply_text(f"📊 Stats\nUsers: {total_users}\nTotal searches: {total_searches}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception while processing update: %s", update, exc_info=context.error)


# ---------------------------------------------------------------------------
async def post_init(application: Application):
    await db.setup_indexes()
    logger.info("Database indexes ready.")


def main():
    application = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("login", login_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CallbackQueryHandler(nav_callback, pattern=r"^(nav:|noop)"))
    application.add_handler(CallbackQueryHandler(download_callback, pattern=r"^dl:"))
    application.add_handler(InlineQueryHandler(inline_search))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_search))
    application.add_error_handler(error_handler)

    logger.info("Starting webhook on port %s ...", config.PORT)
    application.run_webhook(
        listen="0.0.0.0",
        port=config.PORT,
        url_path=config.BOT_TOKEN,
        webhook_url=f"{config.WEBHOOK_URL}/{config.BOT_TOKEN}",
        secret_token=config.WEBHOOK_SECRET or None,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
