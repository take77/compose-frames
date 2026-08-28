# compose-frames

Store screenshot frame compositor — composites raw app screenshots into device mockup frames for App Store / Play Store submissions, and web screenshots into a laptop bezel for marketing images.

## Requirements

```bash
pip install Pillow
```

## Frame directory

Frame assets and `devices.json` are read from the first of these that is set:

| Priority | Source | Example |
|---|---|---|
| 1 | `--frame-dir` | `--frame-dir /path/to/frames` |
| 2 | `$COMPOSE_FRAMES_DIR` | `export COMPOSE_FRAMES_DIR=~/tools/compose-frames-assets/frames` |
| 3 | `<script dir>/frames` | the `frames/` directory in this repo |

The resolved directory is printed on every run, along with which of the three won.

### Shared frames folder

Apple's device bezels may not be redistributed, so they are **not** committed here (see [Frame sources](#frame-sources)). The practical setup is to keep one frames folder outside the repo, sync it via Google Drive, and point the env var at it:

```bash
# on each machine, after syncing the folder from Drive
export COMPOSE_FRAMES_DIR=/path/to/compose-frames-assets/frames
```

That folder holds the same layout as `frames/` here: `devices.json` plus one subdirectory per device. Keep its `devices.json` in sync with the repo's.

## Usage

```bash
# Basic: composite all raw screenshots with all configured devices
python3 compose_frames.py --raw-dir /path/to/your/raw/screenshots

# Specify output directory
python3 compose_frames.py --raw-dir ./raw --out-dir ./output

# Use a specific frame assets directory (overrides $COMPOSE_FRAMES_DIR)
python3 compose_frames.py --raw-dir ./raw --frame-dir /path/to/frames

# Specific device only
python3 compose_frames.py --raw-dir ./raw --device pixel8pro
python3 compose_frames.py --raw-dir ./raw --device macbookair13

# Specific color variant
python3 compose_frames.py --raw-dir ./raw --device pixel8pro --variant black

# Custom background color
python3 compose_frames.py --raw-dir ./raw --bg-color '#FFFFFF'

# Framed only (no background composites)
python3 compose_frames.py --raw-dir ./raw --no-background

# Also generate frameless (rounded corners only) variants
python3 compose_frames.py --raw-dir ./raw --frameless

# Also copy raw opaque screenshots to output
python3 compose_frames.py --raw-dir ./raw --raw-copy
```

## Raw screenshot naming convention

Each device picks up raw screenshots by the filename prefix of its platform:

| Platform | Prefix | Example |
|---|---|---|
| `ios` | `ios_` | `ios_01_list.png`, `ios_02_detail.png` |
| `android` | `and_` | `and_01_list.png`, `and_02_detail.png` |
| `web` | `web_` | `web_01_export.png`, `web_02_dashboard.png` |

Any other `platform` value is an error.

## Output files

| Suffix | Content | Use case |
|---|---|---|
| `*_framed.png` | Frame + screenshot (transparent background) | Compositing / further editing |
| `*_final.png` | Frame + screenshot + background + shadow | **Store submission** |
| `*_rounded.png` | Rounded corners only (transparent) | `--frameless` |
| `*_noframe.png` | Rounded corners + background + shadow | `--frameless` |
| `*_raw.png` | Raw opaque copy | `--raw-copy` |

## Adding a new device

1. Create a subdirectory in the frame directory (e.g. `frames/pixel9pro/`)
2. Place the frame image (RGBA PNG) and, if needed, a mask image (L-mode PNG) there
3. Add an entry to `devices.json`:

`devices.json` is validated before anything is rendered, and an invalid entry exits non-zero with a message. Frame and mask files, though, are only required for devices that actually have raw screenshots in the run — so a missing Apple bezel does not break an Android-only run.

```json
{
  "device_id": {
    "name": "Display Name",
    "platform": "ios | android | web",
    "frame_type": "rgba_transparent | rgba_with_mask",
    "fit": "width | cover",
    "variants": {
      "color_name": {
        "frame": "device_id/frame_color.png",
        "mask": "device_id/mask_color.png"
      }
    },
    "screen": {
      "x": 0, "y": 0,
      "width": 0, "height": 0
    },
    "store_target": {
      "width": 1080, "height": 1920
    }
  }
}
```

