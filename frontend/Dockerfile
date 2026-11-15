FROM node:22.21.0-bookworm-slim AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22.21.0-bookworm-slim AS builder

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_GOALPULSE_PROFILE
ARG NEXT_PUBLIC_API_MODE
ARG NEXT_PUBLIC_GATEWAY_URL
ARG NEXT_PUBLIC_DEMO_USER_A_USERNAME
ARG NEXT_PUBLIC_DEMO_USER_A_PASSWORD
ARG NEXT_PUBLIC_DEMO_USER_B_USERNAME
ARG NEXT_PUBLIC_DEMO_USER_B_PASSWORD
ENV NEXT_PUBLIC_GOALPULSE_PROFILE=${NEXT_PUBLIC_GOALPULSE_PROFILE} \
    NEXT_PUBLIC_API_MODE=${NEXT_PUBLIC_API_MODE} \
    NEXT_PUBLIC_GATEWAY_URL=${NEXT_PUBLIC_GATEWAY_URL} \
    NEXT_PUBLIC_DEMO_USER_A_USERNAME=${NEXT_PUBLIC_DEMO_USER_A_USERNAME} \
    NEXT_PUBLIC_DEMO_USER_A_PASSWORD=${NEXT_PUBLIC_DEMO_USER_A_PASSWORD} \
    NEXT_PUBLIC_DEMO_USER_B_USERNAME=${NEXT_PUBLIC_DEMO_USER_B_USERNAME} \
    NEXT_PUBLIC_DEMO_USER_B_PASSWORD=${NEXT_PUBLIC_DEMO_USER_B_PASSWORD}
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22.21.0-bookworm-slim AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
RUN groupadd --system --gid 1001 goalpulse \
    && useradd --system --uid 1001 --gid goalpulse --home-dir /nonexistent goalpulse
COPY --from=builder --chown=goalpulse:goalpulse /app/public ./public
COPY --from=builder --chown=goalpulse:goalpulse /app/.next/standalone ./
COPY --from=builder --chown=goalpulse:goalpulse /app/.next/static ./.next/static

USER goalpulse
EXPOSE 3000
CMD ["node", "server.js"]
