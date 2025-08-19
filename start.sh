#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# 1) Активируем venv
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
else
  echo "❌ .venv не найден. Активируй окружение вручную."
  exit 1
fi

# 2) Убиваем старые процессы
pkill -f "python .*bot.py" 2>/dev/null || true
pkill -f "uvicorn"         2>/dev/null || true
pkill -f "ngrok http 8000" 2>/dev/null || true
pkill -f "getUpdates"      2>/dev/null || true

# 3) Стартуем uvicorn в фоне
echo "▶️  Запускаю uvicorn на 8000…"
uvicorn main:app --port 8000 --reload > /tmp/uvicorn.log 2>&1 &
UV_PID=$!
sleep 1

# 4) Стартуем ngrok в фоне и берём публичный URL
echo "▶️  Запускаю ngrok…"
ngrok http 8000 > /tmp/ngrok.log 2>&1 &
NGROK_PID=$!
sleep 2

NEW_URL="$(curl -s http://127.0.0.1:4040/api/tunnels | grep -o 'https://[0-9a-z.-]*ngrok-free.app' | head -n1 || true)"
if [ -z "${NEW_URL:-}" ]; then
  echo "❌ Не смог получить публичный URL от ngrok. Проверь /tmp/ngrok.log"
  exit 1
fi
echo "🌐 NGROK_URL: $NEW_URL"

# 5) Обновляем WEBAPP_URL в .env (macOS и Linux вариант)
if sed --version >/dev/null 2>&1; then
  sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=$NEW_URL|" .env || echo "WEBAPP_URL=$NEW_URL" >> .env
else
  /usr/bin/sed -i '' "s|^WEBAPP_URL=.*|WEBAPP_URL=$NEW_URL|" .env || echo "WEBAPP_URL=$NEW_URL" >> .env
fi

# 6) Подгружаем токен/URL из .env
export $(grep -E '^(BOT_TOKEN|WEBAPP_URL)=' .env | xargs)

# 7) Сбрасываем вебхук и чистим очередь апдейтов
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true" >/dev/null || true

echo "✅ WEBAPP_URL обновлён: $WEBAPP_URL"
echo "▶️  Запускаю бота…"

# При выходе гасим ngrok и uvicorn
cleanup() { kill "$NGROK_PID" "$UV_PID" 2>/dev/null || true; }
trap cleanup EXIT

python bot.py
