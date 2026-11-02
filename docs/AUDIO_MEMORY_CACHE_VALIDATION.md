# Continuous audio and mobile diagnostics validation

2026-11-02

The player now gives its single audio element one stable URL. A controlled service worker responds with the already downloaded 500 KiB prefix followed by the remaining bytes from the same upstream representation. There is no Blob-to-network src switch, second audio element or volume crossfade. Native byte-range requests, including Safari's initial two-byte probe and suffix requests, are supported. Continuation status, content range, total length and MIME must match before bytes are appended. Changed or rejected responses fail into the player's normal proxy retry instead of mixing representations.

Without a ready controlling worker, usable Content-Range metadata or a valid prefix, playback uses the normal direct/proxy stream. The worker can be stopped by the browser: a subsequent request then falls back to the regular stream under the same URL. Prefix memory is bounded and scoped to each controlled client. Complete-song persistent caching remains separate from temporary prefixes.

The CSS Horus splash appears in the initial HTML, fades after deferred JavaScript is ready, respects reduced motion and has a maximum visibility failsafe. It does not claim to calibrate or accelerate the physical audio device.

Touch/click the IVOX logo five times within 1.8 seconds between taps to open local audio diagnostics. The latest 150 events are retained only in page memory. Copy, clear and close controls are available. Logs show play intent, source assignment, loading, decoding, playing, first progress and error/stall events. The panel excludes URLs and credentials. `playing` is a browser event, not a physical speaker-latency measurement.

Validation:

- 18 resolver tests and 8 audio router tests pass.
- `node tests/service_worker_audio_check.cjs`: complete-cache reuse without network; byte and suffix ranges; 416; exact prefix/remainder concatenation; cross-client isolation; two-byte Safari-style probe; rejection of an upstream that ignores Range.
- `python tests/mobile_continuous_audio_probe.py`: actual worker in Pixel 7 Chromium emulation, 20-second synthetic WAV and 300 ms continuation delay. Playback crossed the 11.6-second prefix boundary with one source load and zero post-start waiting events. Initial run: 2190 ms to playing; repeat: 1663 ms. This is a synthetic browser test, not production Google streaming or a real phone measurement.
- Five logo taps, panel metrics, URL exclusion, reduced-motion styling and splash removal pass in the mobile browser fixture; screenshots were inspected.
- `python tests/mobile_continuous_audio_probe.py --webkit`: the local WebKit runtime rejected the fixture with media error 4. This run does not validate iPhone playback or establish whether the issue is decoder/runtime or streamed-response support. The normal proxy retry remains available in the actual player.
- JavaScript syntax checks, Python compilation and git diff whitespace checks pass.

Release decision: the universal sub-500 ms cold-start goal is not achieved and iPhone remains unvalidated. Ship the normal content-hash app update, preserving the existing release label. Do not announce IVOX 3.0 until real-device measurements confirm the target and continuity. On iPhone, update/reload, touch the logo five times, play a top search result after precaching, and leave it playing beyond 15 seconds; copy logs from the panel for both the initial and repeat play.

Implementation references: https://web.dev/articles/sw-range-requests and https://developer.mozilla.org/en-US/docs/Web/API/Streams_API/Using_readable_streams
