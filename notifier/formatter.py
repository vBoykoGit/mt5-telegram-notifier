"""Format MT5 trade events and TradingView signals into Telegram HTML messages."""

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

TV_SIGNAL_TITLES = {
    "saucer_buy": "AO Saucer Buy",
    "saucer_sell": "AO Saucer Sell",
    "wma_cross_up": "AO WMA Cross Up",
    "wma_cross_down": "AO WMA Cross Down",
    "higher_peak": "AO Higher Peak",
    "lower_peak": "AO Lower Peak",
    "single_bar_up": "AO Single Bar Up",
    "single_bar_down": "AO Single Bar Down",
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


def format_tv_event(event: dict) -> str:
    """Format a TradingView signal into an HTML Telegram message."""
    signal = event.get("signal", "")
    title = TV_SIGNAL_TITLES.get(signal, signal)
    indicator = event.get("indicator", "TradingView")
    symbol = event.get("symbol", "")
    exchange = event.get("exchange", "")
    tf = event.get("timeframe", "")
    price = event.get("price", 0)

    tag = f"{exchange}:{symbol}" if exchange else symbol
    lines = [
        f"<b>{title}</b>  [TV &gt; {tag} {tf}]",
        f"Индикатор: {indicator}",
        f"Цена: {price}",
    ]
    return "\n".join(lines)


def format_tv_log_line(event: dict) -> str:
    """Short one-line summary for a TradingView signal."""
    signal = event.get("signal", "")
    title = TV_SIGNAL_TITLES.get(signal, signal)
    symbol = event.get("symbol", "")
    tf = event.get("timeframe", "")
    price = event.get("price", 0)
    time_str = event.get("time", "")
    try:
        t = datetime.strptime(time_str, "%Y.%m.%d %H:%M:%S")
        time_short = t.strftime("%H:%M")
    except (ValueError, TypeError):
        time_short = time_str[:5] if time_str else "??:??"

    return f"{time_short}  [TV > {symbol} {tf}]  {title}  @ {price}"


def format_event(event: dict) -> str:
    """Return formatted HTML string for any event type."""
    if event.get("source") == "tradingview":
        return format_tv_event(event)
    evt = event.get("event", "")
    if evt == "position_opened":
        return format_position_opened(event)
    if evt == "pending_placed":
        return format_pending_placed(event)
    return format_close(event)


def format_log_line(event: dict) -> str:
    """Short one-line summary for the GUI event log."""
    if event.get("source") == "tradingview":
        return format_tv_log_line(event)

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
