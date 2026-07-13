# LiveKit media edge

Milestone 1 pins LiveKit Server `v1.13.3` and uses host networking on a Linux
VM. Host networking is intentional: it avoids a Docker NAT layer in the media
path and lets LiveKit advertise the VM's public address. The worker and Android
app are ordinary outbound participants; the owner's home network opens no
inbound port.

## Public deployment

1. Point `LIVEKIT_DOMAIN` and `TURN_DOMAIN` at the VM. Obtain trusted
   certificates for both names. Signal TLS may terminate at your reverse proxy;
   copy the TURN full chain and private key to paths configured as
   `TURN_CERT_FILE` and `TURN_KEY_FILE` (the defaults are under `certs/`).
2. Copy `.env.example` to `.env`, generate a random API key and at least
   32-byte API secret, and set the domains. Never commit `.env` or `certs/`.
3. Run `./configure.sh`, then
   `docker compose --profile production up -d livekit`.
4. Terminate HTTPS/WSS for `LIVEKIT_DOMAIN` at a reverse proxy forwarding to
   `127.0.0.1:7880`. Do not proxy the RTC ports through an HTTP proxy.
5. Open TCP `443` (signal proxy and TURN/TLS according to your topology), TCP
   `7881`, UDP `3478`, and UDP `50000:60000`. If signal and TURN share one IP on
   port 443, use a layer-4 TLS router or a second IP; two processes cannot bind
   the same TCP socket.
6. Run `./diagnose.sh`. Then force UDP off on the Pixel network and confirm the
   LiveKit connection report selects TURN/TLS before accepting the deployment.

The template uses a dedicated TURN hostname with embedded authentication. A
self-signed TURN certificate is not valid for the Android production path.

## Local profile

`docker compose --profile local up livekit-local` starts a development server
at `ws://localhost:7880` with the documented `devkey/secret` credentials. It is
for automated and LAN tests only: it has no trusted TLS or production TURN
coverage.

## Diagnostics

`diagnose.sh` verifies DNS, signal TLS/health, TURN TLS, and exposed TCP ports.
The M1 conformance report additionally records the client-selected ICE
candidate pair for a direct-UDP run and a TURN-only run. Prometheus metrics are
bound at `:6789`; expose them only to a trusted monitoring network.

LiveKit's deployment documentation requires a trusted certificate and separate
TURN domain, and recommends host networking for Dockerized media servers. See
the official [deployment guide](https://docs.livekit.io/transport/self-hosting/deployment/)
and [port reference](https://docs.livekit.io/transport/self-hosting/ports-firewall/).
