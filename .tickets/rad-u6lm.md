---
id: rad-u6lm
status: closed
deps: []
links: [rad-0tmq]
created: 2026-07-27T15:46:22Z
type: feature
priority: 2
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, audio, accessibility, eyes-free, operator-final-check]
---
# Add subtle eyes-free audio cues for voice and tool state

Make the app operable without watching the screen by trying a small set of distinct, low-key earcons. Play cues when the session connects and begins listening, when endpointing finishes and the assistant begins thinking, when a background job starts, when a background job completes, and when read_source begins deterministic verbatim rendering. Generate the sounds in-repository rather than downloading third-party audio; each cue must be distinct and shorter than 500 ms. Keep the experiment easy to tune or remove, avoid masking speech, and do not let app-generated cues become user transcripts.

## Acceptance Criteria

The operator runs a Pixel test covering connected/listening, listening-to-thinking, background-job start, background-job completion, and read_source start; can distinguish every cue without looking at the phone; and likes the overall experience. Any cue the operator asks to change or remove is adjusted or removed before closure. All cues remain under 500 ms, use generated/local assets with no external licensing dependency, and do not disrupt speech, interruption, or transcription.


## Notes

**2026-07-27T18:27:33Z**

Implemented locally synthesized Android earcons: vibraphone E5 connected, C3 disconnect, C4→C5 thinking, C4→E4→A4 job start; electric-piano C6/9 job success and Dmaj7(add9) read_source start. Cue table is in Earcons.kt; sounds are local PCM, under 500 ms, never sent through LiveKit or appended as conversation messages. Verification: focused EarconsTest plus make check-android (unit suite and lint) pass. Debug APK copied to /Users/ravd/agent-share/radcliffe/radcliffe-rad-u6lm-earcons-debug.apk (SHA-256 82166557222b1c0a441ab4451d59568a90ca2033202fd507d861aaf6c1e64916). Pending operator Pixel audition with the pinned claude-sonnet-5 foreground and background profiles; adb is unavailable on this host.

**2026-07-27T18:37:45Z**

Install diagnosis: the shared earcons APK is signed with certificate SHA-256 4ac1d019…a5b42e6, while the existing v0.6.1 review APK is signed with 63613d6e…5c0017. Android rejects an in-place update across those certificates. The review PKCS#12 container exists at the configured local signing location, but the four review-signing environment variables are not loaded in this session, so a compatible review rebuild requires approved signing credentials. Alternative: uninstall the existing Radcliffe app before installing the debug APK; that resets its local enrollment/profile.

**2026-07-28T15:12:23Z**

Built an installable fresh-install review artifact after operator authorized uninstall/reinstall. The APK is the non-debuggable review variant (works.ravd.radcliffe 0.6.1, versionCode 11), locally test-signed because production review credentials remain unavailable. Verified zip alignment, APK Signature Scheme v3, debuggable=false, expected microphone permissions/camera removal, and inclusion of EarconPlayer/EarconRenderer/EarconCues. Shared path: /Users/ravd/agent-share/radcliffe/radcliffe-0.6.1-v11-rad-u6lm-review-test.apk. SHA-256: 783c3d48f9f780e2ac72b4ca99f068c39dae3bf68cdb218a1d90695694007cf3. The existing production-review-signed app must be uninstalled before installing this test-signed review APK; uninstall clears local app profile/enrollment.

**2026-07-28T18:20:28Z**

Operator test found every cue inaudible. Diagnosis confirmed the installed APK contained the feature and the active session delivered listening/thinking plus job started/succeeded events; no server restart was needed. Root cause: EarconPlayer used Android usage 13 (USAGE_ASSISTANCE_SONIFICATION), a Pixel system stream muted independently of LiveKit's call audio. Changed cues to LiveKit's voice-communication route (usage 2/content speech), added explicit AudioTrack creation/write/playback diagnostics and partial-write handling, and added route coverage. Full Android unit/lint checks pass. Replacement non-debuggable review APK: /Users/ravd/agent-share/radcliffe/radcliffe-0.6.1-v11-rad-u6lm-earcon-route-fix-review-test.apk; SHA-256 bb8feb51b9e598395a67cc2a71b03b15b1150fd604eec9dc94c6f7b882f42c43; same test signer as prior APK, v3 signature, zip aligned, HTTP range verified. Pending operator re-audition.

**2026-07-28T18:33:24Z**

Second Pixel audition remained silent after routing AudioTrack to the LiveKit call stream. Server correlation for the 11:25 PDT test proved the phone connected and fresh job STARTED/SUCCEEDED events were emitted while connected. Replaced the low-level 24 kHz static AudioTrack path with preloaded 48 kHz generated WAV samples through SoundPool on the LiveKit call route. Added local-only diagnostics showing build number and last playback status plus a Preview all cues action that bypasses server/event gates. Bumped versionCode to 12. Full Android unit/lint and review vital lint pass. Replacement: /Users/ravd/agent-share/radcliffe/radcliffe-0.6.1-v12-rad-u6lm-soundpool-review-test.apk; SHA-256 e916b5d19e3b8b1817d2a1a8930d9a5e2123d8cbfe2fbca1fef99ba30495020b; non-debuggable, aligned, v3 signed by the same test signer, packaged SoundPool path verified, HTTP Range verified. Debug APK also copied per AGENTS.md: /Users/ravd/agent-share/radcliffe/radcliffe-0.6.1-v12-rad-u6lm-soundpool-debug.apk; SHA-256 c4d422a72884f8a0847a91bc05a94ef826822f4677099a6d2335f64c06326e3f. Pending decisive local preview and lifecycle audition.

**2026-07-28T21:39:13Z**

After the 2026-07-28 Pixel source-read trace showed ambiguous promoted sources plus queued-playback timeout, the operator chose a prompt-only tool-first experiment before adding deterministic gating. Rebuilt the agent sidecar with the operator-authored system-prompt edit and restarted only works.ravd.radcliffe.sidecar and works.ravd.radcliffe.control; both health endpoints are green and both foreground/background profiles remain pinned to claude-sonnet-5. The media worker remained running. No tool-first enforcement code was added. The separate bounded-flow-control and formal ownership work is tracked by linked epic rad-0tmq and its children.

**2026-07-28T21:54:42Z**

Physical Pixel acceptance passed on versionCode 12: the operator heard all lifecycle cues, found them distinct and reasonably voiced, and accepted the palette without further tuning. In the prompt-only source-read experiment, the first request still produced a cascade of spoken model messages, while the second request correctly called the source-read tool first and rendered the source deterministically. The operator accepted prompt-only tool-first behavior for now and explicitly declined an additional deterministic enforcement layer. The residual stacked-playback/cascade risk remains out of scope for this ticket and is tracked by linked epic rad-0tmq.
