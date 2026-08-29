#!/usr/bin/env python3
"""
Store screenshot frame compositor.

Composites raw app screenshots into device mockup frames for
App Store / Play Store submissions.

Usage:
  python3 compose_frames.py --raw-dir ./raw --out-dir ./framed
  python3 compose_frames.py --raw-dir ./raw --device pixel8pro
  python3 compose_frames.py --raw-dir ./raw --variant black
  python3 compose_frames.py --raw-dir ./raw --no-background
  python3 compose_frames.py --raw-dir ./raw --bg-color '#FFFFFF'

Frame assets and devices.json are read from the first of these that is set:
--frame-dir, $COMPOSE_FRAMES_DIR, <script dir>/frames. To add a new device,
add its entry to devices.json and put its frame assets in a subdirectory of
that same frame directory.
"""

from PIL import Image, ImageDraw, ImageFilter
from collections import deque
import argparse
import json
import math
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FALLBACK_FRAME_DIR = os.path.join(SCRIPT_DIR, 'frames')
FRAME_DIR_ENV_VAR = 'COMPOSE_FRAMES_DIR'
DEFAULT_BG_COLOR = (244, 243, 238)  # #F4F3EE

# Raw screenshots are picked up by filename prefix, one prefix per platform.
RAW_PREFIX_BY_PLATFORM = {'ios': 'ios', 'android': 'and', 'web': 'web'}

DEFAULT_FIT = 'width'
SUPPORTED_FITS = ('width', 'cover')

DEFAULT_FRAME_TYPE = 'rgba_transparent'
SUPPORTED_FRAME_TYPES = ('rgba_transparent', 'rgba_with_mask')


class ConfigError(Exception):
    """A problem in devices.json, or in the frame assets one of its devices points at."""


def parse_hex_color(hex_str):
    hex_str = hex_str.lstrip('#')
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))


def resolve_frame_dir(cli_frame_dir):
    """Pick the frame directory: --frame-dir wins, then $COMPOSE_FRAMES_DIR, then <script dir>/frames.

    Returns the absolute path and a short label naming which of the three won.
    """
    if cli_frame_dir:
        return os.path.abspath(os.path.expanduser(cli_frame_dir)), '--frame-dir'
    env_frame_dir = os.environ.get(FRAME_DIR_ENV_VAR)
    if env_frame_dir:
        return os.path.abspath(os.path.expanduser(env_frame_dir)), f'${FRAME_DIR_ENV_VAR}'
    return FALLBACK_FRAME_DIR, 'script default'


def load_devices(frame_dir, frame_dir_source):
    """Read devices.json from the frame directory, as an object of device entries.

    A missing, unparsable or wrongly shaped file is a config problem, not a traceback, and
    the message names where the frame directory came from since that is the usual mistake.
    """
    devices_json = os.path.join(frame_dir, 'devices.json')
    try:
        with open(devices_json) as f:
            devices = json.load(f)
    except FileNotFoundError as exc:
        raise ConfigError(f'devices.json not found: {devices_json} '
                          f'(frame directory from {frame_dir_source})') from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f'devices.json is not valid JSON ({exc}): {devices_json} '
                          f'(frame directory from {frame_dir_source})') from exc
    if not isinstance(devices, dict):
        raise ConfigError(f'devices.json must hold an object of devices, '
                          f'got {type(devices).__name__}: {devices_json}')
    return devices


