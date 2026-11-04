FROM python:3.11-slim

# System standard libraries setup for headless engine
RUN apt-get update && apt-get install -y \
    wget gnupg libglib2.0-0 libnss3 libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 \
    librandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY . /workspace

RUN pip install --no-cache-dir -r requirements.txt
# Playwright chromium downloads
RUN playwright install chromium
RUN playwright install-deps chromium

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
