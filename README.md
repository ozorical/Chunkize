<div align="center">
  <img src="chunkize.jpg" alt="Chunkize" width="640">
</div>

Chunk pregeneration for [Endstone](https://github.com/EndstoneMC/endstone) Bedrock servers, made by **ozz**.

Generate your world ahead of time so players never hit chunk generation lag. Works like [Chunky](https://modrinth.com/plugin/chunky) does for Java edition, but built for Bedrock Dedicated Server.

## How it works

Bedrock Dedicated Server has no API to load or generate chunks directly. Chunkize works around that by driving the vanilla `/tickingarea` command. The target region is split into chunk-aligned batches, and each batch gets a temporary ticking area which forces the server to generate and save those chunks. Chunkize listens for chunk load events to know exactly when a batch is done, removes the ticking area and moves on to the next one, spiraling outward from the center until the whole region is generated.

Progress is saved to disk, so a server restart or crash picks up right where it left off.

## Installation

1. Download the latest `.whl` from the releases page.
2. Drop it into the `plugins` folder of your Endstone server.
3. Restart the server.

Or build it yourself:

```bash
pip install build
python -m build
```

The wheel lands in `dist/`.

## Commands

| Command | Description |
| --- | --- |
| `/chunkize start <radius> [dimension] [centerX] [centerZ] [shape]` | Start pregenerating. Radius is in blocks. Run as a player and it defaults to your position and dimension, from console it defaults to overworld around 0, 0. Shape is `square` (default) or `circle`. |
| `/chunkize pause` | Pause the current task. |
| `/chunkize resume` | Resume a paused task, even after a restart. |
| `/chunkize cancel` | Cancel the task and wipe saved progress. |
| `/chunkize status` | Show progress, speed and ETA. |

Examples:

```
/chunkize start 5000
/chunkize start 3000 nether
/chunkize start 10000 overworld 0 0 circle
```

![Chunkize running in game](demo.png)

## Configuration

`plugins/chunkize/config.toml` is created on first run.

| Key | Default | Description |
| --- | --- | --- |
| `cellChunks` | `8` | Side length of each batch in chunks. Maximum 10, since a ticking area caps out at 100 chunks. |
| `maxActiveAreas` | `4` | Ticking areas used at once. Bedrock allows 10 per world, leave headroom if your server uses its own. |
| `checkIntervalTicks` | `10` | How often the generator checks progress and rotates batches. |
| `cellTimeoutSeconds` | `60` | How long to wait on a batch before retrying it, then skipping it. |
| `maxRadius` | `50000` | Safety cap for the radius argument. |
| `autoResume` | `true` | Continue an interrupted task automatically after a restart. |
| `logIntervalSeconds` | `30` | Progress log interval in the console, 0 to disable. |
| `saveIntervalSeconds` | `30` | How often progress is written to disk. |

## Permissions

| Permission | Default | Description |
| --- | --- | --- |
| `chunkize.command` | op | Access to `/chunkize`. |

## Good to know

- Generation runs while players are online. Lower `maxActiveAreas` to 1 or 2 if you want it gentler on the server, raise it on strong hardware.
- The world keeps its ticking area budget while Chunkize runs. If your server already uses several ticking areas, lower `maxActiveAreas` so the total stays under 10.
- Already generated regions are processed much faster than fresh terrain, since loading is cheaper than generating.
- Nether radius is in nether blocks. A 1000 block nether radius covers the same map area as 8000 overworld blocks.

## License

MIT, see [LICENSE](LICENSE).
