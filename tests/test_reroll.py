"""Tests for rerolling a library track (app/api/reroll.py)."""

from pathlib import Path
from unittest.mock import patch

import pytest
from mutagen.id3 import ID3, TALB, TIT2, TLEN, TPE1

from app.api import reroll

# A single silent MPEG-1 Layer III frame, repeated: enough for mutagen to parse.
FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


def write_mp3(path: Path, frames: int = 40) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FRAME * frames)
    return path


class TestSourceUrl:
    def test_youtube_and_soundcloud_allowed(self):
        assert reroll.source_url("https://www.youtube.com/watch?v=abc").startswith("https://www.youtube.com")
        assert reroll.source_url(" https://youtu.be/abc ") == "https://youtu.be/abc"
        assert reroll.source_url("https://soundcloud.com/a/b")

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd", "http://169.254.169.254/", "https://evil.com/watch?v=1",
        "https://youtube.com.evil.com/x", "javascript:alert(1)", "",
    ])
    def test_everything_else_refused(self, url):
        with pytest.raises(ValueError):
            reroll.source_url(url)


class TestLibraryFile:
    def test_existing_mp3(self, tmp_path):
        write_mp3(tmp_path / "A" / "B" / "Song.mp3")
        with patch.object(reroll, "get_settings") as gs:
            gs.return_value.music_base_path = tmp_path
            assert reroll.library_file("A/B/Song.mp3") == (tmp_path / "A/B/Song.mp3").resolve()

    @pytest.mark.parametrize("rel", ["../outside.mp3", "A/B/missing.mp3", "A/B/cover.jpg", "/etc/passwd"])
    def test_refused(self, tmp_path, rel):
        write_mp3(tmp_path / "A" / "B" / "Song.mp3")
        (tmp_path / "A" / "B" / "cover.jpg").write_bytes(b"x")
        write_mp3(tmp_path.parent / "outside.mp3")
        with patch.object(reroll, "get_settings") as gs:
            gs.return_value.music_base_path = tmp_path
            with pytest.raises(ValueError):
                reroll.library_file(rel)


class TestSwapIn:
    def test_keeps_tags_backs_up_and_replaces(self, tmp_path):
        lib = tmp_path / "music"
        target = write_mp3(lib / "Artist" / "Album" / "Song.mp3", frames=40)
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Song"))
        tags.add(TPE1(encoding=3, text="Artist"))
        tags.add(TALB(encoding=3, text="Album"))
        tags.add(TLEN(encoding=3, text="1000"))
        tags.save(str(target))
        old_bytes = target.read_bytes()
        new = write_mp3(tmp_path / "dl" / "x.mp3", frames=90)

        with patch.object(reroll, "get_settings") as gs:
            gs.return_value.music_base_path = lib
            backup = reroll.swap_in(new, target.resolve(), tmp_path / "replaced")

        assert backup.read_bytes() == old_bytes
        assert backup.relative_to(tmp_path / "replaced").parts[1:] == ("Artist", "Album", "Song.mp3")
        got = ID3(str(target))
        assert str(got["TIT2"]) == "Song" and str(got["TALB"]) == "Album"
        assert "TLEN" not in got  # the old length would be wrong for the new audio
        assert target.stat().st_size > len(old_bytes)  # the new, longer audio
        assert not list(target.parent.glob(".*.reroll"))


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_need_the_admin_token(self, app_client):
        r = await app_client.post("/api/v1/tracks/research", json={"title": "x"})
        assert r.status_code in (401, 403)
        r = await app_client.post("/api/v1/tracks/replace", json={"path": "a.mp3", "url": "https://youtu.be/x"})
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_replace_rejects_a_bad_link_before_downloading(self, app_client):
        auth = {"Authorization": "Bearer test-admin-token"}
        with patch.object(reroll, "_download") as dl:
            r = await app_client.post("/api/v1/tracks/replace", headers=auth,
                                      json={"path": "a.mp3", "url": "https://evil.com/x"})
        assert r.status_code == 422
        dl.assert_not_called()

    @pytest.mark.asyncio
    async def test_research_needs_something_to_search(self, app_client):
        auth = {"Authorization": "Bearer test-admin-token"}
        r = await app_client.post("/api/v1/tracks/research", headers=auth, json={})
        assert r.status_code == 422
