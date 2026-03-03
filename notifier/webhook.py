"""Lightweight HTTP webhook server for receiving TradingView alerts."""

import json
import logging
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

log = logging.getLogger(__name__)

REQUIRED_FIELDS = ("source", "signal", "symbol", "timeframe")


class _WebhookHandler(BaseHTTPRequestHandler):
    """Handles POST /webhook/tradingview requests."""

    server: "WebhookServer"

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def do_POST(self):
        if self.path.rstrip("/") != "/webhook/tradingview":
            self._respond(404, {"error": "not found"})
            return

        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0 or content_len > 65536:
            self._respond(400, {"error": "invalid body"})
            return

        try:
            body = self.rfile.read(content_len)
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("Webhook: bad JSON: %s", exc)
            self._respond(400, {"error": "invalid json"})
            return

        expected_secret = self.server.webhook_secret
        if expected_secret and data.get("secret", "") != expected_secret:
            log.warning("Webhook: invalid secret from %s", self.client_address[0])
            self._respond(403, {"error": "forbidden"})
            return

        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            self._respond(400, {"error": f"missing fields: {missing}"})
            return

        try:
            self.server.process_signal(data)
        except Exception as exc:
            log.error("Webhook: processing error: %s", exc, exc_info=True)
            self._respond(500, {"error": "internal error"})
            return

        self._respond(200, {"ok": True})

    def do_GET(self):
        self._respond(200, {"status": "running", "server": "mt5-telegram-notifier"})

    def _respond(self, code: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class WebhookServer:
    """Runs an HTTP server in a background thread to receive TradingView webhooks."""

    def __init__(
        self,
        events_dir: Path,
        port: int = 8080,
        secret: str = "",
        chart_renderer=None,
    ):
        self._events_dir = events_dir
        self._port = port
        self.webhook_secret = secret
        self._chart_renderer = chart_renderer
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def port(self) -> int:
        return self._port

    def start(self):
        if self._running:
            return
        self._events_dir.mkdir(parents=True, exist_ok=True)
        handler_cls = type("BoundHandler", (_WebhookHandler,), {})
        try:
            self._httpd = HTTPServer(("0.0.0.0", self._port), handler_cls)
        except OSError as exc:
            log.error("Webhook server failed to bind port %d: %s", self._port, exc)
            return

        self._httpd.webhook_secret = self.webhook_secret
        self._httpd.process_signal = self._process_signal
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        log.info("Webhook server started on port %d", self._port)

    def stop(self):
        if not self._running:
            return
        self._running = False
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        log.info("Webhook server stopped")

    def _serve(self):
        try:
            self._httpd.serve_forever()
        except Exception:
            pass
        finally:
            self._running = False

    def _process_signal(self, data: dict):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        signal = data.get("signal", "unknown")
        symbol = data.get("symbol", "UNKNOWN")
        exchange = data.get("exchange", "")
        timeframe = data.get("timeframe", "")
        indicator = data.get("indicator", "AO Cross")

        basename = f"evt_tv_{ts}_{signal}_{symbol}"

        screenshot_name = ""
        if self._chart_renderer:
            try:
                png_name = f"{basename}.png"
                png_path = self._events_dir / png_name
                self._chart_renderer.capture(
                    symbol=symbol,
                    exchange=exchange,
                    timeframe=timeframe,
                    output_path=png_path,
                )
                screenshot_name = png_name
                log.info("Chart screenshot saved: %s", png_name)
            except Exception as exc:
                log.warning("Chart screenshot failed: %s", exc)

        event = {
            "source": "tradingview",
            "event": f"tv_{signal}",
            "indicator": indicator,
            "signal": signal,
            "symbol": symbol,
            "exchange": exchange,
            "timeframe": timeframe,
            "price": data.get("price", 0),
            "time": datetime.now().strftime("%Y.%m.%d %H:%M:%S"),
            "screenshot": screenshot_name,
        }

        json_path = self._events_dir / f"{basename}.json"
        json_path.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("TV event written: %s", json_path.name)

        self._write_heartbeat(data, indicator)

    def _write_heartbeat(self, data: dict, indicator: str):
        symbol = data.get("symbol", "UNKNOWN")
        timeframe = data.get("timeframe", "")
        exchange = data.get("exchange", "")
        hb_id = f"{indicator}_{symbol}_{timeframe}".replace(" ", "_")

        heartbeat = {
            "source": "tradingview",
            "indicator": indicator,
            "symbol": symbol,
            "exchange": exchange,
            "timeframe": timeframe,
            "last_signal": data.get("signal", ""),
            "last_signal_time": datetime.now().strftime("%Y.%m.%d %H:%M:%S"),
            "price": data.get("price", 0),
        }

        hb_path = self._events_dir / f"heartbeat_tv_{hb_id}.json"
        hb_path.write_text(json.dumps(heartbeat, ensure_ascii=False, indent=2), encoding="utf-8")
