# Use official Playwright image (includes Chromium + all system deps)
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium only to keep image smaller)
RUN playwright install chromium

# Copy application code
COPY . .

# Render sets the PORT env var automatically
ENV PORT=10000

# Expose the port
EXPOSE ${PORT}

# Start the FastAPI app with uvicorn
CMD uvicorn juicebox_api:app --host 0.0.0.0 --port ${PORT}
