FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GHOST_ID=mordy

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY engine/ engine/
COPY characters/ characters/
COPY data/ data/

# Mutable runtime state should live on a Railway volume via STATE_DIR,
# not on top of data/lore/.
CMD ["python", "bot.py"]
