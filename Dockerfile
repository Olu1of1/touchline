FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000

COPY requirements-app.txt .
RUN pip install --no-cache-dir -r requirements-app.txt

COPY touchline/ touchline/
COPY web/ web/
COPY data/ data/
COPY timelines/ timelines/

EXPOSE 8000
CMD ["python", "-m", "touchline.server"]
