# Use the official Playwright Python image (includes all OS dependencies)
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create a non-root user (good practice and helps with some sandbox issues)
RUN useradd -m -s /bin/bash renderuser
USER renderuser

# Copy application files
COPY --chown=renderuser:renderuser . .

# Command to easily bind uvicorn to Render's dynamic $PORT
CMD uvicorn juicebox_api:app --host 0.0.0.0 --port ${PORT:-10000}
