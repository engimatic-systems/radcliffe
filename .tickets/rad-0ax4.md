---
id: rad-0ax4
status: closed
deps: []
links: [rad-c2tv]
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


## Notes

**2026-07-23T15:08:27Z**

Implemented on 19fb46a. Full make check passed: 376 Python passed/1 skipped, 107 sidecar tests, Android unit/lint, TypeScript, Ruff, formatting, and mypy. Review-variant lint and compilation also pass. Final APK: /Users/ravd/agent-share/radcliffe/radcliffe-0.6.1-v11-19fb46a-review.apk; SHA-256 f5c1df35e5a5065cb911e74b77c2ecef224f8c71f88c48228ca20def7dcb7061. APK signature verifies with certificate SHA-256 63613d6e48c5ba07f9756015659b9969922761b9457dad339928ff7e445c0017. Final binary is target SDK 36/versionCode 11, non-debuggable, has no cleartext opt-in, camera permission, media-projection permission, screen-capture service, or Compose PreviewActivity. Root/elevated-privilege signature string scan matched zero entries. Keep in_progress for operator Play Protect scan and the linked physical TalkBack acceptance.

**2026-07-27T04:13:17Z**

PR #60 is merged, and the operator confirmed personal verification of the merged chain including the Android review and TalkBack path. The hardened non-debug review artifact is accepted; closing.
