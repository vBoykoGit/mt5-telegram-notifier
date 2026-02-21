"""Format MT5 trade events into Telegram HTML messages."""

from datetime import datetime

EVENT_TITLES = {
    "position_opened": "Позиция открыта",
    "pending_placed": "Отложенный ордер",
    "sl_hit": "Сработал SL",
    "tp_hit": "Сработал TP",
    "stop_out": "Stop Out",
    "manual_close": "Ручное закрытие",
    "ea_close": "Закрытие советником",
    "other_close": "Закрытие позиции",
}


def _header(event: dict) -> str:
    title = EVENT_TITLES.get(event.get("event", ""), event.get("event", ""))
    terminal = event.get("terminal_name", "?")
    symbol = event.get("symbol", "")
    tf = event.get("timeframe", "")
    return f"<b>{title}</b>  [{terminal} &gt; {symbol} {tf}]"


def _format_price(value: float, symbol: str = "") -> str:
    if value == 0:
        return "—"
    digits = 5 if "JPY" not in symbol.upper() else 3
    return f"{value:.{digits}f}"


def format_position_opened(event: dict) -> str:
    direction = event.get("direction", "")
    volume = event.get("volume", 0)
    price = event.get("price", 0)
    sl = event.get("sl", 0)
    tp = event.get("tp", 0)
    sym = event.get("symbol", "")

    lines = [
        _header(event),
        f"{direction} {volume:.2f} лот @ {_format_price(price, sym)}",
    ]
    parts = []
    if sl:
        parts.append(f"SL: {_format_price(sl, sym)}")
    if tp:
        parts.append(f"TP: {_format_price(tp, sym)}")
    if parts:
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_pending_placed(event: dict) -> str:
    order_type = event.get("order_type", "")
    volume = event.get("volume", 0)
    entry = event.get("entry_price", 0)
    sl = event.get("sl", 0)
    tp = event.get("tp", 0)
    sym = event.get("symbol", "")

    lines = [
        _header(event),
        f"{order_type} {volume:.2f} лот @ {_format_price(entry, sym)}",
    ]
    parts = []
    if sl:
        parts.append(f"SL: {_format_price(sl, sym)}")
    if tp:
        parts.append(f"TP: {_format_price(tp, sym)}")
    if parts:
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_close(event: dict) -> str:
    direction = event.get("direction", "")
    volume = event.get("volume", 0)
    price = event.get("price", 0)
    total = event.get("total_profit", 0)
    sym = event.get("symbol", "")

    sign = "+" if total >= 0 else ""
    emoji = "" if total >= 0 else ""

    lines = [
        _header(event),
        f"{direction} {volume:.2f} лот закрыт @ {_format_price(price, sym)}",
        f"{emoji} {sign}{total:.2f} USD",
    ]
    return "\n".join(lines)


def format_event(event: dict) -> str:
    """Return formatted HTML string for any event type."""
    evt = event.get("event", "")
    if evt == "position_opened":
        return format_position_opened(event)
    if evt == "pending_placed":
        return format_pending_placed(event)
    return format_close(event)


def format_log_line(event: dict) -> str:
    """Short one-line summary for the GUI event log."""
    evt = event.get("event", "")
    terminal = event.get("terminal_name", "?")
    symbol = event.get("symbol", "")
    tf = event.get("timeframe", "")
    time_str = event.get("time", "")
    try:
        t = datetime.strptime(time_str, "%Y.%m.%d %H:%M:%S")
        time_short = t.strftime("%H:%M")
    except (ValueError, TypeError):
        time_short = time_str[:5] if time_str else "??:??"

    tag = f"[{terminal} > {symbol} {tf}]"

    if evt == "position_opened":
        direction = event.get("direction", "")
        volume = event.get("volume", 0)
        price = event.get("price", 0)
        return f"{time_short}  {tag}  Открыта {direction} {volume:.2f} @ {price}"
    if evt == "pending_placed":
        otype = event.get("order_type", "")
        volume = event.get("volume", 0)
        entry = event.get("entry_price", 0)
        return f"{time_short}  {tag}  Ордер {otype} {volume:.2f} @ {entry}"

    title = EVENT_TITLES.get(evt, evt)
    total = event.get("total_profit", 0)
    sign = "+" if total >= 0 else ""
    return f"{time_short}  {tag}  {title} {sign}{total:.2f} USD"
