"""
Pinterest Fetch Bot - entry point.

Ways to get media:
1. Direct search: send any text (or /search <query>) -> bot fetches from
   Pinterest and posts results with Prev/Next/Download/Similar
   buttons. Hitting Next past the last fetched pin transparently loads more
   results from Pinterest (infinite scroll, no fixed cap).
2. Inline mode: type "@<bot_username> <query>" in any chat -> Telegram shows
   a native picker grid of results.
3. /explore (also auto-run once on /start): a non-repeating random feed
   pulled from rotating topics.
4. /board <pinterest board URL>: browse all pins from a specific board.

Deployed as a Render Web Service via webhook (see Dockerfile / README).
"""
import asyncio
import html
import logging
import re

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultPhoto,
    InlineQueryResultVideo,
    InputMediaPhoto,
    InputMediaVideo,
    Update,
)
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

CAPTION_PARSE_MODE = "HTML"


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


def _stats_buttons(pin: PinterestMedia) -> list:
    """Small non-actionable "chip" buttons showing engagement stats.
    Pinterest doesn't expose a separate destination per stat type, so
    tapping these just acknowledges (like the page-number button) —
    they're informational, not links."""
    buttons = []
    if pin.like_count is not None:
        buttons.append(InlineKeyboardButton(f"❤️ {pin.like_count:,}", callback_data="noop"))
    if pin.save_count is not None:
        buttons.append(InlineKeyboardButton(f"📌 {pin.save_count:,}", callback_data="noop"))
    if pin.comment_count is not None:
        buttons.append(InlineKeyboardButton(f"💬 {pin.comment_count:,}", callback_data="noop"))
    if pin.view_count is not None:
        buttons.append(InlineKeyboardButton(f"👁 {pin.view_count:,}", callback_data="noop"))
    return buttons


def build_nav_keyboard(session_id: str, index: int, pin: PinterestMedia) -> InlineKeyboardMarkup:
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"nav:{session_id}:{index - 1}", style="primary"))
    nav_row.append(InlineKeyboardButton(f"#{index + 1}", callback_data="noop"))
    nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"nav:{session_id}:{index + 1}", style="primary"))

    rows = [nav_row]

    stats_row = _stats_buttons(pin)
    if stats_row:
        rows.append(stats_row)

    rows.append(
        [
            InlineKeyboardButton("📥 Original", callback_data=f"dl:{session_id}:{index}", style="success"),
            InlineKeyboardButton("🔗 Similar", callback_data=f"sim:{session_id}:{index}"),
        ]
    )
    rows.append([InlineKeyboardButton("🌐 Open Pin", url=pin.pin_url)])
    return InlineKeyboardMarkup(rows)


def _build_caption(label: str, pin: PinterestMedia) -> str:
    label_esc = html.escape(label)
    lines = [f"🔎 <b>{label_esc}</b>"]
    if pin.title:
        lines.append(f"<b>{html.escape(pin.title[:200])}</b>")
    if pin.description:
        desc = pin.description.strip()
        if len(desc) > 600:
            desc = desc[:600].rstrip() + "…"
        lines.append(f'<blockquote expandable>"{html.escape(desc)}"</blockquote>')

    caption = "\n".join(lines).strip()
    if len(caption) > 1024:
        # Truncating a naive [:1024] could cut mid-tag and break Telegram's
        # HTML parser — drop the description block instead if still too long.
        caption = "\n".join(lines[:2]).strip()[:1024]
    return caption


