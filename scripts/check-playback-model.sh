#!/bin/sh
set -eu

TLA_VERSION="1.7.4"
TLA_SHA256="936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
TLA_CACHE="${XDG_CACHE_HOME:-${HOME}/.cache}/radcliffe/tla/${TLA_VERSION}"
TLA_JAR="${TLA_CACHE}/tla2tools.jar"
TLA_URL="https://github.com/tlaplus/tlaplus/releases/download/v${TLA_VERSION}/tla2tools.jar"

if [ ! -f "${TLA_JAR}" ]; then
    mkdir -p "${TLA_CACHE}"
    temporary="${TLA_JAR}.tmp.$$"
    trap 'rm -f "${temporary}"' EXIT HUP INT TERM
    curl -fsSL "${TLA_URL}" -o "${temporary}"
    actual="$(shasum -a 256 "${temporary}" | awk '{print $1}')"
    if [ "${actual}" != "${TLA_SHA256}" ]; then
        echo "TLA+ tools checksum mismatch: ${actual}" >&2
        exit 1
    fi
    mv "${temporary}" "${TLA_JAR}"
    trap - EXIT HUP INT TERM
fi

actual="$(shasum -a 256 "${TLA_JAR}" | awk '{print $1}')"
if [ "${actual}" != "${TLA_SHA256}" ]; then
    echo "Cached TLA+ tools checksum mismatch: ${actual}" >&2
    exit 1
fi

if [ -z "${JAVA_HOME:-}" ] && command -v brew >/dev/null 2>&1; then
    JAVA_HOME="$(brew --prefix openjdk@21)/libexec/openjdk.jdk/Contents/Home"
    export JAVA_HOME
fi

exec "${JAVA_HOME:+${JAVA_HOME}/bin/}java" \
    -XX:+UseParallelGC \
    -jar "${TLA_JAR}" \
    -cleanup \
    -workers auto \
    -config formal/playback/BoundedPlayback.cfg \
    formal/playback/BoundedPlayback.tla
