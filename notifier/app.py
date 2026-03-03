"""MT5 Trade Notifier -- GUI application with system tray support."""

import json
import logging
import shutil
import threading
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from queue import Empty, Queue

import customtkinter as ctk

from .chart_renderer import ChartRenderer
from .firewall import ensure_firewall_rule
from .mt5_detector import TerminalInfo, discover_terminals, install_ea
from .telegram_sender import TelegramSender
from .watcher import EventWatcher
from .webhook import WebhookServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = _REPO_ROOT / "config.json"
MAX_LOG_LINES = 200

STATUS_COLORS = {"ok": "#22c55e", "warn": "#eab308", "dead": "#ef4444"}
STATUS_LABELS = {"ok": "Активен", "warn": "Нет ответа", "dead": "Потерян"}

TV_SIGNAL_SHORT = {
    "saucer_buy": "Saucer Buy",
    "saucer_sell": "Saucer Sell",
    "wma_cross_up": "WMA Cross Up",
    "wma_cross_down": "WMA Cross Down",
    "higher_peak": "Higher Peak",
    "lower_peak": "Lower Peak",
    "single_bar_up": "Single Bar Up",
    "single_bar_down": "Single Bar Down",
}


def load_config() -> dict:
    example = _REPO_ROOT / "config.example.json"
    if not CONFIG_PATH.exists() and example.exists():
        shutil.copy(str(example), str(CONFIG_PATH))
        log.info("Created config.json from config.example.json")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("Failed to load config: %s", exc)
        # If config is empty/corrupted, recreate it from example and continue.
        try:
            if example.exists():
                shutil.copy(str(example), str(CONFIG_PATH))
                log.info("Recreated config.json from config.example.json")
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as restore_exc:
            log.error("Failed to restore config from example: %s", restore_exc)
        return {}


