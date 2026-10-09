FROM node:24.14.1-bookworm-slim@sha256:b506e7321f176aae77317f99d67a24b272c1f09f1d10f1761f2773447d8da26c
WORKDIR /app
COPY vendor/fleximanage/backend/package*.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY vendor/fleximanage/backend/ ./
COPY scripts/seed_controller.js /lab/seed_controller.js
EXPOSE 3443
CMD ["node", "bin/www"]
