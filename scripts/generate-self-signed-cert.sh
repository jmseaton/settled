#!/usr/bin/env bash
# Generate a self-signed certificate for the LAN deployment (§1.3a).
#
#   ./scripts/generate-self-signed-cert.sh                 # this host, guessed
#   ./scripts/generate-self-signed-cert.sh settled.lan 192.168.1.40
#
# A self-signed certificate is not a weaker cipher than a real one — the
# transport is identically encrypted. What it lacks is a third party
# vouching that the host is who it says, which for a box on your own LAN you
# are in a better position to verify than any CA is. The cost is the
# browser's warning on first visit, and that warning is genuinely load-
# bearing: click through it on a *public* network and you have accepted
# whatever answered.
#
# If the host has a real name pointed at it, use Let's Encrypt instead and
# drop the resulting fullchain.pem/privkey.pem in the same place. See
# docs/deployment.md.
set -euo pipefail

CERT_DIR="${CERT_DIR:-certs}"
DAYS="${DAYS:-825}"
FORCE="${FORCE:-0}"

names=("$@")
if [ ${#names[@]} -eq 0 ]; then
    names=("$(hostname)" "localhost")
    # Whatever address the default route leaves by — the one a browser on
    # the LAN will actually type. Best-effort; pass names explicitly if the
    # host is multi-homed and this picks the wrong one.
    lan_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || true)"
    [ -n "${lan_ip:-}" ] && names+=("$lan_ip")
    names+=("127.0.0.1")
fi

command -v openssl >/dev/null || { echo "openssl is not installed" >&2; exit 1; }

mkdir -p "$CERT_DIR"
cert="$CERT_DIR/fullchain.pem"
key="$CERT_DIR/privkey.pem"

if { [ -e "$cert" ] || [ -e "$key" ]; } && [ "$FORCE" != "1" ]; then
    echo "$cert or $key already exists. Re-run with FORCE=1 to replace them." >&2
    echo "Replacing the certificate makes every browser warn again on next visit." >&2
    exit 1
fi

# One SAN entry per name, IP: for the ones that parse as addresses. Modern
# browsers ignore the Common Name entirely, so a certificate whose only
# identity is in the CN is a certificate for nothing.
sans=""
for name in "${names[@]}"; do
    [ -z "$name" ] && continue
    if [[ "$name" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        sans+="IP:$name,"
    else
        sans+="DNS:$name,"
    fi
done
sans="${sans%,}"

echo "Generating a ${DAYS}-day self-signed certificate for: ${sans}"

# P-256 rather than RSA-2048: same practical security, faster handshake,
# and every browser released this decade supports it.
openssl req -x509 -nodes \
    -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -keyout "$key" -out "$cert" \
    -days "$DAYS" -sha256 \
    -subj "/CN=${names[0]}" \
    -addext "subjectAltName=${sans}" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth" 2>/dev/null

# The key is the whole of the transport's security. nginx reads it as root
# before dropping privileges, so 600 is enough and 644 is not.
chmod 600 "$key"
chmod 644 "$cert"

echo
echo "  $cert"
echo "  $key   (mode 600 — do not commit, do not back up to anywhere shared)"
echo
echo "Next:"
echo "  docker compose up -d          # nginx picks the certificate up at start"
echo "  open https://${names[0]}:\${SETTLED_TLS_PORT:-8443}"
echo
echo "The first visit warns. To stop it warning, import $cert into the"
echo "trust store of each machine you use — as a certificate you trust, not"
echo "as a certificate authority."
