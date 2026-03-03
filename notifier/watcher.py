"""Background thread that monitors tg_events/ folder for new events and heartbeats."""

import json
import logging
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue

from .formatter import format_event, format_log_line
from .telegram_sender import TelegramSender

log = logging.getLogger(__name__)


class TerminalStatus:
    """Tracks heartbeat data for a single chart (EA instance)."""

    def __init__(self, data: dict):
        self.terminal_id: str = data.get("terminal_id", "")
        self.terminal_name: str = data.get("terminal_name", "")
        self.chart_id: str = data.get("chart_id", "")
        self.symbol: str = data.get("symbol", "")
        self.timeframe: str = data.get("timeframe", "")
        self.account_login: int = data.get("account_login", 0)
        self.account_server: str = data.get("account_server", "")
        self.balance: float = data.get("balance", 0)
        self.equity: float = data.get("equity", 0)
        self.open_positions: int = data.get("open_positions", 0)
        self.last_seen = datetime.now()
        self.raw = data


class TradingViewSource:
    """Tracks heartbeat data for a TradingView indicator/symbol/timeframe combo."""

    def __init__(self, data: dict):
        self.indicator: str = data.get("indicator", "")
        self.symbol: str = data.get("symbol", "")
        self.exchange: str = data.get("exchange", "")
        self.timeframe: str = data.get("timeframe", "")
        self.last_signal: str = data.get("last_signal", "")
        self.last_signal_time: str = data.get("last_signal_time", "")
        self.price: float = data.get("price", 0)
        self.last_seen = datetime.now()


class EventWatcher:
    """Watches tg_events/ directory and dispatches events."""

    def __init__(
        self,
        events_dir: Path,
        sender: TelegramSender,
        gui_queue: Queue,
        poll_interval: float = 2.0,
        heartbeat_timeout: float = 60.0,
        heartbeat_dead: float = 120.0,
        retention_days: int = 7,
    ):
        self._events_dir = events_dir
        self._processed_dir = events_dir / "processed"
        self._sender = sender
        self._gui_queue = gui_queue
        self._poll_interval = poll_interval
        self._heartbeat_timeout = heartbeat_timeout
        self._heartbeat_dead = heartbeat_dead
        self._retention_days = retention_days

        self._terminals: dict[str, TerminalStatus] = {}
        self._tv_sources: dict[str, TradingViewSource] = {}
        self._known_events: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def terminals(self) -> dict[str, TerminalStatus]:
        with self._lock:
            return dict(self._terminals)

    @property
    def tv_sources(self) -> dict[str, TradingViewSource]:
        with self._lock:
            return dict(self._tv_sources)

    def start(self):
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._processed_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        self._scan_existing_events()

        heartbeat_counter = 0
        cleanup_counter = 0
        while not self._stop.is_set():
            self._process_new_events()

            heartbeat_counter += 1
            if heartbeat_counter >= 3:
                self._read_heartbeats()
                heartbeat_counter = 0

            cleanup_counter += 1
            if cleanup_counter >= 1800:
                self._cleanup_processed()
                cleanup_counter = 0

            self._stop.wait(self._poll_interval)

    def _scan_existing_events(self):
        """Mark already-present event files as known on startup so we don't re-send."""
        try:
            for p in self._processed_dir.glob("evt_*.json"):
                self._known_events.add(p.stem)
        except Exception:
            pass

    def _read_heartbeats(self):
        mt5_changed = False
        tv_changed = False

        try:
            for p in self._events_dir.glob("heartbeat_*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue

                if data.get("source") == "tradingview" or p.name.startswith("heartbeat_tv_"):
                    src = TradingViewSource(data)
                    key = p.stem.removeprefix("heartbeat_tv_")
                    with self._lock:
                        self._tv_sources[key] = src
                    tv_changed = True
                else:
                    chart_id = data.get("chart_id", p.stem)
                    status = TerminalStatus(data)
                    with self._lock:
                        self._terminals[chart_id] = status
                    mt5_changed = True
        except Exception as exc:
            log.warning("heartbeat scan error: %s", exc)

        if mt5_changed:
            self._gui_queue.put(("terminals_updated", None))
        if tv_changed:
            self._gui_queue.put(("tv_sources_updated", None))

    def _process_new_events(self):
        try:
            event_files = sorted(self._events_dir.glob("evt_*.json"))
        except Exception:
            return

        for json_path in event_files:
            if json_path.stem in self._known_events:
                continue

            try:
                event = json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Failed to read %s: %s", json_path.name, exc)
                continue

            caption = format_event(event)
            log_line = format_log_line(event)

            screenshot_name = event.get("screenshot", "")
            screenshot_path = self._events_dir / screenshot_name if screenshot_name else None

            sent = False
            if screenshot_path and screenshot_path.exists():
                sent = self._sender.send_photo(screenshot_path, caption)
            else:
                sent = self._sender.send_message(caption)

            if not sent and self._sender.configured:
                log.warning("Failed to send event %s, will retry next cycle", json_path.name)
                continue

            self._known_events.add(json_path.stem)
            self._move_to_processed(json_path, screenshot_path)
            self._gui_queue.put(("new_event", {"event": event, "log_line": log_line}))
            self._gui_queue.put(("sent_count", self._sender.sent_count))

    def _move_to_processed(self, json_path: Path, screenshot_path: Path | None):
        try:
            shutil.move(str(json_path), str(self._processed_dir / json_path.name))
            if screenshot_path and screenshot_path.exists():
                shutil.move(str(screenshot_path), str(self._processed_dir / screenshot_path.name))
        except OSError as exc:
            log.warning("Failed to move processed files: %s", exc)

    def _cleanup_processed(self):
        if self._retention_days <= 0:
            return
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        try:
            for p in self._processed_dir.iterdir():
                try:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime)
                    if mtime < cutoff:
                        p.unlink()
                except OSError:
                    continue
        except Exception:
            pass

    def get_chart_status(self, chart_id: str) -> str:
        """Return 'ok', 'warn', or 'dead' based on heartbeat freshness."""
        with self._lock:
            ts = self._terminals.get(chart_id)
        if ts is None:
            return "dead"
        age = (datetime.now() - ts.last_seen).total_seconds()
        if age < self._heartbeat_timeout:
            return "ok"
        if age < self._heartbeat_dead:
            return "warn"
        return "dead"

    def get_tv_source_status(self, key: str) -> str:
        """Return 'ok', 'warn', or 'dead' for a TradingView source."""
        with self._lock:
            src = self._tv_sources.get(key)
        if src is None:
            return "dead"
        age = (datetime.now() - src.last_seen).total_seconds()
        if age < self._heartbeat_timeout:
            return "ok"
        if age < self._heartbeat_dead:
            return "warn"
        return "dead"
