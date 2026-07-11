# Slaptastic

A self-hosted music library bot that watches a Mattermost channel for music links, resolves metadata from streaming platforms, finds the best matching source on YouTube, downloads and converts to MP3, and organizes your library with proper tags. Integrated with Jellyfin for streaming playback.

Drop a Spotify, Apple Music, or YouTube link in your team chat and Slaptastic handles the rest.

## Features

- **Multi-platform link detection** -- Spotify, Apple Music, and YouTube URLs are recognized automatically
- **Metadata resolution** -- Fetches track title, artist, album, duration, and artwork from the source platform's API
- **Intelligent candidate scoring** -- Searches YouTube, scores results on title similarity, duration match, channel reputation, view count, and unwanted content detection
- **Configurable approval workflow** -- High-confidence matches auto-approve; borderline matches prompt for manual selection
- **High-quality downloads** -- yt-dlp with FFmpeg transcoding to 320kbps MP3
- **ID3 tagging** -- Embeds artist, title, album, track number, and cover art via mutagen
- **Organized library** -- Files stored as `Artist/Album/Track.mp3`
- **Jellyfin integration** -- Triggers library refresh after each download so new tracks appear immediately
- **Thread-based UX** -- All status updates and interactions happen in Mattermost threads, keeping the channel clean
- **Admin API** -- RESTful endpoints for job management and library queries

## Architecture

```
Mattermost (WebSocket) --> FastAPI App --> Metadata Resolvers (Spotify/Apple Music/YouTube)
                                      --> YouTube Search + Candidate Scorer
                                      --> yt-dlp Downloader + FFmpeg
                                      --> ID3 Tagger (mutagen)
                                      --> File Organizer (Artist/Album/Track.mp3)
                                      --> Jellyfin Library Refresh
                                      --> SQLite Job Database
```

The application connects to Mattermost via WebSocket on startup, listens for messages containing music URLs, and processes them through an async pipeline. Each import is tracked as a "job" with state transitions: `pending -> resolving -> searching -> scoring -> awaiting_approval -> downloading -> tagging -> organizing -> complete`.

## Prerequisites

- **Python 3.12+**
- **FFmpeg** -- required by yt-dlp for audio extraction and transcoding
- **Docker** (optional) -- for containerized deployment

## Quick Start

### Docker Compose (recommended)

1. Clone the repository and create your environment file:

```bash
cp .env.example .env
# Edit .env with your credentials (see Configuration below)
```

2. Start the stack:

```bash
docker compose up -d
```

This starts both the Slaptastic bot and a Jellyfin instance with the music volume mounted read-only.

3. Verify the service is healthy:

```bash
curl http://localhost:8080/health
# {"status": "healthy"}
```

### Local Development

```bash
make dev          # Install with dev dependencies
make test         # Run test suite
make lint         # Run ruff linter
make typecheck    # Run mypy
```

## Configuration

All settings are configured via environment variables or a `.env` file. Variables are case-insensitive.

### Mattermost

| Variable | Default | Description |
|----------|---------|-------------|
| `MATTERMOST_URL` | `http://localhost:8065` | Mattermost server URL |
| `MATTERMOST_TOKEN` | *(required)* | Bot personal access token |
| `MATTERMOST_CHANNEL` | *(required)* | Channel ID the bot monitors |
| `BOT_USERNAME` | `slaptastic` | Bot display name in Mattermost |

### Jellyfin

| Variable | Default | Description |
|----------|---------|-------------|
| `JELLYFIN_URL` | `http://localhost:8096` | Jellyfin server URL |
| `JELLYFIN_TOKEN` | *(required)* | Jellyfin API key for library refresh |

### Streaming Platform APIs

| Variable | Default | Description |
|----------|---------|-------------|
| `SPOTIFY_CLIENT_ID` | *(empty)* | Spotify app client ID for metadata resolution |
| `SPOTIFY_CLIENT_SECRET` | *(empty)* | Spotify app client secret |
| `APPLE_MUSIC_TOKEN` | *(empty)* | Apple Music developer token (JWT) |

### Music Library

| Variable | Default | Description |
|----------|---------|-------------|
| `MUSIC_BASE_PATH` | `/music` | Base path for organized music files |
| `DOWNLOAD_ENABLED` | `false` | Master switch for downloading. Set to `true` to enable. |
| `MP3_BITRATE` | `320` | Target MP3 bitrate in kbps |

