# MT5 Telegram Notifier

GUI-приложение для мониторинга торговых событий из нескольких терминалов MetaTrader 5 и отправки уведомлений в Telegram (текст + скриншот графика).

## Как это работает

1. **MQL5 EA** (`AO_Pattern_Bot`) записывает JSON-события и скриншоты графика в общую папку MT5 (`Common/Files/tg_events/`) при каждом торговом событии (открытие/закрытие позиции, SL, TP, отложенный ордер). Также периодически пишет heartbeat-файл для индикации активности.

2. **Python GUI** мониторит эту папку, отображает статус терминалов в окне, форматирует и отправляет события в Telegram.

## Установка

### 1. Python-зависимости

```bash
pip install -r requirements.txt
```

### 2. Настройка Telegram-бота

1. Откройте [@BotFather](https://t.me/BotFather) в Telegram
2. Создайте нового бота командой `/newbot`
3. Скопируйте полученный токен
4. Отправьте любое сообщение вашему боту
5. Получите `chat_id`:
   ```
   https://api.telegram.org/bot<ВАШ_ТОКЕН>/getUpdates
   ```
   В ответе найдите `"chat":{"id": ЧИСЛО}` -- это ваш `chat_id`.

### 3. Конфигурация

Скопируйте `config.example.json` в `config.json` и заполните:

```json
{
  "telegram_bot_token": "123456:ABC-DEF...",
  "telegram_chat_id": "-1001234567890",
  "common_files_path": "C:\\Users\\ВАШ_ПОЛЬЗОВАТЕЛЬ\\AppData\\Roaming\\MetaQuotes\\Terminal\\Common\\Files",
  "poll_interval_sec": 2,
  "heartbeat_timeout_sec": 60,
  "heartbeat_dead_sec": 120,
  "processed_retention_days": 7
}
```

### 4. Настройка MT5

В каждом терминале MT5, где запущен EA:

- `InpEnableTelegramEvents` = `true`
- `InpTerminalName` = уникальное имя (напр. `"Акк_1"`, `"Demo"`)
- `InpScreenshotWidth` / `InpScreenshotHeight` = размер скриншота (по умолчанию 1920x1080)
- `InpHeartbeatIntervalSec` = интервал heartbeat (по умолчанию 30 сек)

Одинаковый `InpTerminalName` можно задать всем EA в одном терминале -- они будут сгруппированы в GUI.

## Запуск

```bash
python app.py
```

Или без окна консоли:

```bash
pythonw app.py
```

### Автозапуск при входе в Windows

1. Создайте ярлык на `start_notifier.bat`
2. Поместите ярлык в `shell:startup` (нажмите Win+R, введите `shell:startup`)

## Структура файлов

```
mt5-telegram-notifier/
├── notifier/
│   ├── __init__.py
│   ├── app.py              # Точка входа, GUI (CustomTkinter)
│   ├── watcher.py          # Фоновый поток мониторинга файлов
│   ├── telegram_sender.py  # Отправка в Telegram (sendPhoto/sendMessage)
│   └── formatter.py        # Форматирование событий в HTML
├── config.example.json     # Пример конфигурации
├── config.json             # Ваша конфигурация (в .gitignore)
├── requirements.txt       # Python-зависимости
├── start_notifier.bat      # Скрипт автозапуска
└── README.md
```