def validate_devices(devices):
    """Reject a devices.json the run cannot trust, before any image is opened.

    Only the checks that need nothing but the JSON live here. Whether the frame and mask
    files are actually on disk is checked per device in check_variant_assets, so a device
    nobody is rendering never has to have its assets on this machine.
    """
    for device_id, device_cfg in devices.items():
        where = f'devices.json: {device_id}'
        if not isinstance(device_cfg, dict):
            raise ConfigError(f'{where}: device entry must be an object, got {device_cfg!r}')

        platform = device_cfg.get('platform')
        if platform not in RAW_PREFIX_BY_PLATFORM:
            raise ConfigError(f'{where}: unknown platform {platform!r} '
                              f'(expected one of {tuple(RAW_PREFIX_BY_PLATFORM)})')

        frame_type = device_cfg.get('frame_type', DEFAULT_FRAME_TYPE)
        if frame_type not in SUPPORTED_FRAME_TYPES:
            raise ConfigError(f'{where}: unknown frame_type {frame_type!r} '
                              f'(expected one of {SUPPORTED_FRAME_TYPES})')

        fit = device_cfg.get('fit', DEFAULT_FIT)
        if fit not in SUPPORTED_FITS:
            raise ConfigError(f'{where}: unknown fit {fit!r} (expected one of {SUPPORTED_FITS})')

        _validate_variants(device_cfg.get('variants'), frame_type, where)
        _validate_screen(device_cfg.get('screen'), where)

        if 'store_target' in device_cfg:
            _validate_store_target(device_cfg['store_target'], where)


