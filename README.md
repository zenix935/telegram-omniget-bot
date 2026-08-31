# Telegram Media Downloader Bot (Pyrogram MTProto 2GB + OmniGet / yt-dlp)

A production-ready, highly resilient Telegram Bot built with **Pyrogram (MTProto)**, **yt-dlp/omniget-cli**, and **FFmpeg**. 
By utilizing Telegram's native **MTProto protocol**, the bot can upload large files **up to 2,000 MB (2 GB)** without requiring a self-hosted Telegram Bot API server.

---

## 🌟 Key Features

- **🚀 Native MTProto 2GB Uploads:** Upload media files up to 2GB directly through Telegram MTProto protocol with high throughput encryption (`tgcrypto`).
- **Dual-Mode Operation:**
  - **Direct Messages (DMs):** Interactive format selection (Best Video MP4, 720p MP4, MP3 Audio, Cancel).
  - **Groups / Supergroups / Forum Topics:** Silent, zero-clutter auto-mode. Automatically replies in the same topic, sends media directly replying to the link, and cleans up temporary status messages.
- **Group Admin Controls:**
  - `/settings` or `/config`: Interactive panel to toggle auto-download on/off and configure default quality.
  - `/toggle_download`: Instant toggle command for group admins.
- **Anti-Crash & Resource Guardrails:**
  - **Docker Resource Limits:** Hard CPU ceiling (`2.0 CPUs`) and Memory ceiling (`2048 MB RAM`) with non-root security.
  - **FFmpeg Thread Limiter:** Limits FFmpeg transcoding/remuxing to 2 threads (`-threads 2`) to avoid 100% CPU lockups.
  - **Pre-download Disk Check:** Checks free disk space before every download (`>= 3.0 GB` required).
  - **Isolated UUID Workspaces:** Every job executes inside `/tmp/downloads/job_<uuid>/` with guaranteed `try ... finally` cleanup.
  - **Automated Janitor:** Periodic background loop (every 15 min) that purges orphaned files older than 30 minutes.
  - **Multi-Tier Rate Limiting & Concurrency:**
    - Global maximum: 3 concurrent downloads.
    - Per-user maximum: 1 concurrent task.
    - Per-group maximum: 2 concurrent tasks.
    - Token-bucket rate limiter: 5 req/min in DMs, 10 req/min in Groups.
  - **Subprocess & SSRF Safety:**
    - Never uses `shell=True`; uses `asyncio.create_subprocess_exec` with sanitized arguments.
    - Hard process execution timeout (default 600s / 10 minutes) with graceful `SIGTERM` -> `SIGKILL` termination.
    - SSRF protection: Rejects loopback (`127.0.0.1`), private networks (`10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`), and internal DNS targets.
  - **Telegram Flood Protection:**
    - Throttles download and MTProto upload progress message edits to at most once every 4.0 seconds.

---

## ⚙️ Prerequisites: MTProto Credentials & BotFather Setup

