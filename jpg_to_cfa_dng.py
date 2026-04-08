"""
jpg_to_cfa_dng.py
=================
Converts a JPEG to a synthetic CFA (Bayer) DNG that triggers Lightroom
Classic's full raw processing pipeline -- including Adobe Color/Monochrome
profiles and all raw-gated features.

Pipeline:
  JPEG -> decode -> sRGB linearize -> RGGB remosaic -> pure-Python CFA DNG

Output DNG is written to the same folder as the source JPEG.

Usage:
  py jpg_to_cfa_dng.py <input.jpg> [--mode neutral|match|mono] [--scale 0.5]

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

  --scale 0.5     Half-resolution output for upload/analysis (~10MB).

Bit depth:
  Output DNG is ALWAYS 16-bit (BitsPerSample=16, uint16 pixel data).
  In neutral/match modes: 8-bit JPEG values are scaled to uint16 range.
  In mono mode: luma is normalised to luma.max() before scaling, ensuring
  the full 0-65535 range is used regardless of source brightness ceiling.
  The source data ceiling is 8-bit (256 distinct tonal levels). The 16-bit
  container provides precision headroom for LRC's non-linear processing
  (curves, tone mapping, masking) and avoids quantization banding in
  smooth gradients during heavy development. No data is invented.

Deps:
  Pillow, numpy  (no C compiler, no pidng, no exiftool required)
"""

