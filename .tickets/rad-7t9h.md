---
id: rad-7t9h
status: open
deps: []
links: [rad-erac, rad-tg8o]
created: 2026-07-20T04:15:35Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-erac
tags: [radcliffe, agent, unix, sandbox, network, egress, security]
---
# Allow sandboxed Unix agents to access the public internet

The background-agent prompt advertises policy-proxied public internet access, but DockerSandbox currently creates an internal-only Docker network without an egress gateway. Commands such as curl, wget, Git, and workspace-local package installers therefore cannot reach authorized public resources.

## Design

Provide a default-deny egress gateway or proxy between each disposable internal job network and the public internet. Keep the agent container without direct routing to the host or LAN; resolve and revalidate destinations at the trusted boundary; allow only bounded user-authorized public HTTP/HTTPS acquisition; reject loopback, private, link-local, multicast, carrier-grade NAT/Tailscale, cloud-metadata, Docker-host, control-plane, and other local destinations across IPv4, IPv6, redirects, and DNS rebinding. Pass no Radcliffe, model-provider, GitHub, keychain, or host credentials into the sandbox. Keep transfer, timeout, concurrency, and cleanup limits explicit, and make the agent prompt reflect actual capability.

## Acceptance Criteria

A deployed sandbox job can use ordinary curl, wget, HTTPS Git, Python/Node package acquisition, and a small public-web research workflow through the bounded egress path, then promote checked workspace artifacts. Direct container egress remains unavailable; private/LAN/Tailscale/host/control-plane/cloud-metadata targets are denied for literal addresses, DNS answers, redirects, alternate address families, and rebinding attempts. Downloads, connections, response sizes, and runtimes are bounded; proxy and container cleanup are deterministic after success, failure, cancellation, and timeout; no protected credential or unrelated host mount enters the job. Deterministic tests cover allow and deny policy, redirect and DNS edge cases, unavailable-proxy behavior, and cleanup; an opt-in live smoke fetches pinned public fixtures and completes an end-to-end background research job without any physical-device or user-testing step.

