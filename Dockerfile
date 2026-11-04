FROM python:3.11-slim

# Install Chromium system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation \
    fonts-unifont \
    && rm -rf /var/lib/apt/lists/*

# Application directory
WORKDIR /workspace

# Copy application
COPY . /workspace

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser
RUN playwright install chromium

# Expose application port
EXPOSE 8000

# Start NEXUS
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
