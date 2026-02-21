# Пуш в новый репозиторий на GitHub

Remote **origin** уже настроен: `https://github.com/vBoykoGit/mt5-telegram-notifier.git`

## 1. Создайте репозиторий на GitHub

1. Откройте https://github.com/new
2. **Repository name:** `mt5-telegram-notifier`
3. Оставьте пустым (без README, .gitignore, license)
4. Нажмите **Create repository**

## 2. Коммит и пуш (в терминале из папки проекта)

```bash
cd E:\Git\mt5-telegram-notifier

git add .
git commit -m "Initial commit: MT5 Telegram Notifier GUI"
git branch -M main
git push -u origin main
```

Если при `git commit` появится ошибка `unknown option 'trailer'`, выполните коммит с отключением hooks:

```bash
git commit --no-verify -m "Initial commit: MT5 Telegram Notifier GUI"
```

После этого снова выполните:

```bash
git branch -M main
git push -u origin main
```
