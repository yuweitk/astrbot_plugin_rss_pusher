"""SQLite 存储层。

对应 qq-maid-bot 的 `rss/storage/mod.rs` ＋ `storage/notification`。核心语义：

- **订阅** 按 `scope_key`（AstrBot 的 unified_msg_origin）隔离，跨会话不能互删；
- **条目** 以 `(subscription_id, item_key)` 为主键做去重，`revision_hash` 变化说明
  同一篇文章被编辑过，此时重置 `pushed_at` 让它重新推送；
- **推送状态** 直接记在条目上（`pushed_at` / `push_attempts`），失败超过阈值就放弃，
  避免一个死链条目每轮都刷错误日志；
- **保留策略** 只清已推送的旧指纹，绝不动待推送条目。

数据库写在 AstrBot 的 `data/plugin_data/<插件名>/rss.db`。sqlite3 的操作放到线程池里
执行（`asyncio.to_thread`），并用一把锁串行化，避免并发写导致 `database is locked`。
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import uuid
from pathlib import Path

from .models import FeedItem, StoredItem, Subscription
from .timeutil import now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id                   TEXT PRIMARY KEY,
    scope_key            TEXT NOT NULL,
    platform             TEXT NOT NULL,
    target_type          TEXT NOT NULL,
    target_id            TEXT NOT NULL,
    url                  TEXT NOT NULL,
    title                TEXT NOT NULL,
    title_locked         INTEGER NOT NULL DEFAULT 0,
    enabled              INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL,
    last_checked_at      TEXT,
    last_success_at      TEXT,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    UNIQUE (scope_key, url)
);

CREATE TABLE IF NOT EXISTS items (
    subscription_id  TEXT NOT NULL,
    item_key         TEXT NOT NULL,
    revision_hash    TEXT NOT NULL,
    title            TEXT NOT NULL,
    link             TEXT,
    summary          TEXT,
    published_at     TEXT,
    updated_at       TEXT,
    first_seen_at    TEXT NOT NULL,
    pushed_at        TEXT,
    push_attempts    INTEGER NOT NULL DEFAULT 0,
    last_push_error  TEXT,
    PRIMARY KEY (subscription_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_items_pending
    ON items (subscription_id, pushed_at);
CREATE INDEX IF NOT EXISTS idx_items_recent
    ON items (subscription_id, published_at);
"""


