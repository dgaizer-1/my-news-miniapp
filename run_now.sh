set -e

# Активируем окружение
source .venv/bin/activate

# 1) Останавливаем старые процессы
pkill -f "uvicorn|python .*bot.py|ngrok" 2>/dev/null || true
sleep 1

# 2) Запускаем uvicorn
uvicorn main:app --host 127.0.0.1 --port 8000 --reload > /tmp/uvicorn.log 2>&1 &
sleep 2
echo "=== local check ==="
curl -sI http://127.0.0.1:8000/ | head -1 || true

# 3) Запускаем ngrok
echo "=== start ngrok ==="
ngrok http 8000 > /tmp/ngrok.log 2>&1 &
sleep 2
URL=$(curl -s http://127.0.0.1:4040/api/tunnels | grep -o 'https://[0-9a-z.-]*ngrok-free.app' | head -n1)
echo "NGROK: $URL"

# 4) Обновляем .env
if [ -n "$URL" ]; then
  sed -i '' "s|^WEBAPP_URL=.*|WEBAPP_URL=$URL|" .env 2>/dev/null \
  || sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=$URL|" .env
fi

# 5) Чистим вебхук
export $(grep -E '^BOT_TOKEN=' .env | xargs)
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true" >/dev/null || true

# 6) Запускаем бота
echo "=== starting bot ==="
python bot.py
