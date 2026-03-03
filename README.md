# MT5 Trade Notifier

Приложение для отправки уведомлений о торговых событиях из MetaTrader 5 и TradingView в Telegram.

## Возможности

- Уведомления об открытии/закрытии сделок, SL/TP из MT5
- Уведомления о сигналах индикаторов TradingView (через webhook)
- Скриншоты графиков MT5 (через EA) и TradingView (через Playwright)
- GUI с отображением активных MT5-терминалов и TradingView-индикаторов
- Сворачивание в системный трей

## Установка

### 1. Python-зависимости

```bash
pip install -r requirements.txt
```

### 2. Playwright (для скриншотов TradingView)

```bash
playwright install chromium
```

Если Playwright не установлен, TradingView-уведомления будут отправляться без скриншотов.

## Настройка

### Telegram-бот

1. Создайте бота через [@BotFather](https://t.me/BotFather)
2. Получите токен бота и chat_id
3. Введите их в приложении (поля "Токен бота" и "Chat ID") и нажмите "Сохранить"

### MT5

Установите EA `TelegramNotifier` на графики в MetaTrader 5. EA записывает события и скриншоты в общую папку `%APPDATA%\MetaQuotes\Terminal\Common\Files\tg_events\`.

### TradingView (webhook)

Для получения сигналов от TradingView нужна **платная подписка** (Pro/Pro+/Premium) для поддержки webhook-алертов.

#### Шаг 1: Публичный URL

Приложение запускает HTTP-сервер на локальном порту (по умолчанию 8080). Для открытия порта в брандмауэре Windows в интерфейсе есть поле ввода порта и кнопка **«Открыть порт»**. При нажатии приложение добавит правило в брандмауэр Windows для входящего TCP-соединения. Для этого может потребоваться запуск приложения **от имени администратора**; если прав недостаточно, рядом с кнопкой отобразится подсказка.

TradingView требует публичный URL для отправки webhook. Варианты:

**ngrok** (самый простой):
```bash
ngrok http 8080
```
Скопируйте полученный URL (например `https://abc123.ngrok-free.app`).

**Cloudflare Tunnel** (стабильнее):
```bash
cloudflared tunnel --url http://localhost:8080
```

#### Шаг 2: Настройка алерта в TradingView

1. Добавьте индикатор "AO Cross & Color Markers" на график
2. При необходимости задайте **Webhook Secret** в настройках индикатора (должен совпадать с секретом в приложении)
3. Правый клик на графике -> **Add Alert**
4. Condition: выберите индикатор "AO Cross & Color Markers"
5. Trigger: **Any alert() function call**
6. Actions -> **Webhook URL**: `https://<ваш-url>/webhook/tradingview`
7. Нажмите **Create**

Один алерт покрывает все сигналы со всех таймфреймов.

#### Шаг 3: Настройка в приложении

В приложении задайте:
- **Webhook включен**: включает/выключает HTTP webhook-сервер
- **Webhook порт**: порт HTTP-сервера (по умолчанию 8080)
- **Webhook секрет**: опциональный секрет для проверки подлинности запросов
- **Публичный URL**: базовый публичный адрес (ngrok/Cloudflare/IP), приложение само добавляет `/webhook/tradingview`
- **Скриншот, сек**: задержка загрузки графика TradingView перед снимком

Webhook-поля сохраняются автоматически при изменении. Кнопка **Сохранить** остается для ручного сохранения всех настроек.

## Запуск

```bash
python -m notifier.app
```

Или через bat-файл (без окна консоли):
```bash
start_notifier.bat
```

## Конфигурация

Файл `config.json` (создается автоматически из `config.example.json`):

| Параметр | Описание | По умолчанию |
|---|---|---|
| `telegram_bot_token` | Токен Telegram-бота | `""` |
| `telegram_chat_id` | ID чата/группы | `""` |
| `common_files_path` | Путь к Common/Files MT5 | авто |
| `poll_interval_sec` | Интервал опроса папки событий | `2` |
| `heartbeat_timeout_sec` | Таймаут для статуса "warn" | `60` |
| `heartbeat_dead_sec` | Таймаут для статуса "dead" | `120` |
| `processed_retention_days` | Хранение обработанных файлов | `7` |
| `webhook_enabled` | Включить webhook-сервер | `true` |
| `webhook_port` | Порт webhook-сервера | `8080` |
| `webhook_secret` | Секрет для проверки запросов | `""` |
| `webhook_public_url` | Базовый публичный URL webhook | `""` |
| `chart_screenshot_wait_sec` | Ожидание загрузки графика | `5` |
