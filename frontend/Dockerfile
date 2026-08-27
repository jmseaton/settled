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
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
