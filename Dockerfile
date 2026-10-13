FROM --platform=linux/amd64 python:3.11-slim

RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    gnupg2 \
    fonts-nanum \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
       | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# 폰 원격 로그인 (#77, crawler/remote_login.py): 가상 화면 · VNC · 웹 VNC · Cloudflare 임시 터널
ARG CLOUDFLARED_VERSION=2024.8.2
RUN apt-get update && apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-amd64" \
       -o /usr/local/bin/cloudflared \
    && chmod +x /usr/local/bin/cloudflared

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p output sessions

CMD ["python", "cli.py"]
