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

## Safari proxy fix (2026-11-02)

- Safari/macOS, iPhone/iPad/iPod and identifiable iOS browsers receive only a same-origin proxy URL from the resolve endpoint. Safari's resolve endpoint does not invoke an extractor. The frontend also forces the proxy without an awaited resolve request at Play, and uses it for speculative prefix fetches.
- Existing 8 KiB `read1` + flush relay was already progressive. Replaced the 12-second worker queue and 20-second per-video lock wait with 200 ms admission waits. Extraction defaults to 6 seconds (`IVOX_EXTRACT_TIMEOUT`, clamped to 1-10 seconds), extraction network retries are disabled, upstream header acquisition uses a shared 3-second redirect budget, and the audio router no longer repeats extraction within one failed proxy request. Busy admission is a failure rather than a claim that playback succeeded; slow extraction may now fail sooner.
- Session validation stays at route entry and is rechecked once per second during relay instead of once per 8 KiB. It can still block on a session refresh/database lookup; it no longer adds a check before the first chunk.
- Prefix Blob MIME is taken from the actual audio response, trimmed and normalized to lowercase. MP4/AAC responses remain `audio/mp4`; other formats are not relabeled as AAC. No `decodeAudioData` predecode is added: the API requires complete files, and its decoded buffer would not be reused by the existing HTML audio element. See https://developer.mozilla.org/en-US/docs/Web/API/BaseAudioContext/decodeAudioData . The elapsed interval from metadata to playing alone does not isolate decoder time.
- Local HTTP regression: the actual Handler relay delivered its first 8 KiB before allowing the mocked upstream to finish, in 2-17 ms across runs. This proves incremental forwarding with an already resolved upstream; it does not establish a 100 ms cold Google extraction or Northflank/iPhone TTFB.
- 4 Safari/proxy tests (including the actual resolve handler bypass), 18 resolver tests, 8 router tests and 9 prefix-streaming tests pass. The worker byte-range checks and Pixel 7 Chromium continuity fixture also pass (one source load, no waits after playing; 1330 ms initial startup in this run). Real iPhone/production measurements remain necessary after deployment.
