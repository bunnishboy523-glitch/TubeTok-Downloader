FROM python:3.12-slim

# Устанавливаем Chromium/chromedriver (для Selenium) и ffmpeg (для склейки
# видео+аудио при выборе языка озвучки)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV PATH="/usr/lib/chromium:${PATH}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render передаёт порт через переменную окружения PORT
EXPOSE 10000

CMD ["python", "app_bot.py"]
