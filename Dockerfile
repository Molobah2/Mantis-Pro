FROM python:3.12

# Flush Python stdout/stderr immediately so container logs are visible
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Node.js 20 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

COPY . .

# Build the wallet-helper TypeScript (server)
RUN cd wallet-helper && npm ci && npm run build
# Build the AGW browser connect bundle (connect-src → dist/connect.bundle.js)
RUN cd wallet-helper/connect-src && npm install && node build.mjs

EXPOSE 8080
CMD ["python", "agent.py"]
