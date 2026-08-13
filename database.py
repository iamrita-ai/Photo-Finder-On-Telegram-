"""
Async MongoDB access layer (Motor). Two collections:

- users            -> one doc per Telegram user, search_count, timestamps
- search_sessions  -> one doc per search, holds the fetched pins so
                      Prev/Next/Download buttons don't need to re-hit
                      Pinterest on every click. TTL-expires after 24h.
- settings         -> single doc used to cache Pinterest login cookies
                      (see login_service.py)
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 24 * 60 * 60


class Database:
    def __init__(self, mongo_uri: str, db_name: str):
        self._client = AsyncIOMotorClient(mongo_uri)
        self.db = self._client[db_name]
        self.users = self.db["users"]
        self.sessions = self.db["search_sessions"]
        self.settings = self.db["settings"]

    async def setup_indexes(self):
        await self.sessions.create_index("created_at", expireAfterSeconds=SESSION_TTL_SECONDS)
        await self.users.create_index("user_id", unique=True)

    # ---- users --------------------------------------------------------
    async def upsert_user(self, user_id: int, username: Optional[str], first_name: Optional[str]):
        now = datetime.now(timezone.utc)
        await self.users.update_one(
            {"user_id": user_id},
            {
                "$set": {"username": username, "first_name": first_name, "last_seen": now},
                "$setOnInsert": {"joined_at": now, "search_count": 0},
            },
            upsert=True,
        )

    async def increment_search_count(self, user_id: int):
        await self.users.update_one({"user_id": user_id}, {"$inc": {"search_count": 1}})

    async def count_users(self) -> int:
        return await self.users.count_documents({})

    async def total_searches(self) -> int:
        cursor = self.users.aggregate([{"$group": {"_id": None, "total": {"$sum": "$search_count"}}}])
        result = await cursor.to_list(length=1)
        return result[0]["total"] if result else 0

    # ---- search sessions ------------------------------------------------
    async def create_session(self, user_id: int, query: str, pins: list[dict]) -> str:
        session_id = secrets.token_urlsafe(6)
        await self.sessions.insert_one(
            {
                "_id": session_id,
                "user_id": user_id,
                "query": query,
                "pins": pins,
                "index": 0,
                "created_at": datetime.now(timezone.utc),
            }
        )
        return session_id

    async def get_session(self, session_id: str) -> Optional[dict]:
        return await self.sessions.find_one({"_id": session_id})

    async def update_session_index(self, session_id: str, index: int):
        await self.sessions.update_one({"_id": session_id}, {"$set": {"index": index}})

    # ---- pinterest login cookies (optional feature) ---------------------
    async def save_pinterest_cookies(self, cookies: list[dict]):
        await self.settings.update_one(
            {"_id": "pinterest_cookies"},
            {"$set": {"cookies": cookies, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    async def get_pinterest_cookies(self) -> Optional[list[dict]]:
        doc = await self.settings.find_one({"_id": "pinterest_cookies"})
        return doc.get("cookies") if doc else None