### 1. Obtain MTProto Credentials (API_ID & API_HASH)
1. Log in to [https://my.telegram.org](https://my.telegram.org) with your phone number.
2. Navigate to **API development tools**.
3. Create an application (e.g. `OmniGetBot`).
4. Copy your `api_id` and `api_hash`.

### 2. Obtain Bot Token
1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Create a new bot with `/newbot` and copy your `BOT_TOKEN`.

### 3. Group Chat Privacy Requirement
To allow the bot to read media links automatically in Telegram Groups without requiring `@mentions`:
1. In [@BotFather](https://t.me/BotFather), send `/setprivacy`.
2. Select your bot.
3. Choose **Disable**.
4. *(Alternative)* If privacy remains enabled, promote the bot to an **Administrator** in your group chat.
5. For Supergroups with **Topics (Forums)**, ensure the bot has permission to post in all topics.

---

## 🚀 Quickstart & Deployment Guide (Ubuntu / Debian VPS)

### 1. Clone & Setup Configuration
```bash
git clone <repository_url> telegram-omniget-bot
cd telegram-omniget-bot

# Copy sample configuration
cp .env.example .env

# Edit .env with your credentials
nano .env
```

### 2. Configure `.env`
```ini
API_ID=1234567
API_HASH=abcdef0123456789abcdef0123456789
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ123456789
DOWNLOAD_DIR=/tmp/downloads
MIN_FREE_DISK_GB=3.0
MAX_GLOBAL_CONCURRENT=3
MAX_USER_CONCURRENT=1
MAX_GROUP_CONCURRENT=2
USER_RATE_LIMIT_PER_MINUTE=5
GROUP_RATE_LIMIT_PER_MINUTE=10
DOWNLOAD_TIMEOUT_SECONDS=600
FFMPEG_THREADS=2
MAX_FILE_SIZE_MB=2000
ADMIN_IDS=123456789
```

### 3. Run with Docker Compose (Recommended)
```bash
# Build and start container in detached mode
docker compose up -d --build

# View real-time logs
docker compose logs -f
```

### 4. Running Natively (Without Docker)
```bash
# Install system ffmpeg
sudo apt-get update && sudo apt-get install -y ffmpeg

# Create python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run bot
python main.py
```

---

## 🧪 Testing

Run the automated test suite with `pytest`:
```bash
source .venv/bin/activate
pytest -v
```

---

## 📂 Project Architecture

```
telegram-omniget-bot/
├── .env.example             # Documented environment template (MTProto + Guardrails)
├── .gitignore               # Git ignore rules
├── Dockerfile               # Multi-stage hardened runner (non-root)
├── docker-compose.yml       # Production resource limits (2 CPU / 2GB RAM)
├── requirements.txt         # Pinned python dependencies (Pyrogram, TgCrypto, etc.)
├── config.py                # Pydantic Settings configuration & validation
├── main.py                  # Pyrogram MTProto bot entrypoint & lifecycle handlers
├── bot/
│   ├── handlers/
│   │   ├── common.py        # /help command handler
│   │   ├── group.py         # Group link listener, topic router & admin commands
│   │   └── private.py       # DM handlers (/start, format selector callbacks)
│   ├── keyboards/
│   │   └── inline.py        # Inline keyboards (Format selector & admin settings)
│   ├── middlewares/
│   │   └── rate_limit.py    # Multi-tier token-bucket rate limiting & filters
│   └── utils/
│       └── helpers.py       # Link extractors, progress bars, MTProto upload callback
├── core/
│   ├── cleaner.py           # Free disk space checker & 15m Janitor loop
│   ├── downloader.py        # yt-dlp & omniget-cli async subprocess wrapper
│   ├── queue.py             # Concurrency manager & token bucket limiter
│   └── security.py          # SSRF prevention, IP validation, URL checks
└── tests/
    ├── test_cleaner.py      # Janitor and disk space test suite
    ├── test_downloader.py   # Downloader engine and probe test suite
    ├── test_handlers.py     # Group admin & handler test suite
    ├── test_queue.py        # Multi-tier concurrency and rate limiter tests
    ├── test_security.py     # SSRF and URL validation tests
    └── test_utils.py        # Link extractor and formatting tests
```

---

## 🛡️ Security & VPS Safety Specs

| Feature | Specification | Behavior |
| :--- | :--- | :--- |
| **Max File Size** | `2000 MB (2 GB)` | Enabled natively by Pyrogram MTProto protocol |
| **CPU Limit** | `2.0 CPUs` (Compose) + `-threads 2` (FFmpeg) | Prevents FFmpeg from starving server CPU |
| **Memory Limit** | `2048 MB` (RAM ceiling) | OOM prevention during remuxing large video files |
| **Disk Space Guard** | `shutil.disk_usage >= 3.0 GB` | Rejects new downloads if disk space is below safety threshold |
| **Cleanup Guarantee** | `try ... finally: cleanup()` | Immediate purge of temporary directory on completion/failure |
| **Janitor Loop** | Runs every 15 minutes | Purges any orphaned folders in `/tmp/downloads` older than 30 min |
| **Execution Timeout** | Max 600 seconds (10 mins) | Subprocess killed cleanly via `SIGTERM` -> `SIGKILL` if hung |
| **Subprocess Execution** | `asyncio.create_subprocess_exec` | Zero raw shell injection (`shell=True` forbidden) |
| **SSRF Filter** | Private IP + localhost blocklist | Blocks access to internal networks, metadata services, and local ports |
