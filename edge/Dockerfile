FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf

HEALTHCHECK --interval=15s --timeout=5s --retries=10 \
  CMD wget -q -O /dev/null http://127.0.0.1:8088/health || exit 1