class TerminalPanel(ctk.CTkScrollableFrame):
    """Grouped list of MT5 terminals and their charts."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._widgets: dict[str, dict] = {}
        self._no_data_label = ctk.CTkLabel(
            self, text="Ожидание данных от терминалов...",
            text_color="gray", font=ctk.CTkFont(size=13),
        )
        self._no_data_label.pack(pady=20)

    def update_terminals(self, terminals: dict, get_status_fn):
        for w in self._widgets.values():
            for widget in w.get("all_widgets", []):
                widget.destroy()
        self._widgets.clear()

        if not terminals:
            if not self._no_data_label.winfo_ismapped():
                self._no_data_label.pack(pady=20)
            return

        if self._no_data_label.winfo_ismapped():
            self._no_data_label.pack_forget()

        groups: dict[str, list] = defaultdict(list)
        for chart_id, ts in terminals.items():
            groups[ts.terminal_id].append(ts)

        for tid, charts in groups.items():
            first = charts[0]
            all_widgets = []

            worst_status = "ok"
            for c in charts:
                s = get_status_fn(c.chart_id)
                if s == "dead":
                    worst_status = "dead"
                elif s == "warn" and worst_status != "dead":
                    worst_status = "warn"

            group_frame = ctk.CTkFrame(self, fg_color="transparent")
            group_frame.pack(fill="x", padx=4, pady=(8, 2))
            all_widgets.append(group_frame)

            header_frame = ctk.CTkFrame(group_frame, fg_color=("gray90", "gray17"), corner_radius=6)
            header_frame.pack(fill="x")
            all_widgets.append(header_frame)

            status_dot = ctk.CTkLabel(
                header_frame, text="\u25cf", width=20,
                text_color=STATUS_COLORS[worst_status],
                font=ctk.CTkFont(size=16),
            )
            status_dot.pack(side="left", padx=(8, 4))

            name_label = ctk.CTkLabel(
                header_frame,
                text=f"{first.terminal_name}  ({first.account_login} @ {first.account_server})",
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="w",
            )
            name_label.pack(side="left", padx=4, fill="x", expand=True)

            balance_label = ctk.CTkLabel(
                header_frame,
                text=f"Баланс: {first.balance:,.2f}  Эквити: {first.equity:,.2f}",
                font=ctk.CTkFont(size=12),
                text_color=("gray40", "gray60"),
            )
            balance_label.pack(side="right", padx=8)

            for chart in sorted(charts, key=lambda c: c.symbol + c.timeframe):
                cs = get_status_fn(chart.chart_id)
                row = ctk.CTkFrame(group_frame, fg_color="transparent")
                row.pack(fill="x", padx=(24, 4), pady=1)
                all_widgets.append(row)

                dot = ctk.CTkLabel(
                    row, text="\u25cf", width=16,
                    text_color=STATUS_COLORS[cs],
                    font=ctk.CTkFont(size=12),
                )
                dot.pack(side="left", padx=(0, 4))

                chart_label = ctk.CTkLabel(
                    row,
                    text=f"{chart.symbol}  {chart.timeframe}",
                    font=ctk.CTkFont(size=13, weight="bold"),
                    anchor="w", width=120,
                )
                chart_label.pack(side="left")

                pos_text = f"{chart.open_positions} поз."
                pos_label = ctk.CTkLabel(
                    row, text=pos_text,
                    font=ctk.CTkFont(size=12),
                    text_color=("gray40", "gray60"),
                    anchor="w", width=60,
                )
                pos_label.pack(side="left", padx=(8, 0))

                status_label = ctk.CTkLabel(
                    row, text=STATUS_LABELS[cs],
                    font=ctk.CTkFont(size=11),
                    text_color=STATUS_COLORS[cs],
                )
                status_label.pack(side="right", padx=8)

            self._widgets[tid] = {"all_widgets": all_widgets}


class TradingViewPanel(ctk.CTkScrollableFrame):
    """Shows active TradingView indicator sources."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._widgets: list = []
        self._no_data_label = ctk.CTkLabel(
            self, text="Нет данных от TradingView.\nНастройте webhook и создайте алерт.",
            text_color="gray", font=ctk.CTkFont(size=13), justify="center",
        )
        self._no_data_label.pack(pady=20)

    def update_sources(self, sources: dict, get_status_fn):
        for w in self._widgets:
            w.destroy()
        self._widgets.clear()

        if not sources:
            if not self._no_data_label.winfo_ismapped():
                self._no_data_label.pack(pady=20)
            return

        if self._no_data_label.winfo_ismapped():
            self._no_data_label.pack_forget()

        groups: dict[str, list] = defaultdict(list)
        for key, src in sources.items():
            groups[src.symbol].append((key, src))

        for symbol, entries in sorted(groups.items()):
            sym_frame = ctk.CTkFrame(self, fg_color="transparent")
            sym_frame.pack(fill="x", padx=4, pady=(6, 2))
            self._widgets.append(sym_frame)

            sym_header = ctk.CTkFrame(sym_frame, fg_color=("gray90", "gray17"), corner_radius=6)
            sym_header.pack(fill="x")
            self._widgets.append(sym_header)

            first_src = entries[0][1]
            tag = f"{first_src.exchange}:{symbol}" if first_src.exchange else symbol
            ctk.CTkLabel(
                sym_header, text=tag,
                font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
            ).pack(side="left", padx=8, pady=4)

            ctk.CTkLabel(
                sym_header, text=first_src.indicator,
                font=ctk.CTkFont(size=12), text_color=("gray40", "gray60"),
            ).pack(side="right", padx=8, pady=4)

            for key, src in sorted(entries, key=lambda e: e[1].timeframe):
                status = get_status_fn(key)
                row = ctk.CTkFrame(sym_frame, fg_color="transparent")
                row.pack(fill="x", padx=(24, 4), pady=1)
                self._widgets.append(row)

                dot = ctk.CTkLabel(
                    row, text="\u25cf", width=16,
                    text_color=STATUS_COLORS[status],
                    font=ctk.CTkFont(size=12),
                )
                dot.pack(side="left", padx=(0, 4))

                ctk.CTkLabel(
                    row, text=src.timeframe,
                    font=ctk.CTkFont(size=13, weight="bold"),
                    anchor="w", width=60,
                ).pack(side="left")

                signal_text = TV_SIGNAL_SHORT.get(src.last_signal, src.last_signal)
                ctk.CTkLabel(
                    row, text=signal_text,
                    font=ctk.CTkFont(size=12),
                    text_color="#60a5fa",
                    anchor="w", width=140,
                ).pack(side="left", padx=(8, 0))

                ctk.CTkLabel(
                    row, text=src.last_signal_time,
                    font=ctk.CTkFont(size=11),
                    text_color=("gray40", "gray60"),
                ).pack(side="right", padx=8)


