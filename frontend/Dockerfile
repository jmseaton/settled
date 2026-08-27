# Frontend image — build the bundle, then serve it as static files.
#
# Vite's dev server is not in the runtime image. It exists to rebuild on
# save; in a deployment it is a slower, less robust nginx with a filesystem
# watcher attached.

FROM node:22-alpine AS build
WORKDIR /app

COPY package.json package-lock.json* ./
# `npm ci` when a lockfile is present — reproducible, and it fails loudly on
# a lockfile that has drifted from package.json rather than quietly resolving
# something else.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .
# `npm run build` is `tsc -b && vite build`, so a type error fails the image
# build rather than shipping.
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html

# The server configs ship as sources rather than as the live config: which
# one becomes /etc/nginx/conf.d/default.conf is decided at container start
# from whether a certificate is mounted (§1.3a).
COPY nginx/ /etc/nginx/settled-src/
COPY docker-entrypoint.d/20-settled-tls.sh /docker-entrypoint.d/20-settled-tls.sh
RUN chmod +x /docker-entrypoint.d/20-settled-tls.sh

# 443 as well as 80. Nothing listens on it without a certificate — the
# entrypoint falls back to the HTTP-only config — so exposing it always is
# a description of the image rather than a claim about the deployment.
EXPOSE 80 443
