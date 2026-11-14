FROM node:26.10.0-bookworm-slim

ENV NODE_ENV=production \
    PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY server.js ./
COPY start.sh ./
COPY templates ./templates
COPY static ./static

RUN chown -R node:node /app

USER node

EXPOSE 80

ENTRYPOINT ["sh", "/app/start.sh"]
CMD ["node", "server.js"]
