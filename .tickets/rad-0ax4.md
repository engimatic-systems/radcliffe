---
id: rad-0ax4
status: in_progress
deps: []
links: []
created: 2026-07-23T15:02:57Z
type: bug
priority: 1
assignee: coopwilliams
tags: [android, security, play-protect, operator-final-check]
---
# Harden the Android operator-review APK

The debug APK supplied for physical TalkBack review is Play Protect-classified as attempting to bypass Android security protections. Produce a release-style review artifact with a dedicated stable signer and minimal manifest surface.

## Design

Strip unused LiveKit camera/media-projection declarations and screen-capture service; exclude debug tooling and cleartext transport; assign a unique version; sign with a dedicated local review identity; inspect the final binary rather than relying on source manifests.

## Acceptance Criteria

The review APK is non-debuggable, rejects cleartext, contains no camera/media-projection/screen-capture/debug-preview surface, has a unique version and stable non-debug signer, passes repository checks, and is copied to the operator share. Leave final status for an operator Play Protect scan and TalkBack check.

