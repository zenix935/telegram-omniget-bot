import asyncio
import pytest
from core.queue import ConcurrencyManager, TokenBucketLimiter


@pytest.mark.asyncio
async def test_token_bucket_limiter():
    # Capacity: 3 requests per 60s
    limiter = TokenBucketLimiter(capacity=3, window_seconds=60.0)
    key = "user:123"

    assert await limiter.acquire(key) is True
    assert await limiter.acquire(key) is True
    assert await limiter.acquire(key) is True
    # 4th should be rejected
    assert await limiter.acquire(key) is False

    retry_after = await limiter.get_retry_after(key)
    assert retry_after > 0.0

    # Another key is independent
    assert await limiter.acquire("user:456") is True


@pytest.mark.asyncio
async def test_concurrency_manager_user_limits():
    # Global 3, user 1, group 2
    manager = ConcurrencyManager(max_global=3, max_user=1, max_group=2)

    can_start, err = await manager.can_start(user_id=1, is_group=False)
    assert can_start is True
    assert err is None

    async with manager.acquire(user_id=1, is_group=False):
        # Within block, user has 1 active task -> second task must be rejected by can_start
        can_start, err = await manager.can_start(user_id=1, is_group=False)
        assert can_start is False
        assert "active download task running" in err

        # Different user can start
        can_start_2, _ = await manager.can_start(user_id=2, is_group=False)
        assert can_start_2 is True

    # After exiting context, slot is freed
    can_start_after, err_after = await manager.can_start(user_id=1, is_group=False)
    assert can_start_after is True
    assert err_after is None


@pytest.mark.asyncio
async def test_concurrency_manager_group_limits():
    manager = ConcurrencyManager(max_global=5, max_user=2, max_group=2)
    group_id = -1001234567

    async with manager.acquire(user_id=10, chat_id=group_id, is_group=True):
        async with manager.acquire(user_id=11, chat_id=group_id, is_group=True):
            # Group has 2 active tasks -> 3rd must be rejected
            can_start, err = await manager.can_start(user_id=12, chat_id=group_id, is_group=True)
            assert can_start is False
            assert "active downloads in progress" in err

    # All freed
    can_start, _ = await manager.can_start(user_id=12, chat_id=group_id, is_group=True)
    assert can_start is True