def _parse_board_ref(raw: str):
    """Accepts a full pinterest.com board URL or 'username/board-slug'."""
    raw = raw.strip()
    m = re.search(r"pinterest\.[a-z.]+/([^/?#]+)/([^/?#]+)/?", raw, re.IGNORECASE)
    if m:
        return m.group(1), m.group(2)
    parts = [p for p in raw.split("/") if p]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return None, None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.upsert_user(user.id, user.username, user.first_name)
    text = (
        f"👋 Hey {user.first_name}!\n\n"
        "I'm the <b>Pinterest Fetch Bot</b>. Send me any keyword "
        "(like <code>sunset wallpaper</code> or <code>anime aesthetic</code>) and I'll find "
        "photos/videos from Pinterest and send them right here.\n\n"
        f"✨ Inline mode: type <code>@{context.bot.username} your_query</code> "
        "in any chat for a gallery-style picker.\n\n"
        "Commands:\n"
        "/search &lt;query&gt; — search Pinterest\n"
        "/board &lt;board URL&gt; — browse a specific board\n"
        "/explore — random non-repeating feed\n"
        "/help — help\n\n"
        "Here's something random to get started 👇"
    )
    await update.message.reply_text(text, parse_mode=CAPTION_PARSE_MODE)
    await _send_explore(update, context)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Just send a keyword, or use /search <query>.\n"
        "/board <pinterest board URL> — browse all pins on a board\n"
        "/explore — random non-repeating feed\n\n"
        "Use Prev/Next to browse — Next keeps loading more results, no fixed limit.\n"
        "📥 Original = best quality file · 🔗 Similar = more like this pin.\n\n"
        f"Inline mode: type @{context.bot.username} query in any chat for a picker grid."
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    await _do_search(update, context, query)


async def text_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _do_search(update, context, update.message.text)


async def explore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.upsert_user(user.id, user.username, user.first_name)
    await _send_explore(update, context)


async def board_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            "Usage: /board <pinterest board URL>\n"
            "e.g. /board https://www.pinterest.com/username/board-name/"
        )
        return

    raw = " ".join(context.args)
    username, board_slug = _parse_board_ref(raw)
    if not username or not board_slug:
        await update.message.reply_text(
            "Couldn't parse that. Send a board URL like "
            "https://www.pinterest.com/username/board-name/"
        )
        return

    status = await update.message.reply_text(f"🔎 Looking up board '{board_slug}' by {username}...")
    board_id = await asyncio.to_thread(pinterest.find_board_id, username, board_slug)
    if not board_id:
        await status.edit_text(
            "😕 Couldn't find that board (wrong URL, private board, or Pinterest "
            "blocked the lookup for anonymous access)."
        )
        return

    pins = await asyncio.to_thread(pinterest.search_board, board_id, config.DEFAULT_RESULT_LIMIT)
    if not pins:
        await status.edit_text(
            "😕 Pinterest didn't return any pins for this board.\n\n"
            "This is a Pinterest-side restriction, not a bug: board pages are "
            "commonly gated behind a login wall even when search results aren't. "
            "The only way around it is an authenticated session — set up /login "
            "(needs Chrome enabled in the Dockerfile, see README) and try again."
        )
        return

    await db.upsert_user(user.id, user.username, user.first_name)
    label = f"Board: {board_slug}"
    session_id = await db.create_session(user.id, board_id, [p.to_dict() for p in pins], mode="board", label=label)

    await status.delete()
    await _send_pin(update.effective_chat.id, context, session_id, 0, pins[0], label)


async def _do_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    user = update.effective_user
    query = query.strip()
    if not query:
        await update.message.reply_text("Send a keyword to search, e.g.: cats aesthetic")
        return

    status_msg = await update.message.reply_text(f"🔎 Searching Pinterest for '{query}'...")
    pins = await asyncio.to_thread(pinterest.search, query, config.DEFAULT_RESULT_LIMIT)

    if not pins:
        await status_msg.edit_text(
            "😕 No results found. Try a different keyword, or try again in a bit "
            "(Pinterest sometimes rate-limits)."
        )
        return

    await db.upsert_user(user.id, user.username, user.first_name)
    await db.increment_search_count(user.id)
    session_id = await db.create_session(user.id, query, [p.to_dict() for p in pins], mode="search")

    await status_msg.delete()
    await _send_pin(update.effective_chat.id, context, session_id, 0, pins[0], query)


async def _send_explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pins = await asyncio.to_thread(pinterest.search_random, set(), config.DEFAULT_RESULT_LIMIT)
    if not pins:
        return
    session_id = await db.create_session(
        user.id, "explore", [p.to_dict() for p in pins], mode="explore", label="Explore"
    )
    await _send_pin(update.effective_chat.id, context, session_id, 0, pins[0], "Explore")


