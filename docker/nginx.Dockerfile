# ═════════════════════════════════════════════════════════════════════════════
# NWS NBM Viewer — production edge gateway
#
# nginx + ngx_brotli (dynamic module) + our gateway config.  Gzip always;
# Brotli for JSON/GeoJSon/text when this image is used (see docker/nginx.conf
# for the stock-nginx gzip-only fallback).
#
#   docker build -t nbm-gateway -f docker/nginx.Dockerfile .
#
# The brotli modules are compiled against the *exact* nginx version in the
# base image (--with-compat), and wired in via drop-in files so docker/nginx.conf
# stays valid with or without them.
# ═════════════════════════════════════════════════════════════════════════════

FROM nginx:1.27-alpine AS brotli-build

# google/ngx_brotli is maintenance-frozen; pin its final master commit
# (2023-10-09, "Fix build failed for nginx version branch") so module ABI and
# vendored brotli v1.1.0 never move underneath a rebuilt image.
ARG NGX_BROTLI_REF=a71f9312c2deb28875acc7bacfdd5695a111aa53

# Shared CI egress IPs see occasional transient failures from dl-cdn, and
# --no-cache has no retry loop of its own, so wrap every network step.
RUN set -eu; \
    retry() { for i in 1 2 3 4 5; do "$@" && return 0; echo "retry $i: $*"; sleep $((i * 4)); done; return 1; }; \
    retry apk add --no-cache build-base cmake git pcre2-dev zlib-dev

# Match the base image's nginx version exactly so the module ABI aligns.
RUN set -eu; \
    retry() { for i in 1 2 3 4 5; do "$@" && return 0; echo "retry $i: $*"; sleep $((i * 4)); done; return 1; }; \
    NGINX_VERSION="$(nginx -v 2>&1 | sed 's|.*nginx/||')" \
    && retry wget -qO /tmp/nginx.tar.gz "http://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" \
    && mkdir -p /build && tar -xzf /tmp/nginx.tar.gz -C /build \
    && rm -rf /build/ngx_brotli \
    && retry git clone --depth 1 "https://github.com/google/ngx_brotli.git" /build/ngx_brotli \
    && cd /build/ngx_brotli && retry git fetch --depth 1 origin "${NGX_BROTLI_REF}" && git checkout FETCH_HEAD \
    && retry git submodule update --init --depth 1 \
    && cd /build/ngx_brotli/deps/brotli \
    && mkdir -p out && cd out \
    && cmake -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
        -DBROTLI_DISABLE_TESTS=ON .. \
    && cmake --build . --target brotlienc brotlicommon -j"$(nproc)" \
    && cd "/build/nginx-${NGINX_VERSION}" \
    && ./configure --with-compat \
        --add-dynamic-module=/build/ngx_brotli \
    && make -j"$(nproc)" modules \
    && test -f objs/ngx_http_brotli_filter_module.so

FROM nginx:1.27-alpine

# Static brotli compression of pre-compressed assets is pointless here (all
# content is proxied), so only the filter module is required at runtime.
COPY --from=brotli-build /build/nginx-*/objs/ngx_http_brotli_filter_module.so /usr/lib/nginx/modules/

RUN mkdir -p /etc/nginx/modules-enabled /var/cache/nginx/tiles /var/cache/nginx/api \
    && printf 'load_module /usr/lib/nginx/modules/ngx_http_brotli_filter_module.so;\n' \
        > /etc/nginx/modules-enabled/60-brotli.conf \
    && printf 'brotli on;\nbrotli_comp_level 5;\nbrotli_min_length 1024;\nbrotli_buffers 16 8k;\nbrotli_types application/json application/geo+json application/manifest+json application/javascript text/css text/plain text/xml image/svg+xml;\n' \
        > /etc/nginx/conf.d/brotli.conf \
    && rm -f /etc/nginx/conf.d/default.conf

COPY docker/nginx.conf /etc/nginx/nginx.conf

# Fail the build loudly on a bad config.  /etc/hosts is read-only during
# builds, so `nginx -t` runs against a sed copy of the config where the
# compose service names are swapped for literal stubs — everything else
# (syntax, cache paths, drop-ins, brotli module load) is checked verbatim.
# At runtime the real config resolves `backend`/`frontend` via Docker DNS.
RUN sed -e 's|server backend:8000;|server 127.0.0.1:1;|' \
        -e 's|server frontend:3000;|server 127.0.0.1:2;|' \
        /etc/nginx/nginx.conf > /etc/nginx/nginx.buildcheck.conf \
    && nginx -t -c /etc/nginx/nginx.buildcheck.conf \
    && rm /etc/nginx/nginx.buildcheck.conf

EXPOSE 80

STOPSIGNAL SIGQUIT

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
