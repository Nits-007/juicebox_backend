FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium

COPY . .

ENV PORT=10000

EXPOSE ${PORT}

CMD uvicorn juicebox_api:app --host 0.0.0.0 --port ${PORT}
