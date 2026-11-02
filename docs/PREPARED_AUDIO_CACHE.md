# Prepared AAC/fast-start server cache - IVOX 3.0

Update: the server now selects partial, byte-exact AAC/MP4 preparation rather than full-song encoding. See PARTIAL_AUDIO_CACHE.md for the current path; the complete-file implementation and measurements below are retained as the previous architecture.

## Release decision

On 2026-11-02, after authorizing the prepared-cache build for real-device testing, the owner confirmed significantly faster playback and disappearance of media error 4 on Safari on their real iPhone. They explicitly approved the IVOX 3.0 release and announcement. This is user-reported hardware validation; no new numerical iPhone timing trace was supplied, so the release does not claim a universal measured latency.

Playwright documents that available media codecs differ by operating system and recommends WebKit on macOS for the closest Safari media-playback experience: https://playwright.dev/docs/browsers . A Windows WebKit failure does not establish failure on the user's iPhone; it also cannot establish success there.

## Implemented behavior

- Safari/iOS search and explicit prefetch requests schedule the first three eligible YouTube songs in the background. Speculation is limited to one worker, one FFmpeg encoder and eight queued songs. Searches and explicit Play requests promote their candidates. Visibility warming preserves pending jobs instead of discarding them; a running job completes or times out.
- Resolve the YouTube source, download the complete audio to an ephemeral local file, then invoke FFmpeg with explicit -c:a aac -profile:a aac_low -b:a 128k -movflags +faststart. Only the audio track is retained. There is no real-time remux or pipe-based playback in the server route.
- A real MP4 box parser verifies that ftyp exists and moov precedes the first nonempty mdat. The complete output is atomically published only after the encoder exits successfully and validation passes. Partial/truncated inputs and failed conversions never become cache entries.
- The MP4 is served from the prepared-file cache before querying the catalogue, resolving upstream URLs or opening a network stream. HTTP 200/206/416, bounded/open-ended/suffix ranges, exact Content-Length and Content-Range, and Accept-Ranges: bytes are supported. The Content-Type is audio/mp4.
- Readers lease their file, so expiry/LRU eviction cannot remove it mid-playback. Cache refresh invalidates the prepared representation; a missing file becomes a cache miss rather than preventing future preparation.
- If preparation is incomplete, missing or failed, Play uses the existing proxy immediately. It does not synchronously encode or wait for a preparation job.
- Cache defaults: 24 files, 256 MiB of completed files, 30-minute TTL, 32 MiB maximum input/published file, known duration at most 30 minutes, 40-second job budget including a maximum 20-second encoder wait. Failure retry suppression is 2 seconds for a busy resolver, 10 seconds for transient transport/timeouts and 60 seconds for other failures. FFmpeg has an additional working-output size cap; files exceeding the publication size limit are discarded. Working files consume additional temporary space and are removed after each job.
- Files and signed URLs are not written to a shared database. Prepared bytes are local to one process/replica and disappear on cleanup/restart. Other replicas can miss the cache and use the proxy. Expired idle entries are pruned on access; the maximum disk budget still bounds idle cache size.
- When Resource Timing exposes Server-Timing, the mobile debug panel reports prepared_cache_hit: 1 for a prepared response. Server metrics report audio_prepared success/preparation time and source prepared-aac. No signed URLs are logged.

## Validation

python -m unittest discover -s tests -p 'test_*.py'

69 tests passed, including eight new prepared-cache cases: atomic publication, byte ranges/416, active-reader leases, expiry/LRU, input limits/truncation, invalid atom layout, queue bounds/deduplication, encoder timeout cleanup. An API integration test confirms a prepared hit skips the catalogue query, resolver and upstream stream. The final catalogue-skip change was additionally verified with the five iOS proxy tests.

python tests/prepared_audio_probe.py
python tests/prepared_audio_probe.py --webkit

The integration probe performs a real complete download, FFmpeg re-encode, box validation, AAC-LC codec inspection, complete decode and HTTP range playback through Handler.relay. Three play attempts reuse one download and one resolution. The fixture has 20 seconds of synthetic mono audio and a 150 ms simulated upstream response delay; it is not a production song or mobile network test.

- Preparation including download/encode: approximately 1.20-1.23 seconds. Longer songs, cold resolution or constrained production resources can take substantially longer; readiness within 1-2 seconds is not guaranteed.
- Actual output codec/profile: AAC / LC. The encoder target is 128 kbps; the observed simple-tone file averaged approximately 106.6 kbps, so the target is not misrepresented as an exact measured bitrate.
- Chromium with iPhone 13 emulation: loadedmetadata 17, 19, 17 ms; playing 79, 24, 739 ms. This does not prove a universal playing-latency target.
- Windows WebKit with iPhone 13 emulation, final run: media error 4 at 212, 143 and 178 ms; no loadedmetadata. Safari release gate remains false.
- Syntax/compilation and git diff whitespace checks pass.

The server does not import or enable the former real-time-remux experiment. This release uses the prepared-file cache. The release UI now announces IVOX 3.0 following the owner's successful real-iPhone validation.


## IVOX 3.0 hardware confirmation

The owner confirmed successful Safari playback on the real iPhone with the prepared AAC-LC/fast-start implementation and reported that the Error 4 disappeared. Earlier Windows WebKit failures above remain historical test results and did not reproduce on that device. Manifest, visible release badge, update notice and release dialog now identify IVOX 3.0; the content-derived build ID remains independent so future updates are still detected.


## Queue stability and Play priority follow-up

A production QA check after 3.0 confirmed two tracks still used the InnerTube passthrough after a five-second wait (first 8 KiB 246-367 ms). These are network measurements, not iPhone playing times. Inspection found visibility prefetches replacing the entire pending preparation queue. The fix preserves bounded pending work, prioritizes the actual clicked track even if it was prefetched recently, preserves client track order in the prefetch API and warms iOS tracks even when stale browser-cache IDs exist. The queue is capped at eight pending songs with one active encoder. The authenticated resolve response now includes sanitized preparation status/stage/reason; the debug panel reads it only while visible. No signed URLs or filesystem paths are exposed. Cache misses, cold starts and incomplete full-file encoding can still take seconds.
