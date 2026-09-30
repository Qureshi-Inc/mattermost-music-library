"""Scrobble auto-adds are attributed to the friend whose source it is."""

import pytest

from app.jobs.scrobbler import ScrobbleWatcher


class _NoSession:
    async def get(self, *a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("known friends must not need the Mattermost API")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,uid",
    [
        ("moiz", "e3pqz61dgjyq9pjcay9zk18cbh"),
        ("Moiz", "e3pqz61dgjyq9pjcay9zk18cbh"),
        ("shahraiz", "arkxtkrs8fbwbyhpx9tgcaujxh"),
        ("themoosecompany", "a7a5hiwbe3n57koxmxbhu74jqh"),
    ],
)
async def test_known_source_resolves_without_api(source, uid):
    w = ScrobbleWatcher(queue=None)

    async def no_session():
        return _NoSession()

    w._get_session = no_session
    assert await w._resolve_mm_user_id(source) == uid