async def _send_pin(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    session_id: str,
    index: int,
    pin: PinterestMedia,
    label: str,
):
    keyboard = build_nav_keyboard(session_id, index, pin)
    caption = _build_caption(label, pin)

    if pin.is_video:
        status = None
        if pin.needs_remux:
            status = await context.bot.send_message(chat_id=chat_id, text="🎬 Processing video, one moment...")

        source, path = await _get_video_media(pin)
        if source:
            try:
                await context.bot.send_video(
                    chat_id=chat_id, video=source, caption=caption, reply_markup=keyboard,
                    parse_mode=CAPTION_PARSE_MODE,
                )
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
            await status.edit_text("⚠️ Couldn't process the video, sending photo instead.")
        # fall through to photo fallback below

    photo_url = pin.preview_url or pin.thumb_url
    if not photo_url:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Couldn't load this media, try Next.")
        return
    await context.bot.send_photo(
        chat_id=chat_id, photo=photo_url, caption=caption, reply_markup=keyboard,
        parse_mode=CAPTION_PARSE_MODE,
    )


# ---------------------------------------------------------------------------
# Callback buttons
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
        await query.answer("⏱ Session expired, please search again.", show_alert=True)
        return

    if index < 0:
        await query.answer()
        return

    # Answer immediately, BEFORE any slow work below (Pinterest pagination
    # for "load more", HLS video remuxing). Telegram invalidates callback
    # queries that aren't answered within a few seconds ("query is too old"
    # errors) — those steps can take longer than that, so we can't wait
    # until after they finish. Anything that fails past this point is
    # reported via a normal chat message instead of a callback alert, since
    # the query is already spent.
    try:
        await query.answer()
    except Exception:
        pass

    chat_id = query.message.chat_id
    pins_raw = doc["pins"]
    mode = doc.get("mode", "search")

    # Ran past what we've fetched so far -> load more instead of stopping.
    if index >= len(pins_raw):
        exclude_ids = {p["id"] for p in pins_raw if p.get("id")}
        if mode == "explore":
            more = await asyncio.to_thread(pinterest.search_random, exclude_ids, config.DEFAULT_RESULT_LIMIT)
        elif mode == "board":
            more = await asyncio.to_thread(
                pinterest.board_more, doc["query"], exclude_ids, config.DEFAULT_RESULT_LIMIT
            )
        else:
            more = await asyncio.to_thread(
                pinterest.search_more, doc["query"], exclude_ids, config.DEFAULT_RESULT_LIMIT
            )
        if not more:
            await context.bot.send_message(chat_id=chat_id, text="😕 No more results right now, try again shortly.")
            return
        more_dicts = [m.to_dict() for m in more]
        await db.append_session_pins(session_id, more_dicts)
        pins_raw = pins_raw + more_dicts

    await db.update_session_index(session_id, index)
    pin = PinterestMedia.from_dict(pins_raw[index])
    keyboard = build_nav_keyboard(session_id, index, pin)
    label = doc.get("label", doc["query"])
    caption = _build_caption(label, pin)

    path = None
    source = None
    try:
        if pin.is_video:
            source, path = await _get_video_media(pin)
            if source:
                media = InputMediaVideo(source, caption=caption, parse_mode=CAPTION_PARSE_MODE)
            else:
                photo_url = pin.preview_url or pin.thumb_url
                if not photo_url:
                    await context.bot.send_message(chat_id=chat_id, text="⚠️ Couldn't load this media.")
                    return
                media = InputMediaPhoto(photo_url, caption=caption, parse_mode=CAPTION_PARSE_MODE)
        else:
            photo_url = pin.preview_url or pin.thumb_url
            if not photo_url:
                await context.bot.send_message(chat_id=chat_id, text="⚠️ Couldn't load this media.")
                return
            media = InputMediaPhoto(photo_url, caption=caption, parse_mode=CAPTION_PARSE_MODE)

        await query.edit_message_media(media=media, reply_markup=keyboard)
    except Exception:
        logger.exception("Failed to edit message media for session=%s index=%s", session_id, index)
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Trouble loading that, try again shortly.")
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
        await query.answer("⏱ Session expired.", show_alert=True)
        return

    pin = PinterestMedia.from_dict(doc["pins"][index])
    await query.answer("📤 Sending...")
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
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Couldn't download the video, try again shortly.")
    elif pin.original_url:
        await context.bot.send_document(chat_id=chat_id, document=pin.original_url, caption="🖼 Original quality image")
    else:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ No original file available for this pin.")