import sys
import struct
import math
import numpy as np
from pathlib import Path
from PIL import Image


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
# Pure-Python minimal CFA DNG writer
# DNG = TIFF + IFD with CFA tags. Built directly with struct.
# ---------------------------------------------------------------------------
def write_cfa_dng(bayer16: np.ndarray, out_path: str,
                  mode: str = 'neutral',
                  baseline_exposure: float = 0.0,
                  white_level: int = 65535) -> str:
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
    add_shorts     (259,   [1])                # Compression = uncompressed
    add_shorts     (262,   [32803])            # PhotometricInterpretation = CFA
    add_longs      (273,   [0])                # StripOffsets (patched below)
    add_shorts     (274,   [1])                # Orientation = top-left
    add_shorts     (277,   [1])                # SamplesPerPixel = 1
    add_longs      (278,   [h])                # RowsPerStrip
    add_longs      (279,   [len(image_data)])  # StripByteCounts
    add_shorts     (284,   [1])                # PlanarConfiguration
    add_shorts     (33421, [2, 2])             # CFARepeatPatternDim
    add_bytes_tag  (33422, [0, 1, 1, 2])       # CFAPattern = RGGB
    add_ascii      (271,   "PhaedrusMedia")
    add_ascii      (272,   "JPEG2DNG")
    add_bytes_tag  (50706, [1, 4, 0, 0])       # DNGVersion 1.4
    add_bytes_tag  (50707, [1, 1, 0, 0])       # DNGBackwardVersion
    add_ascii      (50708, "PhaedrusMedia JPEG2DNG")
    add_shorts     (50717, [white_level])      # WhiteLevel

    if mode in ('neutral', 'mono'):
        # --- NEUTRAL / MONO MODE ---
        # Identity ColorMatrix: no colour transform. LRC applies its own default.
        # For mono: luma is already equal in all channels so identity is correct.
        add_srationals (50721, [
            (1,1),(0,1),(0,1),
            (0,1),(1,1),(0,1),
            (0,1),(0,1),(1,1),
        ])
        add_shorts     (50778, [21])           # CalibrationIlluminant1 = D65
        add_srational1 (50730, 0.0)            # BaselineExposure = 0.0
        add_rationals  (50728, [(1,1),(1,1),(1,1)])  # AsShotNeutral = 1:1:1

    else:
        # --- MATCH MODE ---
        # Calibrated values measured from actual DNG pixel analysis (2026-04-01).
        # Default render in LRC approximates the source JPEG.
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
def convert(src: Path, mode: str = 'neutral', scale: float = 1.0) -> Path:
    out_dir = src.parent
    print(f"\n  Source : {src}")
    print(f"  Mode   : {mode}")
    print(f"  Scale  : {scale}")
    print(f"  Output : {out_dir}\n")

    # 1. Load JPEG as RGB
    img = Image.open(src).convert("RGB")

    # 2. Resize if requested
    if scale != 1.0:
        new_w = int(img.width  * scale)
        new_h = int(img.height * scale)
        new_w = new_w - (new_w % 2)
        new_h = new_h - (new_h % 2)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        print(f"  Resized     {img.width}x{img.height} px")

    arr = np.array(img).astype(np.float32) / 255.0
    print(f"  Loaded      {arr.shape[1]}x{arr.shape[0]} px, 8-bit RGB")

    # 3. Remove sRGB gamma -> linear light
    linear = srgb_to_linear(arr)
    print(f"  Linearised  range {linear.min():.4f}-{linear.max():.4f}")

    # 4. Mode-specific processing
    be = 0.0

    if mode == 'mono':
        # Rec.709 luminance weights: Y = 0.2126 R + 0.7152 G + 0.0722 B
        # Applied in linear light (correct -- must NOT weight in gamma space)
        luma = (0.2126 * linear[:,:,0]
              + 0.7152 * linear[:,:,1]
              + 0.0722 * linear[:,:,2])
        print(f"  Luma        Rec.709  range {luma.min():.4f}-{luma.max():.4f}")

        # Normalise to fill full 16-bit range.
        # luma.max() is typically ~0.76 for sRGB sources (Rec.709 ceiling).
        # Without normalisation WhiteLevel would be ~49967, leaving ~24% of
        # the 16-bit encoding space unused and reducing LRC slider headroom.
        # Dividing by luma.max() stretches to 0.0-1.0 without clipping or
        # inventing data. Relative tonal relationships are fully preserved.
        luma_max = float(luma.max())
        if luma_max > 0:
            luma = luma / luma_max
        print(f"  Normalised  range {luma.min():.4f}-{luma.max():.4f}  (max was {luma_max:.4f})")

        # Crop to even dims
        h, w = luma.shape
        h_e, w_e = h - (h % 2), w - (w % 2)
        luma = luma[:h_e, :w_e]

        # Map luma identically to all 4 Bayer positions
        # R=G1=G2=B=Y -- demosaic will reconstruct neutral gray
        bayer = np.empty((h_e, w_e), dtype=np.float32)
        bayer[0::2, 0::2] = luma[0::2, 0::2]   # R  position
        bayer[0::2, 1::2] = luma[0::2, 1::2]   # G1 position
        bayer[1::2, 0::2] = luma[1::2, 0::2]   # G2 position
        bayer[1::2, 1::2] = luma[1::2, 1::2]   # B  position
        print(f"  Remosaiced  RGGB Bayer {w_e}x{h_e} (all positions = luma)")

    elif mode == 'match':
        # BaselineExposure computed from image stats (color path only)
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
        # neutral -- standard RGGB color path
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
                  baseline_exposure=be, white_level=white_level)

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
        print("Usage: py jpg_to_cfa_dng.py <input.jpg> [--mode neutral|match|mono] [--scale 0.5]")
        sys.exit(1)

    src   = Path(args[0]).resolve()
    mode  = 'neutral'
    scale = 1.0

    if "--mode" in args:
        idx = args.index("--mode")
        try:
            mode = args[idx + 1]
            if mode not in ('neutral', 'match', 'mono'):
                raise ValueError
        except (IndexError, ValueError):
            print("ERROR: --mode must be 'neutral', 'match', or 'mono'")
            sys.exit(1)

    if "--scale" in args:
        idx = args.index("--scale")
        try:
            scale = float(args[idx + 1])
        except (IndexError, ValueError):
            print("ERROR: --scale requires a numeric argument e.g. --scale 0.5")
            sys.exit(1)

    if not src.exists():
        print(f"ERROR: File not found: {src}")
        sys.exit(1)

    result = convert(src, mode=mode, scale=scale)
    sys.exit(0 if result else 1)
