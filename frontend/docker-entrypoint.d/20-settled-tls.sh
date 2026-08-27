#!/bin/sh
# Choose between the plain-HTTP and the TLS server configuration at
# container start (§1.3a), and render the two fragments that depend on
# runtime settings.
#
# Runs from nginx's own /docker-entrypoint.d, before nginx starts. The
# numbering puts it after the image's own 10-listen-on-ipv6 and before
# 30-tune-worker-processes; nothing here depends on either.
set -e

CERT_DIR=/etc/nginx/certs
CERT="$CERT_DIR/fullchain.pem"
KEY="$CERT_DIR/privkey.pem"
OUT=/etc/nginx/conf.d/default.conf
FRAGMENTS=/etc/nginx/settled

TLS="${SETTLED_TLS:-auto}"
HSTS="${SETTLED_HSTS:-off}"
HTTPS_PORT="${SETTLED_PUBLIC_HTTPS_PORT:-8443}"

log() { echo "[settled-tls] $*"; }

have_cert() { [ -r "$CERT" ] && [ -r "$KEY" ]; }

case "$TLS" in
    off|false|0)
        use_tls=no
        log "SETTLED_TLS=$TLS — serving plain HTTP."
        ;;
    on|true|1)
        # Asked for explicitly: a missing certificate is a failure, not a
        # quiet downgrade to the thing you said you did not want.
        if have_cert; then
            use_tls=yes
        else
            log "SETTLED_TLS=$TLS but $CERT and/or $KEY is missing or unreadable."
            log "Generate a pair with scripts/generate-self-signed-cert.sh, or set SETTLED_TLS=off."
            exit 1
        fi
        ;;
    *)
        if have_cert; then
            use_tls=yes
        else
            use_tls=no
            log "No certificate at $CERT — serving plain HTTP."
            log "The session cookie and your password cross the network in the clear."
            log "See docs/deployment.md, or set SETTLED_TLS=off to silence this."
        fi
        ;;
esac

mkdir -p "$FRAGMENTS"
cp /etc/nginx/settled-src/app.conf "$FRAGMENTS/app.conf"
cp /etc/nginx/settled-src/security-headers.conf "$FRAGMENTS/security-headers.conf"

if [ "$use_tls" = yes ]; then
    if [ "$HTTPS_PORT" = "443" ]; then
        target='https://$host$request_uri'
    else
        target="https://\$host:$HTTPS_PORT\$request_uri"
    fi
    printf 'location / {\n    return 301 %s;\n}\n' "$target" > "$FRAGMENTS/redirect.conf"

    case "$HSTS" in
        on|true|1)
            # Two years, the shortest value the major preload lists accept,
            # and no `preload` directive: submitting a LAN host to a browser
            # vendor's permanent list is not undoable.
            echo 'add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;' \
                > "$FRAGMENTS/hsts.conf"
            log "HSTS on. Do not do this with a self-signed certificate."
            ;;
        *)
            echo '# HSTS off (SETTLED_HSTS is not "on").' > "$FRAGMENTS/hsts.conf"
            ;;
    esac

    cp /etc/nginx/settled-src/tls.conf "$OUT"
    log "TLS on; HTTP on :80 redirects to $target"
else
    cp /etc/nginx/settled-src/http.conf "$OUT"
fi
