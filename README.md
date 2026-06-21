# compose-frames

Store screenshot frame compositor — composites raw app screenshots into device mockup frames for App Store / Play Store submissions.

## Requirements

```bash
pip install Pillow
```

## Usage

```bash
# Basic: composite all raw screenshots with all configured devices
python3 compose_frames.py --raw-dir /path/to/your/raw/screenshots

# Specify output directory
python3 compose_frames.py --raw-dir ./raw --out-dir ./output

# Use custom frame assets directory
python3 compose_frames.py --raw-dir ./raw --frame-dir /path/to/frames

# Specific device only
python3 compose_frames.py --raw-dir ./raw --device pixel8pro

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

- iOS: `ios_*.png` (e.g. `ios_01_list.png`, `ios_02_detail.png`)
- Android: `and_*.png` (e.g. `and_01_list.png`, `and_02_detail.png`)

## Output files

| Suffix | Content | Use case |
|---|---|---|
| `*_framed.png` | Frame + screenshot (transparent background) | Compositing / further editing |
| `*_final.png` | Frame + screenshot + background + shadow | **Store submission** |
| `*_rounded.png` | Rounded corners only (transparent) | `--frameless` |
| `*_noframe.png` | Rounded corners + background + shadow | `--frameless` |
| `*_raw.png` | Raw opaque copy | `--raw-copy` |

## Adding a new device

1. Create a subdirectory under `frames/` (e.g. `frames/pixel9pro/`)
2. Place frame image (RGBA PNG) and mask image (L-mode PNG) there
3. Add an entry to `frames/devices.json`:

```json
{
  "device_id": {
    "name": "Display Name",
    "platform": "ios | android",
    "frame_type": "rgba_transparent | rgba_with_mask",
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

- `rgba_transparent`: Screen area is alpha=0 in the frame PNG. No mask needed (e.g. Apple official bezels).
- `rgba_with_mask`: Separate mask.png defines the screen region (e.g. GitHub community frames for Pixel).

### High-quality frame sources

- **iOS (official):** https://developer.apple.com/design/resources/ (Bezel DMG)
- **Android (RGBA PNG):** https://github.com/jamesjingyi/mockup-device-frames
- **Android (with coordinates):** https://github.com/jonnyjackson26/device-frames-media (template.json + mask.png)

## Integration with Claude Code

To use as a global Claude Code skill, place a skill file at `~/.claude/commands/compose-frames.md` that calls this script with appropriate `--raw-dir` pointing to your project's raw screenshots directory.
