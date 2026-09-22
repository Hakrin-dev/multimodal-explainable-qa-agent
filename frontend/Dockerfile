ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/node:22-alpine AS build
WORKDIR /app
# restricted-network robustness: npmmirror registry + aggressive fetch retries
ENV npm_config_registry=https://registry.npmmirror.com \
    npm_config_fetch_retries=5 \
    npm_config_fetch_retry_maxtimeout=120000 \
    npm_config_fetch_timeout=300000
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN corepack enable && pnpm install --frozen-lockfile --ignore-scripts --fetch-timeout 300000
COPY . .
# bypass pnpm's script runner (non-TTY escalates ignored-build warnings to
# errors in pnpm 12); esbuild binary ships via @esbuild/linux-x64 optional dep
RUN ./node_modules/.bin/vue-tsc --noEmit -p tsconfig.app.json \
 && ./node_modules/.bin/vite build --configLoader runner

FROM ${REGISTRY}/library/nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
