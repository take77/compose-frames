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

Device frames are configured in frames/devices.json (relative to this script).
To add a new device, add its entry there and place frame assets in its subdirectory.
"""

from PIL import Image, ImageDraw, ImageFilter
from collections import deque
import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FRAME_DIR = os.path.join(SCRIPT_DIR, 'frames')
DEFAULT_BG_COLOR = (244, 243, 238)  # #F4F3EE


def parse_hex_color(hex_str):
    hex_str = hex_str.lstrip('#')
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))


def load_devices(frame_dir):
    devices_json = os.path.join(frame_dir, 'devices.json')
    with open(devices_json) as f:
        return json.load(f)


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

def compose_with_frame(screenshot_path, output_path, device_cfg, variant_cfg, frame_dir):
    frame_path = os.path.join(frame_dir, variant_cfg['frame'])
    frame = Image.open(frame_path).convert('RGBA')
    screen = Image.open(screenshot_path).convert('RGBA')

    scr = device_cfg['screen']
    sx, sy, sw, sh = scr['x'], scr['y'], scr['width'], scr['height']

    frame_type = device_cfg.get('frame_type', 'rgba_transparent')

    if frame_type == 'rgba_with_mask' and 'mask' in variant_cfg:
        mask_path = os.path.join(frame_dir, variant_cfg['mask'])
        screen_mask = Image.open(mask_path).convert('L')
    elif frame_type == 'rgba_transparent':
        screen_mask = _flood_fill_mask(
            frame, (frame.size[0] // 2, frame.size[1] // 2),
            lambda rgba: rgba[3] == 0,
        )
    else:
        screen_mask = _flood_fill_mask(
            frame, (frame.size[0] // 2, frame.size[1] // 2),
            lambda rgba: rgba[3] == 0,
        )

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

    canvas = Image.new('RGBA', frame.size, (0, 0, 0, 0))
    canvas.paste(screen, (sx, sy + y_offset), screen)
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

def main():
    parser = argparse.ArgumentParser(description='Composite screenshots into device frames')
    parser.add_argument('--raw-dir', required=True, help='Directory containing raw screenshots (ios_*.png, and_*.png)')
    parser.add_argument('--out-dir', help='Output directory (default: <raw-dir>/../framed)')
    parser.add_argument('--frame-dir', default=DEFAULT_FRAME_DIR, help='Directory containing frames/ and devices.json')
    parser.add_argument('--device', help='Process only this device (e.g. pixel8pro, iphone16promax)')
    parser.add_argument('--variant', help='Frame color variant (e.g. silver, black)')
    parser.add_argument('--bg-color', default='#F4F3EE', help='Background color hex (default: #F4F3EE)')
    parser.add_argument('--no-background', action='store_true', help='Skip background composites')
    parser.add_argument('--frameless', action='store_true', help='Also generate frameless variants')
    parser.add_argument('--raw-copy', action='store_true', help='Also copy raw opaque screenshots')
    args = parser.parse_args()

    raw_dir = os.path.abspath(args.raw_dir)
    frame_dir = os.path.abspath(args.frame_dir)
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.join(os.path.dirname(raw_dir), 'framed')

    if not os.path.isdir(raw_dir):
        print(f'Error: raw directory not found: {raw_dir}', file=sys.stderr)
        sys.exit(1)

    bg_color = parse_hex_color(args.bg_color)
    devices = load_devices(frame_dir)
    os.makedirs(out_dir, exist_ok=True)

    print('Compositing store screenshots...\n')

    for device_id, device_cfg in devices.items():
        if args.device and args.device != device_id:
            continue

        platform = device_cfg['platform']
        prefix = 'ios' if platform == 'ios' else 'and'
        target = device_cfg['store_target']

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

        if not raw_files:
            print(f'[{device_cfg["name"]}] No raw screenshots found ({prefix}_*.png)')
            continue

        print(f'[{device_cfg["name"]} — {variant_name}]')

        for raw_file in raw_files:
            name = raw_file.replace('.png', '')
            raw_path = os.path.join(raw_dir, raw_file)
            framed_path = os.path.join(out_dir, f'{name}_framed.png')

            compose_with_frame(raw_path, framed_path, device_cfg, variant_cfg, frame_dir)

            if not args.no_background:
                final_path = os.path.join(out_dir, f'{name}_final.png')
                place_on_background(framed_path, final_path, target['width'], target['height'], bg_color)

        if args.frameless:
            print(f'\n[{device_cfg["name"]} — frameless]')
            for raw_file in raw_files:
                name = raw_file.replace('.png', '')
                raw_path = os.path.join(raw_dir, raw_file)
                rounded_path = os.path.join(out_dir, f'{name}_rounded.png')
                compose_frameless(raw_path, rounded_path)
                if not args.no_background:
                    noframe_path = os.path.join(out_dir, f'{name}_noframe.png')
                    place_on_background(rounded_path, noframe_path, target['width'], target['height'], bg_color)

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
    main()