class RssStore:
    """订阅与条目的持久化。"""

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """为老版本数据库补齐后加的列（插件升级时不会丢数据）。"""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(subscriptions)")}
        if "title_locked" not in columns:
            self._conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN title_locked INTEGER NOT NULL DEFAULT 0"
            )

    async def _run(self, fn, *args, **kwargs):
        async with self._lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    # ------------------------------------------------------------------ 订阅
    def _create_subscription(
        self,
        scope_key: str,
        platform: str,
        target_type: str,
        target_id: str,
        url: str,
        title: str,
        items: list[FeedItem],
        seen_retention: int,
        title_locked: bool = False,
    ) -> Subscription:
        now = now_iso()
        sub = Subscription(
            id=uuid.uuid4().hex[:16],
            scope_key=scope_key,
            platform=platform,
            target_type=target_type,
            target_id=target_id,
            url=url,
            title=title,
            enabled=True,
            created_at=now,
            last_checked_at=now,
            last_success_at=now,
        )
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO subscriptions
                    (id, scope_key, platform, target_type, target_id, url, title,
                     title_locked, enabled, created_at, last_checked_at, last_success_at,
                     last_error, consecutive_failures)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, NULL, 0)
                """,
                (
                    sub.id,
                    scope_key,
                    platform,
                    target_type,
                    target_id,
                    url,
                    title,
                    1 if title_locked else 0,
                    now,
                    now,
                    now,
                ),
            )
            # 首次添加不推送历史文章：直接把这些条目记为「已见」
            for item in items:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO items
                        (subscription_id, item_key, revision_hash, title, link, summary,
                         published_at, updated_at, first_seen_at, pushed_at, push_attempts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        sub.id,
                        item.item_key,
                        item.revision_hash,
                        item.title,
                        item.link,
                        item.summary,
                        item.published_at,
                        item.updated_at,
                        now,
                        now,
                    ),
                )
            self._trim_seen(sub.id, seen_retention)
        return sub

    async def create_subscription(self, **kwargs) -> Subscription:
        return await self._run(self._create_subscription, **kwargs)

    def _list_by_scope(self, scope_key: str) -> list[Subscription]:
        rows = self._conn.execute(
            "SELECT * FROM subscriptions WHERE scope_key = ? ORDER BY created_at ASC, rowid ASC",
            (scope_key,),
        ).fetchall()
        return [_row_to_subscription(row) for row in rows]

    async def list_by_scope(self, scope_key: str) -> list[Subscription]:
        return await self._run(self._list_by_scope, scope_key)

    def _count_by_scope(self, scope_key: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(1) AS n FROM subscriptions WHERE scope_key = ?", (scope_key,)
        ).fetchone()
        return int(row["n"]) if row else 0

    async def count_by_scope(self, scope_key: str) -> int:
        return await self._run(self._count_by_scope, scope_key)

    def _all_enabled(self) -> list[Subscription]:
        rows = self._conn.execute(
            "SELECT * FROM subscriptions WHERE enabled = 1 ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
        return [_row_to_subscription(row) for row in rows]

    async def all_enabled(self) -> list[Subscription]:
        return await self._run(self._all_enabled)

    def _delete_for_scope(self, scope_key: str, subscription_id: str) -> bool:
        with self._conn:
            row = self._conn.execute(
                "SELECT id FROM subscriptions WHERE id = ? AND scope_key = ?",
                (subscription_id, scope_key),
            ).fetchone()
            if not row:
                return False
            self._conn.execute("DELETE FROM items WHERE subscription_id = ?", (subscription_id,))
            self._conn.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
        return True

    async def delete_for_scope(self, scope_key: str, subscription_id: str) -> bool:
        return await self._run(self._delete_for_scope, scope_key, subscription_id)

    def _set_enabled(self, scope_key: str, subscription_id: str, enabled: bool) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE subscriptions SET enabled = ? WHERE id = ? AND scope_key = ?",
                (1 if enabled else 0, subscription_id, scope_key),
            )
        return cur.rowcount > 0

    async def set_enabled(self, scope_key: str, subscription_id: str, enabled: bool) -> bool:
        return await self._run(self._set_enabled, scope_key, subscription_id, enabled)

    # -------------------------------------------------------------- 检查状态
    def _record_check_success(self, subscription_id: str, feed_title: str | None) -> None:
        now = now_iso()
        with self._conn:
            self._conn.execute(
                """
                UPDATE subscriptions
                   SET last_checked_at = ?, last_success_at = ?, last_error = NULL,
                       consecutive_failures = 0,
                       -- 用户自定义过名称（title_locked）时不被源站标题覆盖
                       title = CASE WHEN title_locked = 1 THEN title
                                    ELSE COALESCE(NULLIF(?, ''), title) END
                 WHERE id = ?
                """,
                (now, now, (feed_title or "").strip(), subscription_id),
            )

    async def record_check_success(self, subscription_id: str, feed_title: str | None) -> None:
        await self._run(self._record_check_success, subscription_id, feed_title)

    def _record_check_failure(self, subscription_id: str, error: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE subscriptions
                   SET last_checked_at = ?, last_error = ?,
                       consecutive_failures = consecutive_failures + 1
                 WHERE id = ?
                """,
                (now_iso(), error[:300], subscription_id),
            )

    async def record_check_failure(self, subscription_id: str, error: str) -> None:
        await self._run(self._record_check_failure, subscription_id, error)

    # ------------------------------------------------------------------ 条目
    def _enqueue_items(
        self, subscription_id: str, items: list[FeedItem], seen_retention: int
    ) -> int:
        if not items:
            return 0
        now = now_iso()
        changed = 0
        with self._conn:
            for item in items:
                cur = self._conn.execute(
                    """
                    INSERT INTO items
                        (subscription_id, item_key, revision_hash, title, link, summary,
                         published_at, updated_at, first_seen_at, pushed_at, push_attempts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)
                    ON CONFLICT (subscription_id, item_key) DO UPDATE SET
                        revision_hash   = excluded.revision_hash,
                        title           = excluded.title,
                        link            = excluded.link,
                        summary         = excluded.summary,
                        published_at    = excluded.published_at,
                        updated_at      = excluded.updated_at,
                        pushed_at       = NULL,
                        push_attempts   = 0,
                        last_push_error = NULL
                    WHERE items.revision_hash <> excluded.revision_hash
                    """,
                    (
                        subscription_id,
                        item.item_key,
                        item.revision_hash,
                        item.title,
                        item.link,
                        item.summary,
                        item.published_at,
                        item.updated_at,
                        now,
                    ),
                )
                changed += max(cur.rowcount, 0)
            self._trim_seen(subscription_id, seen_retention)
        return changed

    async def enqueue_items(
        self, subscription_id: str, items: list[FeedItem], seen_retention: int
    ) -> int:
        return await self._run(self._enqueue_items, subscription_id, items, seen_retention)

    def _trim_seen(self, subscription_id: str, seen_retention: int) -> None:
        """只裁剪已推送的旧条目，待推送条目必须留着。"""
        if seen_retention <= 0:
            return
        self._conn.execute(
            """
            DELETE FROM items
             WHERE subscription_id = ?
               AND pushed_at IS NOT NULL
               AND item_key NOT IN (
                    SELECT item_key FROM items
                     WHERE subscription_id = ?
                     ORDER BY COALESCE(pushed_at, first_seen_at) DESC
                     LIMIT ?
               )
            """,
            (subscription_id, subscription_id, seen_retention),
        )

    def _pending_items(
        self, subscription_id: str, limit: int, max_failures: int
    ) -> list[StoredItem]:
        rows = self._conn.execute(
            """
            SELECT * FROM items
             WHERE subscription_id = ?
               AND pushed_at IS NULL
               AND push_attempts < ?
             ORDER BY COALESCE(published_at, first_seen_at) ASC, rowid ASC
             LIMIT ?
            """,
            (subscription_id, max_failures, limit),
        ).fetchall()
        return [_row_to_item(row) for row in rows]

    async def pending_items(
        self, subscription_id: str, limit: int, max_failures: int
    ) -> list[StoredItem]:
        return await self._run(self._pending_items, subscription_id, limit, max_failures)

    def _mark_pushed(self, subscription_id: str, item_key: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE items
                   SET pushed_at = ?, last_push_error = NULL
                 WHERE subscription_id = ? AND item_key = ?
                """,
                (now_iso(), subscription_id, item_key),
            )

    async def mark_pushed(self, subscription_id: str, item_key: str) -> None:
        await self._run(self._mark_pushed, subscription_id, item_key)

    def _record_push_failure(self, subscription_id: str, item_key: str, error: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE items
                   SET push_attempts = push_attempts + 1, last_push_error = ?
                 WHERE subscription_id = ? AND item_key = ?
                """,
                (error[:300], subscription_id, item_key),
            )

    async def record_push_failure(self, subscription_id: str, item_key: str, error: str) -> None:
        await self._run(self._record_push_failure, subscription_id, item_key, error)

    def _recent_items(self, scope_key: str, limit: int) -> list[tuple[str, StoredItem]]:
        rows = self._conn.execute(
            """
            SELECT i.*, s.title AS sub_title
              FROM items i
              JOIN subscriptions s ON s.id = i.subscription_id
             WHERE s.scope_key = ?
             ORDER BY COALESCE(i.published_at, i.updated_at, i.first_seen_at) DESC,
                      i.rowid DESC
             LIMIT ?
            """,
            (scope_key, limit),
        ).fetchall()
        return [(row["sub_title"], _row_to_item(row)) for row in rows]

    async def recent_items(self, scope_key: str, limit: int) -> list[tuple[str, StoredItem]]:
        return await self._run(self._recent_items, scope_key, limit)

    # ------------------------------------------------------------------ 维护
    def _stats(self) -> tuple[int, int]:
        subs = self._conn.execute("SELECT COUNT(1) AS n FROM subscriptions").fetchone()
        items = self._conn.execute("SELECT COUNT(1) AS n FROM items").fetchone()
        return int(subs["n"]), int(items["n"])

    async def stats(self) -> tuple[int, int]:
        return await self._run(self._stats)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.close()


def _row_to_subscription(row: sqlite3.Row) -> Subscription:
    return Subscription(
        id=row["id"],
        scope_key=row["scope_key"],
        platform=row["platform"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        url=row["url"],
        title=row["title"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"] or "",
        last_checked_at=row["last_checked_at"],
        last_success_at=row["last_success_at"],
        last_error=row["last_error"],
        consecutive_failures=int(row["consecutive_failures"] or 0),
    )


def _row_to_item(row: sqlite3.Row) -> StoredItem:
    return StoredItem(
        item_key=row["item_key"],
        revision_hash=row["revision_hash"],
        title=row["title"],
        link=row["link"],
        summary=row["summary"],
        published_at=row["published_at"],
        updated_at=row["updated_at"],
        first_seen_at=row["first_seen_at"],
        pushed_at=row["pushed_at"],
        push_attempts=int(row["push_attempts"] or 0),
        last_push_error=row["last_push_error"],
    )
