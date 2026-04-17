"""
jpg_to_cfa_dng.py
=================
Converts a JPEG or 16-bit TIFF to a synthetic CFA (Bayer) DNG that triggers
Lightroom Classic's full raw processing pipeline -- including Adobe Color/
Monochrome profiles and all raw-gated features.

Pipeline:
  JPEG/TIFF -> decode -> sRGB linearize (blended) -> RGGB remosaic -> CFA DNG

Output DNG is written to the same folder as the source file.

Usage:
  py jpg_to_cfa_dng.py <input.jpg|tif> [--mode neutral|match|mono|color]
                                        [--scale 0.5] [--gamma 0.0-1.0]

  --mode neutral  (default) Flat metadata -- LRC opens with full headroom,
                  no pre-applied corrections. Best for creative color work.
  --mode match    Calibrated metadata -- LRC default render approximates
                  the source JPEG. Best for archival faithful reproduction.
  --mode mono     Monochrome source -- converts to Rec.709 luma first, then
                  normalises luma to fill the full 0-65535 16-bit range, then
                  maps luma identically to all 4 Bayer positions. Eliminates
                  scanner color fringing and avoids false color in demosaic.
                  Normalisation maximises dynamic range for LRC editing without
                  clipping or inventing data.
                  Use for B&W scans, monochrome film, or any grayscale source.
  --mode color    Color JPEG or TIFF source.
                  JPEG: computes per-image gray-world white balance and embeds
                        the standard sRGB-to-XYZ color matrix.
                  TIFF: skips gray-world WB (TIF is pre-processed by LRC and
                        already color-balanced; gray-world would produce a tint).
                        AsShotNeutral = (1,1,1). sRGB-to-XYZ matrix retained.
                  Use for scanned color prints, color negatives, color slides.

  --scale 0.5     Half-resolution output for upload/analysis (~10MB).
                  Note: scale uses a uint8 proxy resize -- for analysis/test
                  use only, not recommended for final archival output.

  --gamma 0.75    Gamma blend factor (default 1.0).
                  Controls how much of the sRGB gamma curve is removed before
                  writing pixel data into the DNG.

                  1.0 = full linearization (IEC 61966-2-1 piecewise).
                        Mathematically correct for a raw sensor pipeline.
                        Can crush shadows on images where the scan already has
                        a display-referred gamma curve baked in and LRC's raw
                        tone curve then stacks on top.

                  0.0 = no linearization. Gamma-encoded sRGB values written
                        directly into the DNG. Softest shadow rolloff; closest
                        to the original scan appearance. LRC's raw pipeline
                        still applies its tone curve so output will be brighter
                        than source.

                  0.5 = blend halfway between gamma and fully linear. Preserves
                        shadow detail while still giving LRC meaningful linear-
                        light data to work with. Good starting point for scanned
                        slides or prints with heavy shadow regions or dark
                        borders/frames.

                  Recommended starting points by image type:
                    Outdoor scenes, even tones      -->  0.75 - 1.0
                    Mixed interior/exterior          -->  0.50 - 0.75
                    Dark-framed (window/door border) -->  0.30 - 0.50
                    Shadow-heavy interior            -->  0.25 - 0.40

Bit depth:
  Output DNG is ALWAYS 16-bit (BitsPerSample=16, uint16 pixel data).
  Source bit depth is auto-detected on load:
    8-bit  JPEG or TIFF  ->  normalised by 255   ->  float32 [0,1]  ->  uint16
    16-bit TIFF          ->  normalised by 65535  ->  float32 [0,1]  ->  uint16
  16-bit TIFF input from LRC export provides 256x more shadow precision
  than 8-bit JPEG, directly improving gamma_blend accuracy in low-key zones.
  In mono mode: luma is normalised to luma.max() before scaling, ensuring
  the full 0-65535 range is used regardless of source brightness ceiling.
  No data is invented.

Make/Model:
  DNGs are tagged Make="Adobe" Model="DNG" so LRC uses Adobe Standard
  profile -- flat and neutral, no camera-specific colour push.
  Previously "Olympus OM-1" caused LRC to apply a sensor-calibrated
  profile that produced a colour tint on all source types.

Deps:
  Pillow, numpy  (no C compiler, no pidng, no exiftool required)
"""

