# AGENTS.md

## Cursor Cloud specific instructions

### Overview

MT5 Telegram Notifier is a Python/CustomTkinter desktop GUI that monitors MetaTrader 5 trade events from a filesystem directory and sends Telegram notifications. There is no database, no Docker, no build step, and no test suite.

### Running the app (headless)

The app requires a display server. On the Cloud VM use Xvfb:

```bash
Xvfb :99 -screen 0 1280x720x24 &
DISPLAY=:99 python3 -m notifier.app
```

`python3-tk` (system package) must be installed for CustomTkinter to work. The update script handles `pip install -r requirements.txt`; `python3-tk` is a one-time system dependency already present on the VM.

### Configuration

Copy `config.example.json` to `config.json` and set `common_files_path` to a Linux directory (default is a Windows path). For testing without a real Telegram bot, leave `telegram_bot_token` and `telegram_chat_id` empty — the app starts normally and shows "Токен или chat_id не заданы" in the status bar.

### Simulating MT5 events for testing

Since MT5 terminals are not available on Linux, you can exercise the watcher by dropping JSON files into the `tg_events/` subdirectory of `common_files_path`:

- **Heartbeat**: `heartbeat_<chart_id>.json` — picked up every ~6 seconds (3 poll cycles).
- **Trade event**: `evt_<timestamp>_<seq>.json` — picked up within one poll cycle (~2 seconds) and moved to `tg_events/processed/`.

See `notifier/watcher.py` for the expected JSON schemas.

### Linting / Testing

No formal linter or test framework is configured. Use `python3 -m pyflakes notifier/` for basic lint checks and `python3 -m py_compile <file>` for syntax verification.
