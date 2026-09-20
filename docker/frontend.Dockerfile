# ═════════════════════════════════════════════════════════════════════════════
# Next.js 14 frontend image — MapLibre GL map viewer
#
#   target: dev   -> `next dev` with HMR (source bind-mounted by compose)
#   target: prod  -> standalone server output, non-root
# ═════════════════════════════════════════════════════════════════════════════

# ── Dependencies ─────────────────────────────────────────────────────────────
FROM node:20-bookworm-slim AS deps
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json* ./
# `npm ci` when a lockfile exists, otherwise fall back to `npm install` so a
# fresh clone (pre-lockfile) still builds.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# ── Dev: hot reload over a bind-mounted source tree ───────────────────────────
FROM node:20-bookworm-slim AS dev
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=development \
    WATCHPACK_POLLING=true \
    CHOKIDAR_USEPOLLING=true
COPY --from=deps /app/node_modules ./node_modules
COPY . .
EXPOSE 3000
# -H 0.0.0.0 makes the dev server reachable from outside the container
# (live-preview hosts and Docker port mapping).
CMD ["npm", "run", "dev", "--", "-H", "0.0.0.0", "-p", "3000"]

# ── Build ───────────────────────────────────────────────────────────────────
FROM node:20-bookworm-slim AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build && mkdir -p public

# ── Prod: minimal standalone runtime ────────────────────────────────────────
FROM node:20-bookworm-slim AS prod
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN groupadd --system --gid 1001 nodejs && \
    useradd --system --uid 1001 --gid nodejs nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
