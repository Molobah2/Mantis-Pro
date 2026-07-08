FROM python:3.12

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Node.js 20 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

# ── Python deps (cached unless requirements.txt changes) ──────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

# ── Node deps (cached unless package*.json files change) ──────────────
COPY wallet-helper/package.json wallet-helper/package-lock.json ./wallet-helper/
RUN cd wallet-helper && npm ci

COPY wallet-helper/connect-src/package.json wallet-helper/connect-src/package-lock.json ./wallet-helper/connect-src/
RUN cd wallet-helper/connect-src && npm ci

# ── Copy source and build ─────────────────────────────────────────────
COPY . .

RUN cd wallet-helper && npm run build
RUN cd wallet-helper/connect-src && node build.mjs

EXPOSE 8080
CMD ["python", "agent.py"]
