# Changelog

## 1.0.3

- Added a /chunkize config command that opens an in game pop up to pick a speed mode: light (~10 chunks/s), medium (~50 chunks/s) or intense (~150 chunks/s), medium is used by default until you choose. From the console use /chunkize config <light|medium|intense>
- Much faster generation, measured several times more chunks per second than before on an idle server, governed by the chosen mode
- Batch completion is now verified instead of timed, a batch is released the moment its chunks have actually generated and is held (up to settleSeconds) if any are still finishing, so it is both faster and free of the patchy or missing chunks a blind timer could leave behind
- Generation speed adapts to server load, it runs more batches in parallel while the server stays under targetMspt milliseconds per tick and sheds them when load climbs, so it goes fast on an idle server without dragging down tick rate when players are on
- Fixed the server running out of memory and crashing on long runs, the world save is now flushed to disk on a cycle (save hold / save query / save resume) so unsaved chunks cannot pile up in RAM without bound
- Hitting the world ticking area limit now backs off and keeps generating with what is available instead of failing repeatedly, and it falls back gracefully on older Endstone builds that do not report tick timing

## 1.0.2

- Fixed regions only partially saving to disk, ticking areas were being removed as soon as chunks reported loaded, which could cut off generation before it finished
- Batches now hold their ticking area for a settle period after all chunks have loaded, configurable with settleSeconds
- Every chunk is stamped with a touch-and-restore block write before its batch completes, marking it dirty so the server is guaranteed to persist it, configurable with stampChunks
- Removed the shortcut that skipped batches whose chunks appeared loaded, neighbouring areas report partially generated border chunks as loaded so the shortcut could skip unfinished terrain

## 1.0.1

- Fixed intermittent "ticking area already exists" errors caused by reusing an area name in the same tick it was removed
- Ticking areas are no longer removed during chunk load events, removal now happens on the scheduler cycle
- Batches that are already fully loaded complete without creating a ticking area at all
- Timed out batches go through a retry queue instead of an instant remove and re-add
- Command feedback is captured instead of spamming the console, no more "Failed to execute tickingarea as Null" log noise
- Failed area creation now cleans up after itself before retrying

## 1.0.0

- Initial release
- Pregenerate chunks in any dimension with /chunkize start, driven by ticking areas
- Square and circle shapes with a spiral generation order from the center
- Live progress, speed and ETA through /chunkize status
- Pause, resume and cancel at any time
- Progress is saved to disk and survives server restarts, with optional auto resume
- Configurable batch size, concurrency, timeouts and radius cap in config.toml
