"""存储层测试：去重、修订识别、重试上限、保留策略与会话隔离。"""

from __future__ import annotations

import asyncio

from astrbot_plugin_rss_pusher.core.models import FeedItem

SCOPE_A = "qq_official:GroupMessage:g1"
SCOPE_B = "qq_official:GroupMessage:g2"


def _item(
    key: str, hash_: str = "h1", title: str = "标题", published: str = "2026-10-20T02:00:00+00:00"
) -> FeedItem:
    return FeedItem(
        item_key=key,
        revision_hash=hash_,
        title=title,
        link=f"https://example.com/{key}",
        summary="摘要",
        published_at=published,
        updated_at=published,
        source_order=0,
    )


def _create(store, scope=SCOPE_A, url="https://example.com/feed.xml", items=(), title_locked=False):
    return asyncio.run(
        store.create_subscription(
            scope_key=scope,
            platform="qq_official",
            target_type="group",
            target_id="g1",
            url=url,
            title="测试订阅",
            items=list(items),
            seen_retention=500,
            title_locked=title_locked,
        )
    )


def test_create_marks_existing_items_as_seen(store):
    sub = _create(store, items=[_item("a"), _item("b")])
    pending = asyncio.run(store.pending_items(sub.id, 10, 3))
    assert pending == []
    recent = asyncio.run(store.recent_items(SCOPE_A, 10))
    assert len(recent) == 2


def test_enqueue_only_new_items(store):
    sub = _create(store, items=[_item("a")])
    assert asyncio.run(store.enqueue_items(sub.id, [_item("a"), _item("b")], 500)) == 1
    pending = asyncio.run(store.pending_items(sub.id, 10, 3))
    assert [item.item_key for item in pending] == ["b"]


def test_revision_change_requeues_pushed_item(store):
    sub = _create(store, items=[_item("a", "h1")])
    asyncio.run(store.enqueue_items(sub.id, [_item("a", "h2")], 500))
    pending = asyncio.run(store.pending_items(sub.id, 10, 3))
    assert [item.item_key for item in pending] == ["a"]

    asyncio.run(store.mark_pushed(sub.id, "a"))
    assert asyncio.run(store.pending_items(sub.id, 10, 3)) == []

    # 内容再次变化 → 重新排队
    asyncio.run(store.enqueue_items(sub.id, [_item("a", "h3")], 500))
    assert [item.item_key for item in asyncio.run(store.pending_items(sub.id, 10, 3))] == ["a"]


def test_push_failures_stop_after_limit(store):
    sub = _create(store)
    asyncio.run(store.enqueue_items(sub.id, [_item("a")], 500))
    for _ in range(3):
        asyncio.run(store.record_push_failure(sub.id, "a", "boom"))
    assert asyncio.run(store.pending_items(sub.id, 10, 3)) == []


def test_pending_respects_limit_and_uses_oldest_first(store):
    sub = _create(store)
    items = [
        _item("a", published="2026-10-18T00:00:00+00:00"),
        _item("b", published="2026-10-19T00:00:00+00:00"),
        _item("c", published="2026-10-20T00:00:00+00:00"),
    ]
    asyncio.run(store.enqueue_items(sub.id, items, 500))
    pending = asyncio.run(store.pending_items(sub.id, 2, 3))
    assert [item.item_key for item in pending] == ["a", "b"]


def test_retention_keeps_pending_items(store):
    sub = _create(store)
    # 两条已推送 + 一条待推送，保留数设为 1
    asyncio.run(store.enqueue_items(sub.id, [_item("old"), _item("new"), _item("pending")], 500))
    asyncio.run(store.mark_pushed(sub.id, "old"))
    asyncio.run(store.mark_pushed(sub.id, "new"))
    asyncio.run(store.enqueue_items(sub.id, [_item("pending2")], 1))
    pending = asyncio.run(store.pending_items(sub.id, 10, 3))
    assert {item.item_key for item in pending} == {"pending", "pending2"}


def test_delete_is_scoped(store):
    sub_a = _create(store, scope=SCOPE_A)
    sub_b = _create(store, scope=SCOPE_B, url="https://example.com/other.xml")
    assert asyncio.run(store.delete_for_scope(SCOPE_A, sub_b.id)) is False
    assert asyncio.run(store.delete_for_scope(SCOPE_A, sub_a.id)) is True
    assert asyncio.run(store.list_by_scope(SCOPE_A)) == []
    assert len(asyncio.run(store.list_by_scope(SCOPE_B))) == 1


def test_toggle_enabled_and_check_status(store):
    sub = _create(store)
    assert asyncio.run(store.set_enabled(SCOPE_A, sub.id, False)) is True
    assert asyncio.run(store.all_enabled()) == []
    assert asyncio.run(store.set_enabled(SCOPE_A, sub.id, True)) is True
    assert len(asyncio.run(store.all_enabled())) == 1

    asyncio.run(store.record_check_failure(sub.id, "HTTP 500"))
    (loaded,) = asyncio.run(store.list_by_scope(SCOPE_A))
    assert loaded.failed and loaded.consecutive_failures == 1

    asyncio.run(store.record_check_success(sub.id, "新标题"))
    (loaded,) = asyncio.run(store.list_by_scope(SCOPE_A))
    assert not loaded.failed and loaded.title == "新标题"


def test_custom_title_is_preserved(store):
    sub = _create(store, title_locked=True)
    asyncio.run(store.record_check_success(sub.id, "源站标题"))
    (loaded,) = asyncio.run(store.list_by_scope(SCOPE_A))
    assert loaded.title == "测试订阅"


def test_recent_items_sorted_desc(store):
    sub = _create(store)
    asyncio.run(
        store.enqueue_items(
            sub.id,
            [
                _item("a", published="2026-10-18T00:00:00+00:00"),
                _item("c", published="2026-10-20T00:00:00+00:00"),
                _item("b", published="2026-10-19T00:00:00+00:00"),
            ],
            500,
        )
    )
    recent = asyncio.run(store.recent_items(SCOPE_A, 3))
    assert [item.item_key for _, item in recent] == ["c", "b", "a"]


def test_duplicate_url_raises(store):
    _create(store)
    try:
        _create(store)
    except Exception as exc:  # sqlite3.IntegrityError
        assert "UNIQUE" in str(exc).upper()
    else:  # pragma: no cover
        raise AssertionError("重复订阅应该触发唯一约束")
