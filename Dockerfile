FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# BOT_TOKEN must be supplied at runtime, e.g.:
#   docker run -e BOT_TOKEN=xxxx poker-bot
CMD ["python", "bot.py"]
