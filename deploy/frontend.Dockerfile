# ledger frontend (Next.js 16, standalone). Build from the REPO ROOT:
#   docker build -f deploy/frontend.Dockerfile -t ledger-frontend:latest .
FROM node:20-slim AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM node:20-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
# Single-origin: browser talks only to the frontend host; Next
# reverse-proxies /api to the in-cluster backend Service.
ARG BACKEND_ORIGIN=http://ledger-backend:8070
ENV BACKEND_ORIGIN=$BACKEND_ORIGIN
# 访客（Bearer guest）的请求转给演示后端，见 next.config.ts
ARG DEMO_BACKEND_ORIGIN=http://ledger-demo-backend:8070
ENV DEMO_BACKEND_ORIGIN=$DEMO_BACKEND_ORIGIN
RUN npm run build

FROM node:20-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3070 \
    HOSTNAME=0.0.0.0
COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3070
CMD ["node", "server.js"]