def _require_int_at_least(value, minimum, label, where):
    """Reject the JSON values that would otherwise reach Pillow as a size or a coordinate.

    bool is excluded explicitly: it is a subclass of int, so `true` would pass as 1 and
    quietly render a one-pixel screen.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f'{where}: {label} must be an integer >= {minimum}, got {value!r}')


def _validate_variants(variants, frame_type, where):
    """Every device needs at least one variant, and the render path reads its filenames as strings."""
    if not isinstance(variants, dict) or not variants:
        raise ConfigError(f'{where}: "variants" must be a non-empty object, got {variants!r}')
    for variant_name, variant_cfg in variants.items():
        at = f'{where}, variant {variant_name}'
        if not isinstance(variant_cfg, dict):
            raise ConfigError(f'{at}: variant must be an object, got {variant_cfg!r}')
        frame_file = variant_cfg.get('frame')
        if not isinstance(frame_file, str) or not frame_file:
            raise ConfigError(f'{at}: "frame" must be a filename, got {frame_file!r}')
        if frame_type != 'rgba_with_mask':
            continue
        mask_file = variant_cfg.get('mask')
        if not isinstance(mask_file, str) or not mask_file:
            raise ConfigError(f'{at}: frame_type "rgba_with_mask" needs a "mask" filename, '
                              f'got {mask_file!r}')


def _validate_screen(screen, where):
    """The screen rect is dereferenced for every composite, so it has to be there and be usable.

    x and y may be 0, since a screen can start at the very corner of its frame, but a
    width or height below 1 has no meaning.
    """
    if not isinstance(screen, dict):
        raise ConfigError(f'{where}: "screen" must be an object, got {screen!r}')
    for key, minimum in (('x', 0), ('y', 0), ('width', 1), ('height', 1)):
        _require_int_at_least(screen.get(key), minimum, f'screen.{key}', where)


def _validate_store_target(store_target, where):
    """store_target may be left out entirely, but when it is present it has to be usable."""
    if not isinstance(store_target, dict):
        raise ConfigError(f'{where}: store_target must be an object, got {store_target!r}')
    for key in ('width', 'height'):
        _require_int_at_least(store_target.get(key), 1, f'store_target.{key}', where)


def check_variant_assets(device_id, device_cfg, variant_cfg, frame_dir):
    """Check the variant's frame (and mask) files on disk and return the frame's path.

    Called only once a device is known to have raw screenshots to render, so a frame that
    is not on this machine breaks only the runs that actually need it.
    """
    frame_path = os.path.join(frame_dir, variant_cfg['frame'])
    if not os.path.isfile(frame_path):
        raise ConfigError(f'{device_id}: frame image not found: {frame_path}')

    if device_cfg.get('frame_type', DEFAULT_FRAME_TYPE) != 'rgba_with_mask':
        return frame_path

    mask_path = os.path.join(frame_dir, variant_cfg['mask'])
    if not os.path.isfile(mask_path):
        raise ConfigError(f'{device_id}: mask image not found: {mask_path}')
    with Image.open(frame_path) as frame, Image.open(mask_path) as mask:
        if frame.size != mask.size:
            raise ConfigError(f'{device_id}: mask {mask.size} does not match frame {frame.size} '
                              f'({mask_path})')
    return frame_path


# ─── Screen mask helpers ───────────────────────────────────────

def _flood_fill_mask(img, start, predicate):
    w, h = img.size
    px = img.load()
    mask = Image.new('L', (w, h), 0)
    mpx = mask.load()
    queue = deque([start])
    visited = {start}
    while queue:
        x, y = queue.popleft()
        if predicate(px[x, y]):
            mpx[x, y] = 255
            for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                    visited.add((nx, ny))
                    queue.append((nx, ny))
    return mask


def _rounded_rect_mask(w, h, radius):
    scale = 2
    big = Image.new('L', (w * scale, h * scale), 0)
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle(
        [(0, 0), (w * scale - 1, h * scale - 1)],
        radius=radius * scale, fill=255,
    )
    return big.resize((w, h), Image.LANCZOS)


# ─── Generic compositing ──────────────────────────────────────

def load_screen_mask(frame, device_cfg, variant_cfg, frame_dir):
    """Build the screen-shaped mask: the variant's mask PNG for rgba_with_mask, else the frame's transparent hole.

    The flood fill walks around an opaque notch, so the hole it finds spans the full
    screen including the notch band — measuring the centre column instead would put the
    screen top below the notch. It stops at the opaque bezel and never reaches the
    transparent margin outside the device.
    """
    frame_type = device_cfg.get('frame_type', DEFAULT_FRAME_TYPE)
    if frame_type == 'rgba_with_mask':
        mask_path = os.path.join(frame_dir, variant_cfg['mask'])
        return Image.open(mask_path).convert('L')
    return _flood_fill_mask(
        frame, (frame.size[0] // 2, frame.size[1] // 2),
        lambda rgba: rgba[3] == 0,
    )


def fit_to_screen_width(screen, screen_mask, sx, sy, sw, sh):
    """Scale the screenshot to the screen width, keep its aspect ratio, and mask it to the screen shape.

    Returns the scaled RGBA layer and the y to paste it at — vertically centred in the
    screen when the screenshot ends up shorter than the screen.
    """
    orig_w, orig_h = screen.size
    scale = sw / orig_w
    new_w = sw
    new_h = int(orig_h * scale)
    screen = screen.resize((new_w, new_h), Image.LANCZOS)

    crop_mask = screen_mask.crop((sx, sy, sx + sw, sy + sh))
    crop_mask = crop_mask.resize((new_w, min(new_h, sh)), Image.LANCZOS)

    if new_h <= sh:
        screen.putalpha(crop_mask)
    else:
        full_mask = Image.new('L', (new_w, new_h), 0)
        full_mask.paste(crop_mask, (0, 0))
        screen.putalpha(full_mask)

    y_offset = max(0, (sh - new_h) // 2)
    return screen, sy + y_offset


def fit_to_cover_screen(screen, sw, sh):
    """Scale the screenshot until it covers the whole screen rect, then crop it to that rect from the top-left.

    App and web UIs anchor at the top-left, so whatever overflows goes off the right and
    bottom edges. A screenshot captured at the screen's own aspect ratio loses nothing.
    """
    scale = max(sw / screen.width, sh / screen.height)
    covered = screen.resize(
        (math.ceil(screen.width * scale), math.ceil(screen.height * scale)),
        Image.LANCZOS,
    )
    return covered.crop((0, 0, sw, sh))


def compose_with_frame(screenshot_path, output_path, device_cfg, variant_cfg, frame_dir):
    frame_path = os.path.join(frame_dir, variant_cfg['frame'])
    frame = Image.open(frame_path).convert('RGBA')
    screen = Image.open(screenshot_path).convert('RGBA')

    scr = device_cfg['screen']
    sx, sy, sw, sh = scr['x'], scr['y'], scr['width'], scr['height']

    frame_type = device_cfg.get('frame_type', DEFAULT_FRAME_TYPE)
    fit = device_cfg.get('fit', DEFAULT_FIT)
    if fit not in SUPPORTED_FITS:
        raise ConfigError(f'{device_cfg["name"]}: unknown fit {fit!r} (expected one of {SUPPORTED_FITS})')

    if fit == 'cover':
        placed = fit_to_cover_screen(screen, sw, sh)
        if frame_type == 'rgba_with_mask':
            # Defensive: clip to the mask in case a frame's transparent region reaches
            # past it. The bundled pixel8pro frames have no fully transparent pixel
            # outside their mask, but 875 partly transparent ones, which would let a
            # cover-fitted screenshot show faintly through the rounded corners.
            screen_mask = load_screen_mask(frame, device_cfg, variant_cfg, frame_dir)
            placed.putalpha(screen_mask.crop((sx, sy, sx + sw, sy + sh)))
        # rgba_transparent frames get no mask: the opaque bezel goes on top and already
        # clips the screenshot, and masking as well would leave a seam of half-transparent
        # pixels along the anti-aliased edge of the hole.
        paste_y = sy
    else:
        screen_mask = load_screen_mask(frame, device_cfg, variant_cfg, frame_dir)
        placed, paste_y = fit_to_screen_width(screen, screen_mask, sx, sy, sw, sh)

    canvas = Image.new('RGBA', frame.size, (0, 0, 0, 0))
    canvas.paste(placed, (sx, paste_y), placed)

    result = Image.alpha_composite(canvas, frame)

    result.save(output_path, 'PNG')
    print(f'  > {os.path.basename(output_path)} ({result.size[0]}x{result.size[1]})')


def place_on_background(framed_path, output_path, target_w, target_h, bg_color):
    framed = Image.open(framed_path).convert('RGBA')
    fw, fh = framed.size

    max_device_h = int(target_h * 0.88)
    scale = min(max_device_h / fh, (target_w * 0.90) / fw)
    new_w = int(fw * scale)
    new_h = int(fh * scale)
    framed = framed.resize((new_w, new_h), Image.LANCZOS)

    shadow_img = Image.new('RGBA', (target_w, target_h), (0, 0, 0, 0))
    device_alpha = framed.split()[3]
    shadow_layer = Image.new('RGBA', framed.size, (30, 50, 60, 60))
    shadow_layer.putalpha(device_alpha)

    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    shadow_img.paste(shadow_layer, (x + 3, y + 10), shadow_layer)
    shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=18))

    bg = Image.new('RGBA', (target_w, target_h), bg_color + (255,))
    bg = Image.alpha_composite(bg, shadow_img)
    bg.paste(framed, (x, y), framed)

    bg.convert('RGB').save(output_path, 'PNG')
    print(f'  > {os.path.basename(output_path)} ({target_w}x{target_h})')


def compose_frameless(screenshot_path, output_path, corner_radius=72):
    screen = Image.open(screenshot_path).convert('RGBA')
    w, h = screen.size
    mask = _rounded_rect_mask(w, h, corner_radius)
    screen.putalpha(mask)
    screen.save(output_path, 'PNG')
    print(f'  > {os.path.basename(output_path)} ({w}x{h})')


# ─── Main ───────────────────────────────────────────────────────

def store_target_size(device_cfg, frame_path):
    """Canvas size for the *_final.png background composite: store_target if set, else the frame's own size.

    Only an absent store_target falls back. A present one has already been validated, so a
    malformed entry fails loudly instead of quietly rendering at some other size.
    """
    target = device_cfg.get('store_target')
    if target is not None:
        return target['width'], target['height']
    with Image.open(frame_path) as frame:
        return frame.size


def main():
    parser = argparse.ArgumentParser(description='Composite screenshots into device frames')
    parser.add_argument('--raw-dir', required=True, help='Directory containing raw screenshots (ios_*.png, and_*.png, web_*.png)')
    parser.add_argument('--out-dir', help='Output directory (default: <raw-dir>/../framed)')
    parser.add_argument('--frame-dir', help=f'Directory containing devices.json and frame assets '
                                           f'(default: ${FRAME_DIR_ENV_VAR}, else {FALLBACK_FRAME_DIR})')
    parser.add_argument('--device', help='Process only this device (e.g. pixel8pro, iphone16promax)')
    parser.add_argument('--variant', help='Frame color variant (e.g. silver, black)')
    parser.add_argument('--bg-color', default='#F4F3EE', help='Background color hex (default: #F4F3EE)')
    parser.add_argument('--no-background', action='store_true', help='Skip background composites')
    parser.add_argument('--frameless', action='store_true', help='Also generate frameless variants')
    parser.add_argument('--raw-copy', action='store_true', help='Also copy raw opaque screenshots')
    args = parser.parse_args()

    raw_dir = os.path.abspath(args.raw_dir)
    frame_dir, frame_dir_source = resolve_frame_dir(args.frame_dir)
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.join(os.path.dirname(raw_dir), 'framed')

    if not os.path.isdir(raw_dir):
        print(f'Error: raw directory not found: {raw_dir}', file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(frame_dir):
        print(f'Error: frame directory not found: {frame_dir} (from {frame_dir_source})', file=sys.stderr)
        sys.exit(1)

    bg_color = parse_hex_color(args.bg_color)
    print(f'Frames: {frame_dir} (from {frame_dir_source})')

    devices = load_devices(frame_dir, frame_dir_source)
    if args.device:
        devices = {device_id: cfg for device_id, cfg in devices.items() if device_id == args.device}
    # Validate after the --device filter, so an entry for a device this machine's tool does
    # not know about cannot block a run that was never going to render it.
    validate_devices(devices)
    os.makedirs(out_dir, exist_ok=True)

    print('Compositing store screenshots...\n')

    for device_id, device_cfg in devices.items():
        prefix = RAW_PREFIX_BY_PLATFORM[device_cfg['platform']]

        variants = device_cfg['variants']
        if args.variant and args.variant in variants:
            variant_name = args.variant
        else:
            variant_name = list(variants.keys())[0]
        variant_cfg = variants[variant_name]

        raw_files = sorted([
            f for f in os.listdir(raw_dir)
            if f.startswith(prefix + '_') and f.endswith('.png')
        ])

        # Skip before touching any frame asset: a device with nothing to render must not
        # require its frame to be on this machine.
        if not raw_files:
            print(f'[{device_cfg["name"]}] No raw screenshots found ({prefix}_*.png)')
            continue

        frame_path = check_variant_assets(device_id, device_cfg, variant_cfg, frame_dir)
        target_w, target_h = store_target_size(device_cfg, frame_path)

        print(f'[{device_cfg["name"]} — {variant_name}]')

        for raw_file in raw_files:
            name = raw_file.replace('.png', '')
            raw_path = os.path.join(raw_dir, raw_file)
            framed_path = os.path.join(out_dir, f'{name}_framed.png')

            compose_with_frame(raw_path, framed_path, device_cfg, variant_cfg, frame_dir)

            if not args.no_background:
                final_path = os.path.join(out_dir, f'{name}_final.png')
                place_on_background(framed_path, final_path, target_w, target_h, bg_color)

        if args.frameless:
            print(f'\n[{device_cfg["name"]} — frameless]')
            for raw_file in raw_files:
                name = raw_file.replace('.png', '')
                raw_path = os.path.join(raw_dir, raw_file)
                rounded_path = os.path.join(out_dir, f'{name}_rounded.png')
                compose_frameless(raw_path, rounded_path)
                if not args.no_background:
                    noframe_path = os.path.join(out_dir, f'{name}_noframe.png')
                    place_on_background(rounded_path, noframe_path, target_w, target_h, bg_color)

        if args.raw_copy:
            print(f'\n[{device_cfg["name"]} — raw copies]')
            for raw_file in raw_files:
                name = raw_file.replace('.png', '')
                raw_path = os.path.join(raw_dir, raw_file)
                opaque_path = os.path.join(out_dir, f'{name}_raw.png')
                img = Image.open(raw_path).convert('RGB')
                img.save(opaque_path, 'PNG')
                print(f'  > {os.path.basename(opaque_path)} ({img.size[0]}x{img.size[1]})')

        print()

    print(f'Done! Files in: {os.path.abspath(out_dir)}')


if __name__ == '__main__':
    try:
        main()
    except ConfigError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
