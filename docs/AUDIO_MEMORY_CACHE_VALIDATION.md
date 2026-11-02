# Audio memory and persistent cache validation

2026-11-02

- Top results request a maximum 500 KiB prefix per song, with three concurrent speculative downloads and three resident Blob URLs.
- InnerTube resolve responses expose the signed upstream URL with a same-origin proxy fallback. CSP permits Google video subdomains; preconnect also targets the actual resolved origin.
- Partial Blob playback resumes on the stream before reaching the prefix boundary, preserving the current position. This source change can introduce a short interruption; it is not gapless MSE playback.
- The service worker stores a complete copy of played YouTube tracks under stable track IDs and serves byte ranges from those copies. Automatic fills stop at 40 entries; explicit downloads are not evicted. Browser quota/eviction and unsuccessful downloads can prevent next-day offline playback.
- A direct URL may be bound to the server IP or rejected by the browser/provider. Playback falls back to the proxy on error or a four-second startup timeout. Cross-origin prefix fetches also fall back on CORS failures.

Validation:

- 18 resolver tests and 8 audio router tests pass.
- `node tests/service_worker_audio_check.cjs`: complete-cache reuse without network, byte ranges, suffix ranges and HTTP 416 pass.
- `python tests/mobile_memory_cache_probe.py`: Chromium Pixel 7 emulation, synthetic WAV prefix, no network requests during Play. Five play-to-playing measurements: 1757, 485, 15, 14, 14 ms. The initial browser/audio-device startup misses the 500 ms target even with local bytes. This is an isolated decoding test, not a production network benchmark or a real handset test.
- JavaScript syntax checks, Python compilation and git diff whitespace checks pass.

Production Google streaming latency, mobile Safari and audible continuity during source handoff still require device validation. No universal sub-500 ms startup guarantee is established by these tests.