### Scoring Thresholds

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTO_APPROVE_THRESHOLD` | `0.90` | Score at or above this value auto-approves the top candidate |
| `MANUAL_REVIEW_THRESHOLD` | `0.70` | Score below this value requires manual candidate selection |

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_URL` | `sqlite+aiosqlite:///./slaptastic.db` | Async SQLAlchemy database URL |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_API_TOKEN` | *(empty)* | Bearer token for protected admin API endpoints |

## Mattermost Setup

1. **Create a bot account** in Mattermost:
   - Go to **Integrations > Bot Accounts > Add Bot Account**
   - Username: `slaptastic`
   - Display name: `Slaptastic`
   - Role: Member (no admin required)

2. **Generate a personal access token**:
   - Go to the bot's profile > **Security > Personal Access Tokens**
   - Create a token and copy it -- this is your `MATTERMOST_TOKEN`

3. **Get the channel ID**:
   - Open the channel you want the bot to monitor
   - Click the channel name header > **View Info**
   - Copy the Channel ID -- this is your `MATTERMOST_CHANNEL`

4. **Add the bot to the channel**:
   - In the target channel, type `/invite @slaptastic`

5. **Enable WebSocket** (System Console):
   - Ensure WebSocket connections are allowed for bot accounts
   - Under **Environment > Web Server**, confirm WebSocket is not restricted

## Jellyfin Setup

1. **Create a music library** in Jellyfin:
   - Go to **Dashboard > Libraries > Add Media Library**
   - Content type: **Music**
   - Folders: Add the path where Slaptastic writes files (the Docker volume maps to `/media/music`)

2. **Generate an API key**:
   - Go to **Dashboard > API Keys > Add**
   - Name: `slaptastic`
   - Copy the key -- this is your `JELLYFIN_TOKEN`

3. **Configure metadata providers** (recommended):
   - Enable MusicBrainz and AudioDB for album art fallback
   - Set scan interval to manual (Slaptastic triggers refreshes on demand)

## Finamp Setup

[Finamp](https://github.com/jmshrv/finamp) is an open-source Jellyfin music client for iOS and Android.

1. **Install Finamp** from the App Store or Play Store

2. **Connect to your Jellyfin server**:
   - Open Finamp and enter your Jellyfin server URL
   - Log in with your Jellyfin credentials

3. **Select your music library**:
   - Choose the music library you created above
   - Enable offline download if desired

4. **Automatic updates**:
   - When Slaptastic adds new tracks and triggers a library refresh, Finamp picks them up on next sync
   - Enable background refresh in Finamp settings for near-real-time updates

## Coolify Deployment

[Coolify](https://coolify.io) is a self-hostable PaaS (similar to Heroku/Vercel) for Docker-based apps.

1. **Create a new service** in Coolify:
   - Source: Git repository (point to your Slaptastic repo)
   - Build pack: **Dockerfile**

2. **Configure environment variables**:
   - Add all required variables from the Configuration section above
   - Set `DOWNLOAD_ENABLED=true` when ready to go live

3. **Configure storage**:
   - Add a persistent volume mounted at `/music` for the music library
   - Add a persistent volume mounted at `/app/data` for the SQLite database

4. **Set the exposed port** to `8080`

5. **Configure health check**:
   - Path: `/health`
   - Interval: 30s

6. **Deploy** and verify:
   - Check logs for "Mattermost WebSocket listener started"
   - Confirm `/ready` returns `{"status": "ready", "database": "connected"}`

7. **Networking**:
   - Ensure Coolify can reach your Mattermost and Jellyfin instances
   - If Jellyfin is also on Coolify, use Docker network names instead of public URLs

## Usage

### Posting a Music Link

Drop any supported link in the monitored channel:

```
https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT
```

Slaptastic will reply in a thread with progress updates:

> Resolving metadata for Spotify track...
> Found: "Never Gonna Give You Up" by Rick Astley
> Searching YouTube for matching audio...
> Top candidate scored 0.94 -- auto-approved
> Downloading and converting to MP3...
> Tagged and organized: Rick Astley/Whenever You Need Somebody/Never Gonna Give You Up.mp3
> Jellyfin library refresh triggered.

### Bot Commands

All commands are issued by mentioning `@slaptastic` in the channel or thread:

| Command | Description |
|---------|-------------|
| `@slaptastic status` | Check the status of your current job |
| `@slaptastic candidates` | View scored candidates with match details |
| `@slaptastic add 1` | Select candidate #1 from the list |
| `@slaptastic approve` | Approve the selected candidate for download |
| `@slaptastic cancel` | Cancel the active job |
| `@slaptastic retry` | Retry a failed job |

### Workflow Examples

**Auto-approved (score >= 0.90):**
Post link -> resolves metadata -> searches YouTube -> top candidate scores 0.94 -> downloads automatically.

**Manual review (score between 0.70 and 0.90):**
Post link -> resolves metadata -> searches YouTube -> top candidate scores 0.82 -> bot posts candidate list -> you run `@slaptastic add 1` -> `@slaptastic approve` -> downloads.

**Low confidence (score < 0.70):**
Post link -> resolves metadata -> searches YouTube -> best score is 0.65 -> bot posts candidates with a warning -> manual selection required.

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Liveness check |
| `GET` | `/ready` | None | Readiness check (verifies DB connectivity) |
| `GET` | `/api/v1/jobs` | Bearer token | List import jobs with filtering |
| `GET` | `/api/v1/tracks` | Bearer token | List tracks in the library |

Protected endpoints require the `Authorization: Bearer <ADMIN_API_TOKEN>` header.

## Development

```bash
# Install dev dependencies
make dev