import sys
import struct
import math
import numpy as np
from pathlib import Path
from PIL import Image

# Extensions treated as pre-processed (LRC export) -- skip gray-world WB
TIFF_EXTENSIONS = {'.tif', '.tiff'}


# ---------------------------------------------------------------------------
# sRGB -> linear light (IEC 61966-2-1 piecewise)
# ---------------------------------------------------------------------------
def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(
        c <= 0.04045,
        c / 12.92,
        ((c + 0.055) / 1.055) ** 2.4
    )


# ---------------------------------------------------------------------------
# Blended linearization
# gamma_blend=1.0 -> full linear (existing behavior)
# gamma_blend=0.0 -> keep sRGB gamma values as-is
# gamma_blend=0.5 -> midpoint blend between gamma and linear
# ---------------------------------------------------------------------------
def apply_gamma_blend(arr: np.ndarray, gamma_blend: float) -> np.ndarray:
    if gamma_blend >= 1.0:
        return srgb_to_linear(arr)
    if gamma_blend <= 0.0:
        return arr.copy()
    linear = srgb_to_linear(arr)
    return arr * (1.0 - gamma_blend) + linear * gamma_blend


# ---------------------------------------------------------------------------
# Pure-Python minimal CFA DNG writer
# DNG = TIFF + IFD with CFA tags. Built directly with struct.
# ---------------------------------------------------------------------------
def write_cfa_dng(bayer16: np.ndarray, out_path: str,
                  mode: str = 'neutral',
                  baseline_exposure: float = 0.0,
                  white_level: int = 65535,
                  asshot_neutral: tuple = (1.0, 1.0, 1.0)) -> str:
    assert bayer16.ndim == 2,          "bayer16 must be 2D (H x W)"
    assert bayer16.dtype == np.uint16, "bayer16 must be uint16"

    h, w       = bayer16.shape
    image_data = bayer16.tobytes()

    BYTE=1; ASCII=2; SHORT=3; LONG=4; RATIONAL=5; SRATIONAL=10

    entries_raw = []
    extra_data  = bytearray()

    def add_shorts(tag, values):
        if len(values) <= 2:
            inline = b''.join(struct.pack('<H', v) for v in values).ljust(4, b'\x00')
            entries_raw.append((tag, SHORT, len(values), inline, None))
        else:
            off = len(extra_data)
            for v in values: extra_data.extend(struct.pack('<H', v))
            entries_raw.append((tag, SHORT, len(values), None, off))

    def add_longs(tag, values):
        if len(values) == 1:
            entries_raw.append((tag, LONG, 1, struct.pack('<L', values[0]), None))
        else:
            off = len(extra_data)
            for v in values: extra_data.extend(struct.pack('<L', v))
            entries_raw.append((tag, LONG, len(values), None, off))

    def add_ascii(tag, s):
        b = s.encode('ascii') + b'\x00'
        off = len(extra_data)
        extra_data.extend(b)
        entries_raw.append((tag, ASCII, len(b), None, off))

    def add_bytes_tag(tag, values):
        if len(values) <= 4:
            inline = bytes(values).ljust(4, b'\x00')
            entries_raw.append((tag, BYTE, len(values), inline, None))
        else:
            off = len(extra_data)
            extra_data.extend(bytes(values))
            entries_raw.append((tag, BYTE, len(values), None, off))

    def add_srational1(tag, value_float):
        denom = 10000
        num   = int(round(value_float * denom))
        off   = len(extra_data)
        extra_data.extend(struct.pack('<lL', num, denom))
        entries_raw.append((tag, SRATIONAL, 1, None, off))

    def add_srationals(tag, pairs):
        off = len(extra_data)
        for n, d in pairs:
            extra_data.extend(struct.pack('<lL', n, d))
        entries_raw.append((tag, SRATIONAL, len(pairs), None, off))

    def add_rationals(tag, pairs):
        off = len(extra_data)
        for n, d in pairs:
            extra_data.extend(struct.pack('<LL', n, d))
        entries_raw.append((tag, RATIONAL, len(pairs), None, off))

    # --- Core structural tags (identical in all modes) ---
    add_longs      (254,   [0])
    add_shorts     (256,   [w])
    add_shorts     (257,   [h])
    add_shorts     (258,   [16])
    add_shorts     (259,   [1])
    add_shorts     (262,   [32803])
    add_longs      (273,   [0])
    add_shorts     (274,   [1])
    add_shorts     (277,   [1])
    add_longs      (278,   [h])
    add_longs      (279,   [len(image_data)])
    add_shorts     (284,   [1])
    add_shorts     (33421, [2, 2])
    add_bytes_tag  (33422, [0, 1, 1, 2])
    # Make/Model = "Adobe"/"DNG" -> LRC uses Adobe Standard profile (neutral).
    # "Olympus OM-1" previously caused a sensor-calibrated profile to be
    # applied, producing a colour tint on all source types.
    add_ascii      (271,   "Adobe")
    add_ascii      (272,   "DNG")
    add_bytes_tag  (50706, [1, 4, 0, 0])
    add_bytes_tag  (50707, [1, 1, 0, 0])
    add_ascii      (50708, "Adobe DNG")
    add_shorts     (50717, [white_level])

    if mode in ('neutral', 'mono'):
        add_srationals (50721, [
            (1,1),(0,1),(0,1),
            (0,1),(1,1),(0,1),
            (0,1),(0,1),(1,1),
        ])
        add_shorts     (50778, [21])
        add_srational1 (50730, 0.0)
        add_rationals  (50728, [(1,1),(1,1),(1,1)])

    elif mode == 'color':
        add_srationals (50721, [
            ( 32405, 10000), (-15371, 10000), ( -4985, 10000),
            ( -9693, 10000), ( 18760, 10000), (   416, 10000),
            (   556, 10000), ( -2040, 10000), ( 10572, 10000),
        ])
        add_shorts     (50778, [21])
        add_srational1 (50730, baseline_exposure)
        denom = 1000000
        add_rationals  (50728, [
            (int(round(asshot_neutral[0] * denom)), denom),
            (int(round(asshot_neutral[1] * denom)), denom),
            (int(round(asshot_neutral[2] * denom)), denom),
        ])

    else:
        add_srationals (50721, [
            ( 31339, 10000), (-16169, 10000), ( -4906, 10000),
            ( -9788, 10000), ( 19161, 10000), (   335, 10000),
            (   719, 10000), ( -2290, 10000), ( 14052, 10000),
        ])
        add_shorts     (50778, [21])
        add_srational1 (50730, baseline_exposure)
        add_rationals  (50728, [(957407, 1000000),
                                (1000000, 1000000),
                                (978414, 1000000)])

    entries_raw.sort(key=lambda e: e[0])

    n_entries   = len(entries_raw)
    ifd_offset  = 8
    ifd_size    = 2 + n_entries * 12 + 4
    extra_start = ifd_offset + ifd_size
    image_start = extra_start + len(extra_data)
    if image_start % 2:
        image_start += 1

    buf = bytearray()
    buf += b'II'
    buf += struct.pack('<H', 42)
    buf += struct.pack('<L', ifd_offset)
    buf += struct.pack('<H', n_entries)
    for (tag, typ, count, inline, extra_off) in entries_raw:
        buf += struct.pack('<HHL', tag, typ, count)
        if inline is not None:
            buf += struct.pack('<L', image_start) if tag == 273 else inline
        else:
            buf += struct.pack('<L', extra_start + extra_off)
    buf += struct.pack('<L', 0)
    buf += bytes(extra_data)
    while len(buf) < image_start:
        buf += b'\x00'
    buf += image_data

    Path(out_path).write_bytes(buf)
    return out_path


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------
def convert(src: Path, mode: str = 'neutral', scale: float = 1.0,
            gamma_blend: float = 1.0) -> Path:
    out_dir = src.parent
    is_tif  = src.suffix.lower() in TIFF_EXTENSIONS

    print(f"\n  Source      : {src}")
    print(f"  Mode        : {mode}")
    print(f"  Scale       : {scale}")
    print(f"  GammaBlend  : {gamma_blend:.2f}  (1.0=full linear, 0.0=keep gamma)")
    print(f"  Output      : {out_dir}\n")

    # 1. Load source image (JPEG or TIFF) as float32 RGB in [0, 1].
    img = Image.open(src)
    if img.mode == 'I':
        arr_raw   = np.array(img, dtype=np.int32).clip(0, 65535).astype(np.uint16)
        arr_raw   = np.stack([arr_raw, arr_raw, arr_raw], axis=-1)
        bit_depth = 16
        norm      = 65535.0
    else:
        img_rgb   = img.convert("RGB")
        arr_raw   = np.array(img_rgb)
        if arr_raw.dtype == np.uint16:
            bit_depth = 16
            norm      = 65535.0
        else:
            bit_depth = 8
            norm      = 255.0

    arr = arr_raw.astype(np.float32) / norm
    print(f"  Loaded      {arr.shape[1]}x{arr.shape[0]} px, "
          f"{bit_depth}-bit RGB  (norm={norm:.0f})")

    # 2. Resize if requested.
    if scale != 1.0:
        new_w = int(arr.shape[1] * scale)
        new_h = int(arr.shape[0] * scale)
        new_w = new_w - (new_w % 2)
        new_h = new_h - (new_h % 2)
        arr_u8 = (arr * 255).clip(0, 255).astype(np.uint8)
        pil_rs = Image.fromarray(arr_u8).resize((new_w, new_h), Image.LANCZOS)
        arr    = np.array(pil_rs).astype(np.float32) / 255.0
        print(f"  Resized     {new_w}x{new_h} px")

    # 3. Blended linearization
    linear = apply_gamma_blend(arr, gamma_blend)
    print(f"  Linearised  gamma_blend={gamma_blend:.2f}  "
          f"range {linear.min():.4f}-{linear.max():.4f}")

    # 4. Mode-specific processing
    be             = 0.0
    asshot_neutral = (1.0, 1.0, 1.0)

    if mode == 'mono':
        luma = (0.2126 * linear[:,:,0]
              + 0.7152 * linear[:,:,1]
              + 0.0722 * linear[:,:,2])
        print(f"  Luma        Rec.709  range {luma.min():.4f}-{luma.max():.4f}")
        luma_max = float(luma.max())
        if luma_max > 0:
            luma = luma / luma_max
        print(f"  Normalised  range {luma.min():.4f}-{luma.max():.4f}  (max was {luma_max:.4f})")
        h, w = luma.shape
        h_e, w_e = h - (h % 2), w - (w % 2)
        luma = luma[:h_e, :w_e]
        bayer = np.empty((h_e, w_e), dtype=np.float32)
        bayer[0::2, 0::2] = luma[0::2, 0::2]
        bayer[0::2, 1::2] = luma[0::2, 1::2]
        bayer[1::2, 0::2] = luma[1::2, 0::2]
        bayer[1::2, 1::2] = luma[1::2, 1::2]
        print(f"  Remosaiced  RGGB Bayer {w_e}x{h_e} (all positions = luma)")

    elif mode == 'color':
        # TIFF (LRC export): skip gray-world WB, set AsShotNeutral=(1,1,1).
        # JPEG (raw scan): compute gray-world WB from channel means.
        if is_tif:
            asshot_neutral = (1.0, 1.0, 1.0)
            print(f"  WB           TIF source -- AsShotNeutral=(1,1,1)  "
                  f"(pre-processed, gray-world bypassed)")
        else:
            r_mean = float(linear[:,:,0].mean())
            g_mean = float(linear[:,:,1].mean())
            b_mean = float(linear[:,:,2].mean())
            g_mean = max(g_mean, 1e-6)
            asshot_neutral = (
                min(r_mean / g_mean, 1.0),
                1.0,
                min(b_mean / g_mean, 1.0),
            )
            print(f"  WB (gray world) R/G={asshot_neutral[0]:.4f}  "
                  f"B/G={asshot_neutral[2]:.4f}")

        mean_linear = float(linear.mean())
        mean_gamma  = float(arr.mean())
        be = -math.log2(mean_gamma / mean_linear) if mean_linear > 0 else -0.579
        print(f"  BaselineExp {be:.4f} EV")
        h, w = linear.shape[:2]
        h_e, w_e = h - (h % 2), w - (w % 2)
        linear = linear[:h_e, :w_e, :]
        bayer = np.empty((h_e, w_e), dtype=np.float32)
        bayer[0::2, 0::2] = linear[0::2, 0::2, 0]
        bayer[0::2, 1::2] = linear[0::2, 1::2, 1]
        bayer[1::2, 0::2] = linear[1::2, 0::2, 1]
        bayer[1::2, 1::2] = linear[1::2, 1::2, 2]
        print(f"  Remosaiced  RGGB Bayer {w_e}x{h_e}")

    elif mode == 'match':
        mean_linear = float(linear.mean())
        mean_gamma  = float(arr.mean())
        be = -math.log2(mean_gamma / mean_linear) if mean_linear > 0 else -0.579
        print(f"  BaselineExp {be:.4f} EV")
        h, w = linear.shape[:2]
        h_e, w_e = h - (h % 2), w - (w % 2)
        linear = linear[:h_e, :w_e, :]
        bayer = np.empty((h_e, w_e), dtype=np.float32)
        bayer[0::2, 0::2] = linear[0::2, 0::2, 0]
        bayer[0::2, 1::2] = linear[0::2, 1::2, 1]
        bayer[1::2, 0::2] = linear[1::2, 0::2, 1]
        bayer[1::2, 1::2] = linear[1::2, 1::2, 2]
        print(f"  Remosaiced  RGGB Bayer {w_e}x{h_e}")

    else:
        # neutral
        h, w = linear.shape[:2]
        h_e, w_e = h - (h % 2), w - (w % 2)
        linear = linear[:h_e, :w_e, :]
        bayer = np.empty((h_e, w_e), dtype=np.float32)
        bayer[0::2, 0::2] = linear[0::2, 0::2, 0]
        bayer[0::2, 1::2] = linear[0::2, 1::2, 1]
        bayer[1::2, 0::2] = linear[1::2, 0::2, 1]
        bayer[1::2, 1::2] = linear[1::2, 1::2, 2]
        print(f"  Remosaiced  RGGB Bayer {w_e}x{h_e}")

    # 5. Scale to 16-bit
    bayer16     = (bayer * 65535).clip(0, 65535).astype(np.uint16)
    white_level = int(bayer16.max())
    print(f"  16-bit      range {bayer16.min()}-{bayer16.max()} (BitsPerSample=16, uint16)")

    # 6. Write DNG
    scale_tag = "" if scale == 1.0 else f"_x{int(scale*100)}"
    dng_path  = src.parent / (src.stem + scale_tag + ".dng")
    write_cfa_dng(bayer16, str(dng_path), mode=mode,
                  baseline_exposure=be, white_level=white_level,
                  asshot_neutral=asshot_neutral)

    size_mb = dng_path.stat().st_size / (1024 * 1024)
    print(f"\n  Output : {dng_path}")
    print(f"  Size   : {size_mb:.1f} MB\n")

    return dng_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: py jpg_to_cfa_dng.py <input.jpg|tif> "
              "[--mode neutral|match|mono|color] [--scale 0.5] [--gamma 0.75]")
        sys.exit(1)

    src         = Path(args[0]).resolve()
    mode        = 'neutral'
    scale       = 1.0
    gamma_blend = 1.0

    if "--mode" in args:
        idx = args.index("--mode")
        try:
            mode = args[idx + 1]
            if mode not in ('neutral', 'match', 'mono', 'color'):
                raise ValueError
        except (IndexError, ValueError):
            print("ERROR: --mode must be 'neutral', 'match', 'mono', or 'color'")
            sys.exit(1)

    if "--scale" in args:
        idx = args.index("--scale")
        try:
            scale = float(args[idx + 1])
        except (IndexError, ValueError):
            print("ERROR: --scale requires a numeric argument e.g. --scale 0.5")
            sys.exit(1)

    if "--gamma" in args:
        idx = args.index("--gamma")
        try:
            gamma_blend = float(args[idx + 1])
            if not (0.0 <= gamma_blend <= 1.0):
                raise ValueError
        except (IndexError, ValueError):
            print("ERROR: --gamma must be a value between 0.0 and 1.0")
            sys.exit(1)

    if not src.exists():
        print(f"ERROR: File not found: {src}")
        sys.exit(1)

    result = convert(src, mode=mode, scale=scale, gamma_blend=gamma_blend)
    sys.exit(0 if result else 1)
