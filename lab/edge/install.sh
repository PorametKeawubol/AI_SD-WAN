#!/usr/bin/env bash
set -euo pipefail
test "$(id -u)" = 0 || { echo 'Run on an isolated edge VM as root.' >&2; exit 1; }
. /etc/os-release
test "$VERSION_CODENAME" = focal || { echo 'Pinned lab requires Ubuntu 20.04 focal.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg python3
keydir=$(mktemp -d)
trap 'rm -rf -- "$keydir"' EXIT
curl --fail --silent --show-error --location https://deb.flexiwan.com/flexiWAN/gpgkey/flexiwan.source.gpg.key -o "$keydir/key1.asc"
curl --fail --silent --show-error --location https://deb.flexiwan.com/flexiWAN/gpgkey/flexiwan.ng.source.gpg.key -o "$keydir/key2.asc"
gpg --show-keys --with-colons "$keydir/key1.asc" | grep -q 'C3897D73CFBEC7D09B9F90A059940D52B0B2DEF2' || exit 1
gpg --show-keys --with-colons "$keydir/key2.asc" | grep -q 'C0D28A983A813B9CBDCBD08DB3C27F8C1693E5B9' || exit 1
cat "$keydir/key1.asc" "$keydir/key2.asc" > /usr/share/keyrings/ai-sdwan-flexiwan.asc
printf '%s\n' 'deb [arch=amd64 signed-by=/usr/share/keyrings/ai-sdwan-flexiwan.asc] https://deb.flexiwan.com/flexiWAN focal main' > /etc/apt/sources.list.d/ai-sdwan-flexiwan.list
apt-get update
curl --fail --silent --show-error --location https://deb.flexiwan.com/flexiWAN/pool/main/f/flexiwan-router/flexiwan-router_6.3.35.focal_amd64.deb -o "$keydir/edge.deb"
printf '%s  %s\n' '7d902afd3e80dd957d5d6163217c893ec3f55806d76c7ea7516b63bacfe0eddc' "$keydir/edge.deb" | sha256sum --check --status
apt-get -o Dpkg::Options::=--force-confold install -y "$keydir/edge.deb"
apt-mark hold flexiwan-router
# Prepare hugepages and kernel parameters with the vendor's own checker.
fwsystem_checker --quiet
# The upstream checker can return zero even when checks fail. Verify VPP start
# and packet forwarding in provisioning/evaluation; its exit code is not evidence.
