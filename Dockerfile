FROM node:20-alpine AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM node:20-alpine

RUN apk add --no-cache ffmpeg

WORKDIR /app
COPY server/package.json server/package-lock.json* ./
RUN npm ci --omit=dev
COPY server/server.js ./
COPY --from=frontend /server/public ./public

EXPOSE 3000

CMD ["node", "server.js"]
