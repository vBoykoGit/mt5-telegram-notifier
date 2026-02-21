"""MT5 Trade Notifier -- GUI application with system tray support."""

import json
import logging
import shutil
import threading
from collections import defaultdict
from pathlib import Path
from queue import Empty, Queue

import customtkinter as ctk

from .telegram_sender import TelegramSender
from .watcher import EventWatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# Config in repo root (parent of notifier package)
_REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = _REPO_ROOT / "config.json"
MAX_LOG_LINES = 200

STATUS_COLORS = {"ok": "#22c55e", "warn": "#eab308", "dead": "#ef4444"}
STATUS_LABELS = {"ok": "Активен", "warn": "Нет ответа", "dead": "Потерян"}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        example = _REPO_ROOT / "config.example.json"
        if example.exists():
            shutil.copy(str(example), str(CONFIG_PATH))
            log.info("Created config.json from config.example.json -- please fill in token and chat_id")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("Failed to load config: %s", exc)
        return {}


class TerminalPanel(ctk.CTkScrollableFrame):
    """Grouped list of terminals and their charts."""

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


class EventLogPanel(ctk.CTkScrollableFrame):
    """Scrollable event log with color-coded entries."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._lines: list[ctk.CTkLabel] = []

    def add_line(self, text: str, event: dict | None = None):
        color = ("gray20", "gray80")
        if event:
            total = event.get("total_profit", None)
            evt_type = event.get("event", "")
            if evt_type == "sl_hit" or (total is not None and total < 0):
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
        self.geometry("780x620")
        self.minsize(600, 450)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._config = load_config()
        self._gui_queue: Queue = Queue()
        self._tray_icon = None
        self._tray_thread = None

        token = self._config.get("telegram_bot_token", "")
        chat_id = self._config.get("telegram_chat_id", "")
        self._sender = TelegramSender(token, chat_id)

        common_path = Path(self._config.get(
            "common_files_path",
            Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files",
        ))
        events_dir = common_path / "tg_events"

        self._watcher = EventWatcher(
            events_dir=events_dir,
            sender=self._sender,
            gui_queue=self._gui_queue,
            poll_interval=self._config.get("poll_interval_sec", 2),
            heartbeat_timeout=self._config.get("heartbeat_timeout_sec", 60),
            heartbeat_dead=self._config.get("heartbeat_dead_sec", 120),
            retention_days=self._config.get("processed_retention_days", 7),
        )

        self._build_ui()
        self._watcher.start()
        self._poll_gui_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.grid_rowconfigure(3, weight=0)

        terminals_label = ctk.CTkLabel(
            self, text="Терминалы",
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
        )
        terminals_label.grid(row=0, column=0, sticky="nw", padx=12, pady=(8, 0))

        self._terminal_panel = TerminalPanel(self, height=200)
        self._terminal_panel.grid(row=0, column=0, sticky="nsew", padx=8, pady=(30, 4))

        log_label = ctk.CTkLabel(
            self, text="Лог событий",
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
        )
        log_label.grid(row=1, column=0, sticky="nw", padx=12, pady=(4, 0))

        self._event_log = EventLogPanel(self, height=180)
        self._event_log.grid(row=1, column=0, sticky="nsew", padx=8, pady=(30, 4))

        tg_frame = ctk.CTkFrame(self, height=36)
        tg_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
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

        events_dir = self._watcher._events_dir
        status_frame = ctk.CTkFrame(self, height=24, fg_color=("gray90", "gray17"))
        status_frame.grid(row=3, column=0, sticky="ew", padx=0, pady=0)

        self._path_label = ctk.CTkLabel(
            status_frame, text=f"Папка: {events_dir}",
            font=ctk.CTkFont(size=11), text_color=("gray50", "gray50"),
            anchor="w",
        )
        self._path_label.pack(side="left", padx=8, pady=2)

        self._queue_label = ctk.CTkLabel(
            status_frame, text="Очередь: 0",
            font=ctk.CTkFont(size=11), text_color=("gray50", "gray50"),
        )
        self._queue_label.pack(side="right", padx=8, pady=2)

        self.after(500, self._check_telegram_status)

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