async def similar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, session_id, index_str = query.data.split(":")
    index = int(index_str)

    doc = await db.get_session(session_id)
    if not doc:
        await query.answer("⏱ Session expired.", show_alert=True)
        return

    pin = PinterestMedia.from_dict(doc["pins"][index])
    seed_query = " ".join((pin.title or "trending").split()[:6])

    await query.answer("🔎 Finding similar pins...")
    pins = await asyncio.to_thread(pinterest.search, seed_query, config.DEFAULT_RESULT_LIMIT)
    chat_id = query.message.chat_id

    if not pins:
        await context.bot.send_message(chat_id=chat_id, text="😕 Couldn't find similar pins.")
        return

    user_id = query.from_user.id
    label = f"Similar to: {seed_query}"
    new_session_id = await db.create_session(user_id, seed_query, [p.to_dict() for p in pins], mode="search", label=label)
    await _send_pin(chat_id, context, new_session_id, 0, pins[0], label)


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
            # Inline results need a directly playable URL - HLS-only videos
            # can't be remuxed on the fly here, so they fall through to the
            # photo branch below (using their poster/thumbnail).
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
        await update.message.reply_text("⛔ Owner-only command.")
        return

    if not config.PINTEREST_EMAIL or not config.PINTEREST_PASSWORD:
        await update.message.reply_text("⚠️ PINTEREST_EMAIL / PINTEREST_PASSWORD env vars are not set.")
        return

    msg = await update.message.reply_text("🔐 Attempting Pinterest login (headless Chrome, may take a moment)...")
    success = await asyncio.to_thread(login_service.attempt_login, pinterest)

    if success:
        await msg.edit_text("✅ Login successful.")
    else:
        await msg.edit_text(
            "❌ Login failed. Common cause: Chrome isn't installed in the container — "
            "see the commented-out Chrome install block in the Dockerfile. "
            "Search works fine without this."
        )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id):
        return
    total_users = await db.count_users()
    total_searches = await db.total_searches()
    await update.message.reply_text(f"📊 Stats\nUsers: {total_users}\nTotal searches: {total_searches}")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id):
        return

    reply_to = update.message.reply_to_message
    text = " ".join(context.args) if context.args else None

    if not reply_to and not text:
        await update.message.reply_text(
            "Usage:\n"
            "/broadcast <message> — sends plain text to all users\n"
            "or reply to any message (photo/video/text) with /broadcast to forward it to all users"
        )
        return

    user_ids = await db.get_all_user_ids()
    status = await update.message.reply_text(f"📢 Broadcasting to {len(user_ids)} users...")

    sent, failed = 0, 0
    for uid in user_ids:
        try:
            if reply_to:
                await context.bot.copy_message(chat_id=uid, from_chat_id=reply_to.chat_id, message_id=reply_to.message_id)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # gentle pacing to avoid Telegram flood limits

    await status.edit_text(f"✅ Broadcast finished.\nSent: {sent}\nFailed: {failed}")


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
    application.add_handler(CommandHandler("explore", explore_command))
    application.add_handler(CommandHandler("board", board_command))
    application.add_handler(CommandHandler("login", login_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("broadcast", broadcast_cmd))
    application.add_handler(CallbackQueryHandler(nav_callback, pattern=r"^(nav:|noop)"))
    application.add_handler(CallbackQueryHandler(download_callback, pattern=r"^dl:"))
    application.add_handler(CallbackQueryHandler(similar_callback, pattern=r"^sim:"))
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