# Run tests
make test

# Run tests with coverage report
make test-cov

# Lint (ruff)
make lint

# Auto-format
make format

# Type checking (mypy)
make typecheck

# Build Docker image locally
make docker-build

# Start Docker Compose stack
make docker-up

# Stop Docker Compose stack
make docker-down

# Clean build artifacts
make clean
```

### Project Structure

```
app/
  main.py            # FastAPI app, lifespan, health endpoints
  config.py          # Pydantic settings (all env vars)
  database.py        # SQLAlchemy async engine and session
  api/               # REST API routes
  mattermost/        # WebSocket listener, command handler, message formatter
  resolvers/         # Platform-specific metadata resolvers (Spotify, Apple Music, YouTube)
  matching/          # YouTube search and candidate scoring
  downloader/        # yt-dlp wrapper and FFmpeg integration
  library/           # ID3 tagging and file organization
  security/          # Auth middleware and input validation
  models/            # SQLAlchemy ORM models
  schemas/           # Pydantic request/response schemas
tests/               # pytest test suite
docker/              # Docker-related configuration
```

## Scoring System

When Slaptastic searches YouTube for a track, it scores each result on six weighted factors:

| Factor | Weight | What It Measures |
|--------|--------|-----------------|
| Title similarity | 35% | Fuzzy string match (SequenceMatcher) of candidate title against "Artist - Title" |
| Duration match | 25% | How close the video duration is to the expected track length |
| Channel reputation | 15% | Whether the channel looks official (VEVO, Records, artist name) |
| "Official" in title | 10% | Bonus for "Official Audio", "Official Video", etc. |
| Unwanted content penalty | 10% | Penalty for live, remix, cover, karaoke, slowed, nightcore when not expected |
| View count | 5% | Logarithmic scale favoring higher view counts |

The final score is normalized to 0.0-1.0. The `AUTO_APPROVE_THRESHOLD` and `MANUAL_REVIEW_THRESHOLD` settings determine the workflow path.

### Duration scoring detail

- Within 5 seconds: 1.0 (perfect match)
- Within 10 seconds: 0.7-1.0 (linear interpolation)
- Within 15 seconds: 0.4-0.7 (linear interpolation)
- Beyond 15 seconds: 0.1 (likely wrong version or a video mix)

## Security Considerations

- **Bot token**: The Mattermost token grants message read/write access. Store it securely and rotate periodically.
- **API token**: The `ADMIN_API_TOKEN` protects management endpoints. Use a strong random value (32+ characters).
- **Streaming API credentials**: Spotify and Apple Music tokens are used for read-only metadata lookups. They cannot modify playlists or accounts.
- **Download safety**: `DOWNLOAD_ENABLED` defaults to `false`. Enable only when you have verified the rest of the configuration is correct.
- **Input validation**: All incoming URLs and commands are validated before processing. The bot only processes URLs from recognized platforms.
- **Non-root container**: The Docker image runs as a non-root `slaptastic` user.
- **Network isolation**: In Docker Compose, Jellyfin mounts the music volume as read-only (`ro`). Only Slaptastic writes to the music directory.

## License

MIT
