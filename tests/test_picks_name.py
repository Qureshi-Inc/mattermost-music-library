"""Whose picks playlist an import goes in, and crediting a job made through the API."""

from unittest.mock import MagicMock

import pytest

from app.jobs.pipeline import JobPipeline


@pytest.mark.asyncio
async def test_known_friends_keep_their_playlist_name():
    # Moose is mutasif on Mattermost now; their playlist was always themoosecompany's.
    p = JobPipeline(queue=MagicMock(), mattermost_client=None)
    assert await p._picks_name("a7a5hiwbe3n57koxmxbhu74jqh") == "themoosecompany"
    assert await p._picks_name("pwdagarckfdijypad9of9ymprh") == "nooramin40"


@pytest.mark.asyncio
async def test_strangers_fall_back_to_their_id_without_mattermost():
    p = JobPipeline(queue=MagicMock(), mattermost_client=None)
    assert await p._picks_name("someoneelse123") == "someoneelse123"
    assert await p._picks_name(None) == "unknown"


@pytest.mark.asyncio
async def test_a_job_made_through_the_api_can_name_who_asked(app_client):
    auth = {"Authorization": "Bearer test-admin-token"}
    url = "https://music.apple.com/us/album/x?i=1001"
    r = await app_client.post("/api/v1/jobs", json={"url": url, "requester_user_id": "a7a5hiwbe3n57koxmxbhu74jqh"},
                              headers=auth)
    assert r.status_code in (200, 201), r.text
    assert r.json()["requester_user_id"] == "a7a5hiwbe3n57koxmxbhu74jqh"
    r = await app_client.post("/api/v1/jobs", json={"url": url}, headers=auth)
    assert r.status_code in (200, 201) and r.json()["requester_user_id"] is None
    r = await app_client.post("/api/v1/jobs", json={"url": url, "requester_user_id": "../evil"}, headers=auth)
    assert r.status_code == 422
