set -e

# 1) активируем venv и гасим хвосты
source .venv/bin/activate
pkill -f "uvicorn|ngrok|python .*bot.py" 2>/dev/null || true
sleep 1

# 2) поднимаем uvicorn на 8000
uvicorn main:app --host 127.0.0.1 --port 8000 --reload > /tmp/uvicorn.log 2>&1 &
sleep 2

# проверяем, что живой
if ! curl -sI http://127.0.0.1:8000/ | head -1 | grep -q 200; then
  echo "❌ Uvicorn не отвечает, лог ниже:"
  tail -n 60 /tmp/uvicorn.log
  exit 1
fi

# 3) поднимаем ngrok и берём публичный URL
ngrok http 8000 > /tmp/ngrok.log 2>&1 &
sleep 2
URL=$(curl -s http://127.0.0.1:4040/api/tunnels | grep -o 'https://[0-9a-z.-]*ngrok-free.app' | head -n1)
if [ -z "$URL" ]; then
  echo "❌ Не получил URL от ngrok. Лог:"
  tail -n 60 /tmp/ngrok.log
  exit 1
fi

# 4) записываем в .env и сбрасываем вебхук
sed -i '' "s|^WEBAPP_URL=.*|WEBAPP_URL=$URL|" .env 2>/dev/null || sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=$URL|" .env
echo "🌐 WEBAPP_URL=$URL"

export $(grep -E '^BOT_TOKEN=' .env | xargs)
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true" >/dev/null

# 5) стартуем бота (логи останутся в этом окне)
python bot.py
