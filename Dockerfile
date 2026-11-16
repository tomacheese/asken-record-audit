FROM python:3.15-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    INTERVAL_SECONDS=86400

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# uid 1000 は一般的なシングルユーザーのホストの既定値に合わせている。これにより
# --user を指定しなくても、バインドマウントした data ディレクトリに書き込める。
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown -R app:app /data
USER 1000

ENTRYPOINT ["./entrypoint.sh"]
