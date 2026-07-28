# Eyes-free earcons

Radcliffe creates its short lifecycle cues on the Android device. It does not
download sound files, send cue audio through LiveKit, or add a cue to the
conversation timeline. `EarconPlayer` wraps the generated PCM in local 48 kHz
WAV samples and preloads them with Android `SoundPool`. It uses the same call
route as LiveKit without requesting audio focus, so it follows the audible
voice output while the remote speech track stays in control. Do not route cues
through `USAGE_ASSISTANCE_SONIFICATION`: Pixel devices can mute that system
stream independently of the voice session.

All cues are defined in `apps/android/app/src/main/java/works/ravd/radcliffe/Earcons.kt`.
That table is the intentional tuning surface: adjust the instrument, MIDI notes
(`C4 = 60`, `C5 = 72`), note onset, note duration, or normalized gain there.
The renderer makes PCM from that table at runtime, and validates that every cue
is under 500 ms.

| Lifecycle event | Voice | Default cue |
| --- | --- | --- |
| Session becomes ready to listen | Vibraphone | E5 |
| Session disconnects | Vibraphone | C3 |
| Endpointed user turn begins assistant thinking | Vibraphone | C4 → C5 |
| Background job starts | Vibraphone | C4 → E4 → A4 |
| Background job succeeds | Electric piano | C6/9: C4 E4 G4 D5 |
| `read_source` begins deterministic rendering | Electric piano | Dmaj7(add9): D3 A3 C#4 E4 |

The foreground service plays only a state transition, not repeated state
events. It plays a job cue only after the job model accepts a fresh lifecycle
event, so replayed or stale job envelopes stay silent.

The in-conversation diagnostics panel shows the installed build number and the
last local cue playback status. Its **Preview all cues** action plays the six
cues in sequence without creating conversation messages or server events.