### frame_type

- `rgba_transparent`: The screen area is alpha=0 in the frame PNG. No mask needed (e.g. Apple official bezels). The screen shape is found by flood-filling the transparent hole from the centre of the image.
- `rgba_with_mask`: A separate mask PNG defines the screen region (e.g. GitHub community frames for Pixel). Every variant must name a `mask`, and it must exist and be the same size as its frame.

Any other value is an error. Note that `fit: cover` respects the mask on `rgba_with_mask` frames — without it the screenshot would spill past the mask's rounded corners, because the frame itself is transparent there.

### fit

Optional, defaults to `width`.

- `width`: Scale the screenshot to the screen width, keep its aspect ratio, and centre it vertically in the screen. This is the phone behaviour — a screenshot shorter than the screen leaves a gap above and below.
- `cover`: Scale by `max(screen_w / shot_w, screen_h / shot_h)` so the screenshot covers the whole screen rect, then crop it to that rect anchored top-left. Use this for laptops and any other frame whose screen must be filled edge to edge. A screenshot captured at the screen's own aspect ratio loses nothing.

### store_target

Optional. It is the canvas size of the `*_final.png` background composite. Omit the key entirely to use the frame's own size, which is what a marketing image (as opposed to a store submission) usually wants. If the key is present, `width` and `height` must both be positive integers — an empty or malformed `store_target` is an error rather than a silent fallback.

### screen

The bounding box of the screen area in frame-image pixels. For a frame with a notch, this is the bounding box of the **whole transparent hole including the notch band** — the notch itself is opaque in the PNG and gets painted back over the screenshot during compositing. Measuring the transparent run down the centre column instead would put the screen top below the notch.

### Adding a laptop

A laptop bezel is a `web` platform device with `"fit": "cover"` and no `store_target`, like the shipped `macbookair13`:

```json
"macbookair13": {
  "name": "MacBook Air M5 13-inch",
  "platform": "web",
  "frame_type": "rgba_transparent",
  "variants": {
    "midnight": { "frame": "macbookair13/macbookair13_midnight.png" }
  },
  "screen": { "x": 420, "y": 288, "width": 2560, "height": 1664 },
  "fit": "cover"
}
```

Capture the web screenshot at the screen's own aspect ratio so `cover` crops nothing. For `macbookair13` that screen is 2560x1664, which is **20:13** (≈ 1.538:1) — *not* 16:10 — so capture at a **1600x1040** viewport.

A 16:10 capture (1600x1000, say) is proportionally taller than the screen, so `cover` scales it to match the screen height and crops roughly 100px off the right edge. Name the file `web_*.png` and run with `--device macbookair13`.

## Frame sources

### Apple (iPhone, MacBook)

Download the bezels from <https://developer.apple.com/design/resources/> — the *Device Bezels* section — and mount the DMG:

- `Bezel-iPhone-16.dmg` → iPhone 16 Pro Max
- `Bezel-MacBook-Air-M5.dmg` → MacBook Air M5 13-inch

Rename the extracted PNG to the filename in the device's `variants` entry and put it in the device's subdirectory of your frame directory.

**Licence:** Apple's design resources may not be redistributed. The Apple bezels are therefore gitignored here (`frames/iphone16promax/*.png`, `frames/macbookair13/*.png`) and never committed to this public repo. Download them yourself, or take them from the shared frames folder described above.

### Android

- RGBA PNG frames: <https://github.com/jamesjingyi/mockup-device-frames>
- Frames with coordinates: <https://github.com/jonnyjackson26/device-frames-media> (template.json + mask.png)

These are freely licensed, so `frames/pixel8pro/` **is** committed here.

## Integration with Claude Code

To use as a global Claude Code skill, place a skill file at `~/.claude/skills/compose-frames/SKILL.md` that calls this script with `--raw-dir` pointing at the project's raw screenshots directory, and set `COMPOSE_FRAMES_DIR` so the frames resolve without a flag.
