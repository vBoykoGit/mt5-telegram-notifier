"""Capture TradingView chart screenshots via Playwright and the TV widget."""

import logging
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_TV_INTERVAL_MAP = {
    "1": "1", "3": "3", "5": "5", "15": "15", "30": "30",
    "45": "45", "60": "60", "120": "120", "180": "180",
    "240": "240", "D": "D", "W": "W", "M": "M",
    "1D": "D", "1W": "W", "1M": "M",
}

_WIDGET_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  html, body {{ margin:0; padding:0; background:#131722; overflow:hidden; }}
  #chart {{ width:1280px; height:720px; }}
</style>
</head><body>
<div id="chart"></div>
<script src="https://s3.tradingview.com/tv.js"></script>
<script>
new TradingView.widget({{
  "autosize": false,
  "width": 1280,
  "height": 720,
  "symbol": "{exchange_symbol}",
  "interval": "{interval}",
  "timezone": "Etc/UTC",
  "theme": "dark",
  "style": "1",
  "locale": "en",
  "hide_top_toolbar": false,
  "hide_legend": false,
  "allow_symbol_change": false,
  "save_image": false,
  "studies": ["AO@tv-basicstudies"],
  "container_id": "chart",
  "loading_screen": {{"backgroundColor": "#131722"}}
}});
</script>
</body></html>"""


class ChartRenderer:
    """Renders TradingView chart screenshots using a headless browser."""

    def __init__(self, wait_sec: float = 5.0):
        self._wait_sec = wait_sec
        self._lock = threading.Lock()
        self._browser = None
        self._playwright = None
        self._available = False
        self._init_attempted = False

    def _ensure_browser(self):
        if self._init_attempted:
            return
        self._init_attempted = True
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._available = True
            log.info("Playwright browser launched for chart screenshots")
        except Exception as exc:
            log.warning(
                "Playwright not available, screenshots disabled: %s. "
                "Run: pip install playwright && playwright install chromium",
                exc,
            )
            self._available = False

    @property
    def available(self) -> bool:
        if not self._init_attempted:
            with self._lock:
                self._ensure_browser()
        return self._available

    def capture(self, symbol: str, exchange: str, timeframe: str, output_path: Path) -> bool:
        with self._lock:
            self._ensure_browser()
            if not self._available:
                return False

            interval = _TV_INTERVAL_MAP.get(timeframe, timeframe)
            exchange_symbol = f"{exchange}:{symbol}" if exchange else symbol

            html = _WIDGET_HTML.format(
                exchange_symbol=exchange_symbol,
                interval=interval,
            )

            tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
            tmp.write(html)
            tmp.close()
            tmp_path = Path(tmp.name)

            try:
                page = self._browser.new_page(viewport={"width": 1280, "height": 720})
                page.goto(f"file:///{tmp_path.as_posix()}")
                page.wait_for_timeout(int(self._wait_sec * 1000))
                page.screenshot(path=str(output_path), full_page=False)
                page.close()
                log.info("Screenshot captured: %s (%s %s)", output_path.name, exchange_symbol, interval)
                return True
            except Exception as exc:
                log.error("Screenshot capture failed: %s", exc)
                return False
            finally:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def shutdown(self):
        with self._lock:
            if self._browser:
                try:
                    self._browser.close()
                except Exception:
                    pass
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
            self._available = False
