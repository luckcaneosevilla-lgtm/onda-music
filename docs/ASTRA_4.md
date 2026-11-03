# Ivox-Astra 4.0 — audio startup analysis and implementation

## What caused avoidable waiting

1. Preparing 512 KiB before publishing the cache delayed readiness in proportion to upstream bandwidth. Fast metadata alone did not remove this download cost.
2. The preparation worker served speculative results serially. Promoting a clicked track could not bypass an unrelated job already running.
3. The native route checked the prepared cache independently for every HTTP request. Safari's two-byte probe could see original MP4 offsets, then subsequent ranges could see relocated offsets after a background publication. Each response was individually valid, but together they described different files.
4. Router warming, preparation and Play could concurrently resolve the same song, duplicating upstream player requests.
5. A refresh parameter could invalidate caches again on every native range request, discarding useful work during playback.

## Options considered

| Approach | Benefit | Limitation | Decision |
|---|---|---|---|
| Encode a complete AAC file | Compatible, seekable output | Full download and encode before readiness; CPU and disk overhead | Keep existing optional path, avoid for startup |
| Live fragmented FFmpeg output | Can emit early MP4 initialization | Tail metadata still needs seekable input; new representation has different length and seek behavior | Leave experimental code unused |
| Browser prefix Blob / source switch | Local first bytes | Safari decoding and continuity can break; duplicate work and source changes | Keep native media path |
| Relocate MP4 metadata and cache a small original prefix | No encoding; stable length and samples; native ranges | Must validate continuation and pin offsets for an attempt | Implement with 64 KiB initial fetch |

## 4.0 changes

- Production preparation fetches 64 KiB initially, rather than 512 KiB. Tail metadata is fetched separately and chunk offsets are patched. Unsupported layouts fail into the existing original route. No encoder or complete-song download is introduced.
- Explicit Play has one reserved preparation slot in addition to the background worker. It bypasses unrelated queued work and joins an already running preparation for the same ID. Waiting is bounded at two seconds; background work has an eight-second preparation budget. Cold resolution/provider failures can still exceed fast-start targets via fallback.
- Every native Play receives a random attempt ID. The server records its first route decision per account/song/attempt. A prepared view remains pinned even if the cache is invalidated or evicted; a fallback decision does not switch to relocated bytes when preparation later finishes. Native retries use a new attempt ID.
- Pinned views are bounded by 32 MiB and 2,048 attempt records, with expiry bounded by cache/source lifetime. Admission fails explicitly at the record limit rather than evicting a live attempt and changing its offsets. The ordinary prepared cache has its independent 32 MiB budget.
- Cache refresh runs only once when an attempt is created. Subsequent Range requests reuse its view.
- InnerTube requests for the same song are coalesced across warming, Play and preparation, with bounded waiting and at most eight concurrent distinct resolution owners.
- AAC samples, duration, total representation size, suffix ranges, two-byte probes and seeking are preserved. Changed upstream validators or truncated continuation fail explicitly instead of appending an error body to audio.
- Manifest, launch screen, title, account release notice, update notice and visible branding identify Ivox-Astra 4.0. Service-worker shell version v27 refreshes cached assets while retaining account/library data.

## Verification on 2026-11-03

The AAC integration fixture is 40 seconds long, with `moov` after media data and 80 ms simulated upstream latency per range. With the production 64 KiB prefix it fetched 73,214 bytes before publication, versus 531,966 with the previous 512 KiB prefix: 86.2% fewer preparation bytes for this fixture. Metadata size varies by track.

The first integrated cold attempt reached `loadedmetadata` at 183 ms and `playing` at 253 ms; two warm attempts reached `playing` at 36 and 32 ms. All three successfully sought to and played past 35 seconds, beyond the cached prefix. Complete FFmpeg decode checksums matched the original; reconstructed random-access ranges matched exactly. These are Chromium/iPhone-emulation results on a synthetic local source, not real Safari or production latency guarantees.

Regression coverage includes cold-wait bounds, reserved Play admission, joining preparation, immutable route choices across publication/invalidation, one refresh per attempt, and shared player resolution. Existing authentication, isolation, cache, resolver, routing and streaming tests are also run.

Final Python run: **85 tests passed**. All **15 full-player interaction cases passed**, including first tap, repeated taps while loading, sheet controls, asynchronous sources, legacy device cache, duration correction, recovery, reconnect, pause during a stall and absence of a competing second download. The interaction fixture now answers the separate resolve request with JSON instead of incorrectly returning WAV bytes for it.

The release smoke check verifies the actual HTML, version endpoint, manifest, release dialog, layouts at 320/390/768/1440 px, service-worker v27, offline shell and absence of browser script errors. Screenshots are in `test-results/astra-4-mobile-*.png`.

Commands:

```powershell
python -m unittest discover -s tests -v
python tests/partial_audio_probe.py --astra
python tests/astra_release_check.py
python tests/play_intent_check.py
node tests/prefetch_priority_check.cjs
node tests/service_worker_audio_check.cjs
```

Real iPhone timing and playback over the production network must be distinguished from these automated checks. A release number does not establish universal latency or provider availability.

## Deployment and actual production media

Published application commit `4cef735` to `origin/main`. The public `/version.json` endpoint subsequently returned `release: 4.0`, build `9316d1a7af`, matching the hash of the committed application files.

`python tests/astra_production_check.py` exercised the deployed app using real catalogue audio, an isolated QA account and Chromium with the native iPhone user agent. The fourth search result reached `playing` at **206 ms**; the first result reached it at **620 ms**. Both played beyond six seconds and successfully sought to and played beyond 35 seconds. Observed audio responses were HTTP 206, `audio/mp4`, source `prepared-partial`, with no browser script errors. These are two individual production runs, not percentile measurements or physical iPhone timings; the shared server cache was not flushed, so the fourth-result case is not proof of a globally cold origin.

The sanitized result is stored locally in `test-results/astra-production.json`. Credentials and signed audio URLs are not included. The production-check script creates a dedicated QA account on each invocation and should not be used as a load test.
