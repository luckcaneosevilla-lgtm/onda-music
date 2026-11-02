# Partial AAC/MP4 preparation and continuous playback

The production candidate prepares metadata and a bounded prefix instead of downloading/re-encoding the entire song. It intentionally does not concatenate an independently encoded 30-second MP4 with the original: those are different representations and cannot be joined transparently as ordinary progressive MP4 bytes.

The selected source is already AAC-LC in MP4. Keeping its samples avoids FFmpeg work entirely and preserves audio quality. The previous full AAC conversion remains an optional implementation/test path; the server now explicitly selects partial mode.

## Representation

- Download at most 512 KiB of the beginning (roughly 30 seconds at 128 kbps), plus bounded metadata/header ranges. Metadata is limited to 1 MiB and top-level traversal to 64 boxes; already front-loaded fragmented files are kept byte-exact without scanning every fragment.
- If moov follows mdat, obtain it through an upstream Range request, move it after ftyp and update every stco/co64 chunk offset. Reject unsupported offset-bearing/fragment relocation layouts rather than corrupting them.
- Cache the resulting prefix and metadata. The representation retains the original total size, duration, sample timestamps and audio packets. No truncated-file duration or second audio element is introduced.
- Every output byte maps to either cached metadata/prefix or an exact original-source range. Full responses, two-byte Safari probes, suffix ranges, cross-prefix requests and seeking all refer to this same stable representation.
- Validate upstream 206 status, Content-Range bounds/total, MIME, ETag and Last-Modified when present. Continuation failures close the response, invalidate its prepared entry and use the player's normal recovery/fallback; JSON/error headers must not be appended inside an audio body.
- One preparation worker, eight queued candidates with explicit Play priority, 24 entries and a 32 MiB memory budget. No temporary complete file or FFmpeg process is needed for partial mode. A cold job has an eight-second preparation budget and signed URL expiry bounds its cache lifetime.
- Cache misses keep the existing same-origin proxy. No fixed delay is inserted before play(). Initial preparation and cold/network fallback are not guaranteed below 200 ms or ready in 99% of cases.

## Diagnostics

Network logs expose audio_path as prepared-partial, prepared-full, proxy-fallback or unreported when Server-Timing is unavailable. The server response includes source prepared-partial and timing values partial_prepare_ms, partial_metadata_ms and partial_fetch_ms. Preparation status exposes mode partial, prefix_bytes and total_bytes without URLs or paths. These are metadata/byte preparation measurements, not AAC conversion times: no AAC re-encode occurs.

The mobile panel shows the same fields while open. prepared_cache_hit remains available. TTFB measured from HTTP request dispatch excludes TCP/TLS setup; a separate connect_ms is reported by the production probe to avoid conflating them.

## Validation

- Seven new unit tests cover stco/co64 relocation, overflow rejection, front-loaded/fragmented byte preservation, range crossing, exact media-sample addressing, changed validators, ignored Range requests and no FFmpeg process in partial mode.
- Full Python suite: 77 tests pass. Frontend priority checks and JS syntax checks pass.
- Real AAC-LC fixture with tail metadata: preparation fetched bytes 0-524287 and 640603-648280, rather than the entire source. With 80 ms simulated origin latency per request, total preparation was approximately 180 ms; metadata patching was 0.06 ms; cached first 8 KiB was 0.011 ms.
- FFmpeg decoded the entire original and reconstructed files to identical PCM checksums. Random access matched the reconstructed representation exactly.
- Chromium/iPhone emulation: loadedmetadata 15-19 ms, playing 24-79 ms. These are local synthetic results, not iPhone hardware or production latency guarantees.

Reproduce with python tests/partial_audio_probe.py and python -m unittest discover -s tests -p 'test_audio_partial.py'. The integration probe uses test-only imageio_ffmpeg/Playwright; runtime partial preparation uses only the standard library and the existing validated upstream client.