class DetectedTerminalsPanel(ctk.CTkFrame):
    """Shows running MT5 terminals discovered by psutil, with install buttons."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._rows: list[ctk.CTkFrame] = []
        self._no_data_label = ctk.CTkLabel(
            self,
            text="Нажмите «Поиск терминалов» для обнаружения запущенных MT5.",
            text_color="gray", font=ctk.CTkFont(size=12),
        )
        self._no_data_label.pack(pady=6)
        self._install_callback = None

    def set_install_callback(self, cb):
        self._install_callback = cb

    def show_terminals(self, terminals: list[TerminalInfo]):
        for r in self._rows:
            r.destroy()
        self._rows.clear()

        if not terminals:
            if not self._no_data_label.winfo_ismapped():
                self._no_data_label.configure(
                    text="Запущенные терминалы MT5 не обнаружены.",
                )
                self._no_data_label.pack(pady=6)
            else:
                self._no_data_label.configure(
                    text="Запущенные терминалы MT5 не обнаружены.",
                )
            return

        if self._no_data_label.winfo_ismapped():
            self._no_data_label.pack_forget()

        for t in terminals:
            row = ctk.CTkFrame(self, fg_color=("gray90", "gray17"), corner_radius=6)
            row.pack(fill="x", padx=4, pady=2)
            self._rows.append(row)

            ctk.CTkLabel(
                row, text=t.name,
                font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
            ).pack(side="left", padx=8, pady=4)

            both = t.ea_installed and t.indicator_installed
            if both:
                ea_text = "EA установлен"
                ea_color = "#22c55e"
            elif t.ea_installed:
                ea_text = "Индикатор не установлен"
                ea_color = "#eab308"
            elif t.indicator_installed:
                ea_text = "EA не установлен"
                ea_color = "#eab308"
            else:
                ea_text = "EA не установлен"
                ea_color = "#ef4444"

            status_lbl = ctk.CTkLabel(
                row, text=ea_text,
                font=ctk.CTkFont(size=11), text_color=ea_color,
            )
            status_lbl.pack(side="left", padx=(12, 0), pady=4)

            if not both:
                install_btn = ctk.CTkButton(
                    row, text="Установить EA", width=110, height=26,
                    command=lambda ti=t, sl=status_lbl, btn_ref=[None]: self._on_install(ti, sl, btn_ref),
                )
                install_btn.pack(side="right", padx=8, pady=4)
            else:
                ctk.CTkLabel(
                    row, text="\u2713",
                    font=ctk.CTkFont(size=14), text_color="#22c55e",
                ).pack(side="right", padx=12, pady=4)

    def _on_install(self, terminal: TerminalInfo, status_lbl: ctk.CTkLabel, btn_ref: list):
        if self._install_callback:
            self._install_callback(terminal, status_lbl)


class EventLogPanel(ctk.CTkScrollableFrame):
    """Scrollable event log with color-coded entries."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._lines: list[ctk.CTkLabel] = []

    def add_line(self, text: str, event: dict | None = None):
        color = ("gray20", "gray80")
        if event:
            source = event.get("source", "")
            total = event.get("total_profit", None)
            evt_type = event.get("event", "")

            if source == "tradingview":
                sig = event.get("signal", "")
                if "sell" in sig or "down" in sig or "lower" in sig:
                    color = "#ef4444"
                else:
                    color = ("#2563eb", "#60a5fa")
            elif evt_type == "sl_hit" or (total is not None and total < 0):
                color = "#ef4444"
            elif evt_type == "tp_hit" or (total is not None and total > 0):
                color = "#22c55e"
            elif evt_type in ("position_opened", "pending_placed"):
                color = ("#2563eb", "#60a5fa")

        label = ctk.CTkLabel(
            self, text=text, anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=color,
        )
        label.pack(fill="x", padx=4, pady=1, anchor="nw")
        self._lines.append(label)

        while len(self._lines) > MAX_LOG_LINES:
            old = self._lines.pop(0)
            old.destroy()

        self._parent_canvas.yview_moveto(1.0)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("MT5 Trade Notifier")
        self.geometry("820x700")
        self.minsize(650, 500)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._config = load_config()
        self._gui_queue: Queue = Queue()
        self._tray_icon = None
        self._tray_thread = None
        self._settings_debounce_after_id: str | None = None

        token = self._config.get("telegram_bot_token", "")
        chat_id = self._config.get("telegram_chat_id", "")
        self._sender = TelegramSender(token, chat_id)

        common_path = Path(self._config.get(
            "common_files_path",
            Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files",
        ))
        self._events_dir = common_path / "tg_events"

        self._watcher = EventWatcher(
            events_dir=self._events_dir,
            sender=self._sender,
            gui_queue=self._gui_queue,
            poll_interval=self._config.get("poll_interval_sec", 2),
            heartbeat_timeout=self._config.get("heartbeat_timeout_sec", 60),
            heartbeat_dead=self._config.get("heartbeat_dead_sec", 120),
            retention_days=self._config.get("processed_retention_days", 7),
        )

        wait_sec = self._config.get("chart_screenshot_wait_sec", 5)
        self._chart_renderer = ChartRenderer(wait_sec=wait_sec)

        webhook_port = self._config.get("webhook_port", 8080)
        webhook_secret = self._config.get("webhook_secret", "")
        self._webhook = WebhookServer(
            events_dir=self._events_dir,
            port=webhook_port,
            secret=webhook_secret,
            chart_renderer=self._chart_renderer,
        )

        self._build_ui()
        self._watcher.start()

        if self._config.get("webhook_enabled", True):
            self._webhook.start()
        self._update_webhook_status()

        self._poll_gui_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=3)
        self.grid_rowconfigure(2, weight=2)
        self.grid_rowconfigure(3, weight=0)
        self.grid_rowconfigure(4, weight=0)

        self._build_settings_frame()
        self._build_sources_tabs()
        self._build_event_log()
        self._build_status_bars()

        self.after(500, self._check_telegram_status)

    def _build_settings_frame(self):
        settings_frame = ctk.CTkFrame(self)
        settings_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        settings_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            settings_frame, text="Токен бота:",
            font=ctk.CTkFont(size=12), anchor="e", width=100,
        ).grid(row=0, column=0, padx=(8, 4), pady=(6, 2), sticky="e")

        self._token_entry = ctk.CTkEntry(
            settings_frame, show="*",
            placeholder_text="Вставьте токен Telegram-бота",
        )
        self._token_entry.grid(row=0, column=1, padx=4, pady=(6, 2), sticky="ew")

        self._show_token_btn = ctk.CTkButton(
            settings_frame, text="Показать", width=80, height=28,
            command=self._toggle_token_visibility,
        )
        self._show_token_btn.grid(row=0, column=2, padx=(4, 8), pady=(6, 2))

        ctk.CTkLabel(
            settings_frame, text="Chat ID:",
            font=ctk.CTkFont(size=12), anchor="e", width=100,
        ).grid(row=1, column=0, padx=(8, 4), pady=(2, 2), sticky="e")

        self._chatid_entry = ctk.CTkEntry(
            settings_frame,
            placeholder_text="Числовой ID чата или группы",
        )
        self._chatid_entry.grid(row=1, column=1, padx=4, pady=(2, 2), sticky="ew")

        self._save_btn = ctk.CTkButton(
            settings_frame, text="Сохранить", width=80, height=28,
            command=self._save_settings,
        )
        self._save_btn.grid(row=1, column=2, padx=(4, 8), pady=(2, 2))

        ctk.CTkLabel(
            settings_frame, text="Webhook включен:",
            font=ctk.CTkFont(size=12), anchor="e", width=100,
        ).grid(row=2, column=0, padx=(8, 4), pady=(2, 2), sticky="e")

        self._webhook_enabled_var = ctk.BooleanVar(
            value=bool(self._config.get("webhook_enabled", True))
        )
        self._webhook_enabled_switch = ctk.CTkSwitch(
            settings_frame, text="",
            variable=self._webhook_enabled_var,
            onvalue=True,
            offvalue=False,
            command=self._on_webhook_toggle_changed,
        )
        self._webhook_enabled_switch.grid(row=2, column=1, padx=4, pady=(2, 2), sticky="w")

        ctk.CTkLabel(
            settings_frame, text="Webhook порт:",
            font=ctk.CTkFont(size=12), anchor="e", width=100,
        ).grid(row=3, column=0, padx=(8, 4), pady=(2, 2), sticky="e")

        wh_inner = ctk.CTkFrame(settings_frame, fg_color="transparent")
        wh_inner.grid(row=3, column=1, padx=4, pady=(2, 2), sticky="ew")

        self._port_entry = ctk.CTkEntry(wh_inner, width=80, placeholder_text="8080")
        self._port_entry.pack(side="left")

        self._webhook_status_label = ctk.CTkLabel(
            wh_inner, text="",
            font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"),
        )
        self._webhook_status_label.pack(side="left", padx=(12, 0))

        self._test_webhook_btn = ctk.CTkButton(
            settings_frame,
            text="Проверить webhook",
            width=120,
            height=28,
            command=self._test_webhook_local,
        )
        self._test_webhook_btn.grid(row=3, column=2, padx=(4, 8), pady=(2, 2))

        ctk.CTkLabel(
            settings_frame, text="Webhook секрет:",
            font=ctk.CTkFont(size=12), anchor="e", width=100,
        ).grid(row=4, column=0, padx=(8, 4), pady=(2, 2), sticky="e")

        self._secret_entry = ctk.CTkEntry(
            settings_frame, show="*",
            placeholder_text="Опционально",
        )
        self._secret_entry.grid(row=4, column=1, padx=4, pady=(2, 2), sticky="ew")

        ctk.CTkLabel(
            settings_frame, text="Публичный URL:",
            font=ctk.CTkFont(size=12), anchor="e", width=100,
        ).grid(row=5, column=0, padx=(8, 4), pady=(2, 2), sticky="e")

        url_inner = ctk.CTkFrame(settings_frame, fg_color="transparent")
        url_inner.grid(row=5, column=1, columnspan=2, padx=4, pady=(2, 2), sticky="ew")
        url_inner.grid_columnconfigure(0, weight=1)

        self._public_url_entry = ctk.CTkEntry(
            url_inner,
            placeholder_text="https://xxxx.ngrok-free.app  (ngrok http <порт>)",
        )
        self._public_url_entry.grid(row=0, column=0, sticky="ew")

        self._webhook_url_label = ctk.CTkLabel(
            settings_frame, text="",
            font=ctk.CTkFont(size=11), text_color="#60a5fa",
            anchor="w",
        )
        self._webhook_url_label.grid(row=6, column=1, columnspan=2, padx=4, pady=(0, 2), sticky="w")

        ctk.CTkLabel(
            settings_frame, text="Скриншот, сек:",
            font=ctk.CTkFont(size=12), anchor="e", width=100,
        ).grid(row=7, column=0, padx=(8, 4), pady=(2, 2), sticky="e")

        self._screenshot_wait_entry = ctk.CTkEntry(
            settings_frame,
            placeholder_text="5.0",
        )
        self._screenshot_wait_entry.grid(row=7, column=1, padx=4, pady=(2, 2), sticky="ew")

        ctk.CTkLabel(
            settings_frame, text="Путь к EA:",
            font=ctk.CTkFont(size=12), anchor="e", width=100,
        ).grid(row=8, column=0, padx=(8, 4), pady=(2, 2), sticky="e")

        self._ea_source_entry = ctk.CTkEntry(
            settings_frame,
            placeholder_text=r"E:\Git\trading-bot-mt5",
        )
        self._ea_source_entry.grid(row=8, column=1, padx=4, pady=(2, 2), sticky="ew")

        self._ea_browse_btn = ctk.CTkButton(
            settings_frame, text="Обзор...", width=80, height=28,
            command=self._browse_ea_source,
        )
        self._ea_browse_btn.grid(row=8, column=2, padx=(4, 8), pady=(2, 2))

        ctk.CTkLabel(
            settings_frame, text="Брандмауэр:",
            font=ctk.CTkFont(size=12), anchor="e", width=100,
        ).grid(row=9, column=0, padx=(8, 4), pady=(2, 6), sticky="e")

        fw_inner = ctk.CTkFrame(settings_frame, fg_color="transparent")
        fw_inner.grid(row=9, column=1, padx=4, pady=(2, 6), sticky="ew")

        self._firewall_port_entry = ctk.CTkEntry(fw_inner, width=80, placeholder_text="8080")
        self._firewall_port_entry.pack(side="left")

        self._firewall_result_label = ctk.CTkLabel(
            fw_inner, text="",
            font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"),
        )
        self._firewall_result_label.pack(side="left", padx=(12, 0))

        self._firewall_btn = ctk.CTkButton(
            settings_frame, text="Открыть порт", width=120, height=28,
            command=self._on_firewall_btn_click,
        )
        self._firewall_btn.grid(row=9, column=2, padx=(4, 8), pady=(2, 6))

        token = self._config.get("telegram_bot_token", "")
        chat_id = self._config.get("telegram_chat_id", "")
        port = self._config.get("webhook_port", 8080)
        secret = self._config.get("webhook_secret", "")
        public_url = self._config.get("webhook_public_url", "")
        screenshot_wait = self._config.get("chart_screenshot_wait_sec", 5)

        if token:
            self._token_entry.insert(0, token)
        if chat_id:
            self._chatid_entry.insert(0, str(chat_id))
        self._port_entry.insert(0, str(port))
        if secret:
            self._secret_entry.insert(0, secret)
        if public_url:
            self._public_url_entry.insert(0, public_url)
        self._screenshot_wait_entry.insert(0, str(screenshot_wait))
        self._firewall_port_entry.insert(0, str(port))

        ea_source = self._config.get("ea_source_path", "")
        if ea_source:
            self._ea_source_entry.insert(0, ea_source)

        self._port_entry.bind("<KeyRelease>", self._on_webhook_field_changed)
        self._secret_entry.bind("<KeyRelease>", self._on_webhook_field_changed)
        self._public_url_entry.bind("<KeyRelease>", self._on_webhook_field_changed)
        self._screenshot_wait_entry.bind("<KeyRelease>", self._on_webhook_field_changed)

    def _build_sources_tabs(self):
        self._tabview = ctk.CTkTabview(self, height=200)
        self._tabview.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 4))

        self._tabview.add("MT5 Терминалы")
        self._tabview.add("TradingView")

        mt5_tab = self._tabview.tab("MT5 Терминалы")

        top_bar = ctk.CTkFrame(mt5_tab, fg_color="transparent")
        top_bar.pack(fill="x", padx=4, pady=(0, 4))

        self._scan_btn = ctk.CTkButton(
            top_bar, text="Поиск терминалов", width=140, height=28,
            command=self._on_scan_terminals,
        )
        self._scan_btn.pack(side="left")

        self._scan_status_label = ctk.CTkLabel(
            top_bar, text="",
            font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"),
        )
        self._scan_status_label.pack(side="left", padx=(12, 0))

        self._detected_panel = DetectedTerminalsPanel(mt5_tab)
        self._detected_panel.pack(fill="x", padx=0, pady=(0, 4))
        self._detected_panel.set_install_callback(self._on_install_ea)

        sep = ctk.CTkLabel(
            mt5_tab, text="Активные EA (heartbeat)",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w",
        )
        sep.pack(fill="x", padx=8, pady=(2, 0))

        self._terminal_panel = TerminalPanel(mt5_tab)
        self._terminal_panel.pack(fill="both", expand=True)

        self._tv_panel = TradingViewPanel(self._tabview.tab("TradingView"))
        self._tv_panel.pack(fill="both", expand=True)

    def _build_event_log(self):
        log_label = ctk.CTkLabel(
            self, text="Лог событий",
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
        )
        log_label.grid(row=2, column=0, sticky="nw", padx=12, pady=(4, 0))

        self._event_log = EventLogPanel(self, height=160)
        self._event_log.grid(row=2, column=0, sticky="nsew", padx=8, pady=(30, 4))

    def _build_status_bars(self):
        tg_frame = ctk.CTkFrame(self, height=36)
        tg_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=4)
        tg_frame.grid_columnconfigure(1, weight=1)

        self._tg_status_label = ctk.CTkLabel(
            tg_frame, text="Telegram: проверка...",
            font=ctk.CTkFont(size=12),
        )
        self._tg_status_label.grid(row=0, column=0, padx=8, pady=4)

        self._sent_label = ctk.CTkLabel(
            tg_frame, text="Отправлено: 0",
            font=ctk.CTkFont(size=12), text_color=("gray40", "gray60"),
        )
        self._sent_label.grid(row=0, column=1, padx=8, pady=4)

        test_btn = ctk.CTkButton(
            tg_frame, text="Тест", width=60, height=28,
            command=self._test_telegram,
        )
        test_btn.grid(row=0, column=2, padx=8, pady=4)

        status_frame = ctk.CTkFrame(self, height=24, fg_color=("gray90", "gray17"))
        status_frame.grid(row=4, column=0, sticky="ew", padx=0, pady=0)

        self._path_label = ctk.CTkLabel(
            status_frame, text=f"Папка: {self._events_dir}",
            font=ctk.CTkFont(size=11), text_color=("gray50", "gray50"),
            anchor="w",
        )
        self._path_label.pack(side="left", padx=8, pady=2)

        self._queue_label = ctk.CTkLabel(
            status_frame, text="Очередь: 0",
            font=ctk.CTkFont(size=11), text_color=("gray50", "gray50"),
        )
        self._queue_label.pack(side="right", padx=8, pady=2)

    def _update_webhook_status(self):
        enabled = bool(self._webhook_enabled_var.get())
        if not enabled:
            self._webhook_status_label.configure(
                text="Webhook выключен",
                text_color=("gray40", "gray60"),
            )
        elif self._webhook.running:
            self._webhook_status_label.configure(
                text=f"Webhook запущен на порту {self._webhook.port}",
                text_color="#22c55e",
            )
        else:
            self._webhook_status_label.configure(
                text="Webhook не запущен",
                text_color="#ef4444",
            )
        self._update_webhook_url_label()

    def _browse_ea_source(self) -> None:
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="Выберите папку с EA (trading-bot-mt5)")
        if folder:
            self._ea_source_entry.delete(0, "end")
            self._ea_source_entry.insert(0, folder)
            self._schedule_auto_save(delay_ms=300)

    def _on_scan_terminals(self) -> None:
        self._scan_btn.configure(state="disabled")
        self._scan_status_label.configure(text="Поиск...", text_color=("gray40", "gray60"))

        def run():
            terminals = discover_terminals()
            self.after(0, lambda: self._on_scan_done(terminals))

        threading.Thread(target=run, daemon=True).start()

    def _on_scan_done(self, terminals: list[TerminalInfo]) -> None:
        self._scan_btn.configure(state="normal")
        count = len(terminals)
        if count:
            self._scan_status_label.configure(
                text=f"Найдено: {count}", text_color="#22c55e",
            )
        else:
            self._scan_status_label.configure(
                text="Терминалы не найдены", text_color="#eab308",
            )
        self._detected_panel.show_terminals(terminals)

    def _on_install_ea(self, terminal: TerminalInfo, status_lbl) -> None:
        ea_source = self._ea_source_entry.get().strip()
        if not ea_source:
            status_lbl.configure(text="Укажите путь к EA в настройках", text_color="#ef4444")
            return
        status_lbl.configure(text="Копирование...", text_color=("gray40", "gray60"))

        def run():
            ok, msg = install_ea(terminal.data_dir, ea_source)
            self.after(0, lambda: self._on_install_done(ok, msg, status_lbl))

        threading.Thread(target=run, daemon=True).start()

    def _on_install_done(self, ok: bool, msg: str, status_lbl) -> None:
        if ok:
            status_lbl.configure(text="EA установлен", text_color="#22c55e")
        else:
            status_lbl.configure(text=msg[:60] or "Ошибка установки", text_color="#ef4444")

    def _on_firewall_btn_click(self) -> None:
        port_str = self._firewall_port_entry.get().strip()
        try:
            port = int(port_str)
        except ValueError:
            self._firewall_result_label.configure(
                text="Введите целое число", text_color="#ef4444",
            )
            return
        if port <= 0 or port > 65535:
            self._firewall_result_label.configure(
                text="Порт должен быть от 1 до 65535", text_color="#ef4444",
            )
            return

        self._firewall_btn.configure(state="disabled")
        self._firewall_result_label.configure(text="Открываю...", text_color=("gray40", "gray60"))

        def run():
            ok, msg = ensure_firewall_rule(port)
            self.after(0, lambda: self._on_firewall_done(ok, msg, port))

        threading.Thread(target=run, daemon=True).start()

    def _on_firewall_done(self, ok: bool, msg: str, port: int) -> None:
        self._firewall_btn.configure(state="normal")
        if ok:
            self._firewall_result_label.configure(
                text=f"Порт {port} открыт", text_color="#22c55e",
            )
        else:
            self._firewall_result_label.configure(
                text=msg or "Запустите от имени администратора", text_color="#ef4444",
            )

    def _update_webhook_url_label(self):
        enabled = bool(self._webhook_enabled_var.get())
        if not enabled:
            self._webhook_url_label.configure(
                text="Webhook отключен. Включите переключатель, чтобы принимать сигналы.",
            )
            return
        base = self._public_url_entry.get().strip().rstrip("/")
        if base:
            full_url = f"{base}/webhook/tradingview"
            self._webhook_url_label.configure(
                text=f"URL для TradingView: {full_url}",
            )
        else:
            self._webhook_url_label.configure(
                text="Укажите публичный URL (ngrok http <порт> или cloudflared tunnel)",
            )

    def _toggle_token_visibility(self):
        if self._token_entry.cget("show") == "*":
            self._token_entry.configure(show="")
            self._show_token_btn.configure(text="Скрыть")
        else:
            self._token_entry.configure(show="*")
            self._show_token_btn.configure(text="Показать")

    def _on_webhook_toggle_changed(self):
        self._update_webhook_status()
        self._schedule_auto_save(delay_ms=200)

    def _on_webhook_field_changed(self, event=None):
        self._update_webhook_url_label()
        self._schedule_auto_save(delay_ms=700)

    def _schedule_auto_save(self, delay_ms: int = 700):
        if self._settings_debounce_after_id:
            try:
                self.after_cancel(self._settings_debounce_after_id)
            except Exception:
                pass
        self._settings_debounce_after_id = self.after(
            delay_ms,
            self._run_auto_save,
        )

    def _run_auto_save(self):
        self._settings_debounce_after_id = None
        self._apply_and_persist_settings(show_feedback=False, strict_validation=True)

    def _parse_webhook_ui(self, strict_validation: bool) -> tuple | None:
        port_str = self._port_entry.get().strip()
        secret = self._secret_entry.get().strip()
        public_url = self._public_url_entry.get().strip()
        enabled = bool(self._webhook_enabled_var.get())
        wait_str = self._screenshot_wait_entry.get().strip()

        port = None
        if port_str:
            try:
                port = int(port_str)
            except ValueError:
                if strict_validation:
                    self._webhook_status_label.configure(
                        text="Webhook порт: введите целое число",
                        text_color="#ef4444",
                    )
                    return None
        if port is None:
            port = 8080

        wait_sec = None
        if wait_str:
            try:
                wait_sec = float(wait_str)
                if wait_sec <= 0:
                    raise ValueError("wait must be > 0")
            except ValueError:
                if strict_validation:
                    self._webhook_status_label.configure(
                        text="Скриншот, сек: введите число > 0",
                        text_color="#ef4444",
                    )
                    return None
        if wait_sec is None:
            wait_sec = 5.0

        return enabled, port, secret, public_url, wait_sec

    def _apply_and_persist_settings(self, show_feedback: bool, strict_validation: bool):
        parsed = self._parse_webhook_ui(strict_validation=strict_validation)
        if parsed is None:
            return
        enabled, port, secret, public_url, wait_sec = parsed

        token = self._token_entry.get().strip()
        chat_id = self._chatid_entry.get().strip()

        self._config["telegram_bot_token"] = token
        self._config["telegram_chat_id"] = chat_id
        self._config["webhook_enabled"] = enabled
        self._config["webhook_port"] = port
        self._config["webhook_secret"] = secret
        self._config["webhook_public_url"] = public_url
        self._config["chart_screenshot_wait_sec"] = wait_sec
        self._config["ea_source_path"] = self._ea_source_entry.get().strip()

        try:
            CONFIG_PATH.write_text(
                json.dumps(self._config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("Config saved to %s", CONFIG_PATH)
        except Exception as exc:
            log.error("Failed to save config: %s", exc)
            return

        self._sender.reconfigure(token, chat_id)
        self._check_telegram_status()

        old_wait_sec = float(getattr(self._chart_renderer, "_wait_sec", 5.0))
        wait_changed = abs(wait_sec - old_wait_sec) > 1e-9
        if wait_changed:
            self._chart_renderer.shutdown()
            self._chart_renderer = ChartRenderer(wait_sec=wait_sec)

        old_port = self._webhook.port
        if port != old_port or secret != self._webhook.webhook_secret or wait_changed:
            self._webhook.stop()
            self._webhook = WebhookServer(
                events_dir=self._events_dir,
                port=port,
                secret=secret,
                chart_renderer=self._chart_renderer,
            )

        if enabled:
            if not self._webhook.running:
                self._webhook.start()
        else:
            if self._webhook.running:
                self._webhook.stop()
        self._update_webhook_status()

        if show_feedback:
            self._save_btn.configure(text="Сохранено", state="disabled")
            self.after(2000, lambda: self._save_btn.configure(text="Сохранить", state="normal"))

    def _save_settings(self):
        self._apply_and_persist_settings(show_feedback=True, strict_validation=False)

    def _check_telegram_status(self):
        def _check():
            ok, msg = self._sender.test_connection()
            self._gui_queue.put(("tg_status", (ok, msg)))
        threading.Thread(target=_check, daemon=True).start()

    def _test_telegram(self):
        def _send():
            ok = self._sender.send_message(
                "<b>MT5 Trade Notifier</b>\nТестовое сообщение. Всё работает!"
            )
            status = "Тест отправлен" if ok else "Ошибка отправки"
            self._gui_queue.put(("tg_status", (ok, status)))
            if ok:
                self._gui_queue.put(("sent_count", self._sender.sent_count))
        threading.Thread(target=_send, daemon=True).start()

    def _test_webhook_local(self):
        enabled = bool(self._webhook_enabled_var.get())
        if not enabled:
            self._webhook_status_label.configure(
                text="Webhook выключен — включите переключатель",
                text_color="#ef4444",
            )
            return

        def _send():
            port_str = self._port_entry.get().strip()
            secret = self._secret_entry.get().strip()
            try:
                port = int(port_str) if port_str else 8080
            except ValueError:
                port = 8080

            payload = {
                "source": "tradingview",
                "signal": "single_bar_up",
                "symbol": "TEST",
                "timeframe": "1",
                "exchange": "TEST",
                "indicator": "Webhook Test",
                "price": 1.2345,
            }
            if secret:
                payload["secret"] = secret

            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/webhook/tradingview",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=6) as resp:
                    ok = 200 <= resp.status < 300
                if ok:
                    msg = f"Webhook OK (POST на localhost:{port})"
                    color = "#22c55e"
                else:
                    msg = f"Webhook ошибка HTTP {resp.status}"
                    color = "#ef4444"
            except urllib.error.HTTPError as exc:
                msg = f"Webhook ошибка HTTP {exc.code}"
                color = "#ef4444"
            except Exception as exc:
                msg = f"Webhook недоступен: {exc}"
                color = "#ef4444"

            self.after(0, lambda: self._webhook_status_label.configure(text=msg, text_color=color))

        threading.Thread(target=_send, daemon=True).start()

    def _poll_gui_queue(self):
        try:
            for _ in range(50):
                try:
                    msg_type, payload = self._gui_queue.get_nowait()
                except Empty:
                    break

                if msg_type == "terminals_updated":
                    terminals = self._watcher.terminals
                    self._terminal_panel.update_terminals(
                        terminals, self._watcher.get_chart_status
                    )
                elif msg_type == "tv_sources_updated":
                    tv_sources = self._watcher.tv_sources
                    self._tv_panel.update_sources(
                        tv_sources, self._watcher.get_tv_source_status
                    )
                elif msg_type == "new_event":
                    event = payload["event"]
                    log_line = payload["log_line"]
                    self._event_log.add_line(log_line, event)
                elif msg_type == "sent_count":
                    self._sent_label.configure(text=f"Отправлено: {payload}")
                elif msg_type == "tg_status":
                    ok, msg = payload
                    color = "#22c55e" if ok else "#ef4444"
                    self._tg_status_label.configure(
                        text=f"Telegram: {msg}", text_color=color,
                    )
        except Exception as exc:
            log.error("GUI queue error: %s", exc)

        self.after(200, self._poll_gui_queue)

    def _on_close(self):
        if self._try_minimize_to_tray():
            return
        self._shutdown()

    def _try_minimize_to_tray(self) -> bool:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            return False

        if self._tray_icon is not None:
            self.withdraw()
            return True

        self.withdraw()
        self._tray_icon = self._create_tray_icon(pystray, Image, ImageDraw)
        self._tray_thread = threading.Thread(
            target=self._tray_icon.run, daemon=True,
        )
        self._tray_thread.start()
        return True

    def _create_tray_icon(self, pystray, Image, ImageDraw):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([8, 8, 56, 56], fill="#22c55e")
        draw.text((20, 16), "M5", fill="white")

        menu = pystray.Menu(
            pystray.MenuItem("Показать окно", self._tray_show),
            pystray.MenuItem("Выход", self._tray_quit),
        )
        icon = pystray.Icon("mt5_notifier", img, "MT5 Trade Notifier", menu)
        icon.on_activate = self._tray_show
        return icon

    def _tray_show(self, *args):
        self.after(0, self._restore_window)

    def _restore_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _tray_quit(self, *args):
        if self._tray_icon:
            self._tray_icon.stop()
        self.after(0, self._shutdown)

    def _shutdown(self):
        self._watcher.stop()
        self._webhook.stop()
        self._chart_renderer.shutdown()
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
