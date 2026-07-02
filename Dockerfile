FROM python:3.12

WORKDIR /app

# Install Node.js 20 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

COPY . .

# Build the wallet-helper TypeScript
RUN cd wallet-helper && npm ci && npm run build

EXPOSE 8080
CMD ["python", "agent.py"]
