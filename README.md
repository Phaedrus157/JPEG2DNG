# JPEG2DNG

Converts JPEG or 16-bit TIFF images to synthetic CFA (Bayer) DNG files that
trigger Adobe Lightroom Classic's full raw processing pipeline — including
**AI Denoise**, **Raw Details**, Adobe Color and Monochrome profiles, raw-gated
masking tools, and the complete Develop module.

> **Important:** This tool was developed and tested against **scanned
> photographic film** — colour slides, colour negatives, and B&W negatives
> scanned to JPEG or TIFF. The source images are fully-rendered, display-referred
> sRGB files from a film scanner. They are **not** camera sensor data. This
> distinction is fundamental to understanding both the capabilities and the known
> limitations documented below.

---

## Contents

- [The problem it solves](#the-problem-it-solves)
- [Requirements](#requirements)
- [Usage](#usage)
- [Modes](#modes)
- [Monochrome — recommended and well-tested](#monochrome--recommended-and-well-tested)
- [Color — functional with known limitations](#color--functional-with-known-limitations)
- [Color vs Monochrome: an honest assessment](#color-vs-monochrome-an-honest-assessment)
- [Gamma blend parameter](#gamma-blend-parameter)
- [16-bit TIFF input](#16-bit-tiff-input)
- [Companion batch scripts](#companion-batch-scripts)
- [Technical notes](#technical-notes)
- [License](#license)

---

## The problem it solves

Lightroom Classic withholds a substantial part of its processing capability
from JPEGs and TIFFs. AI Denoise, Raw Details, raw-specific tone mapping, Adobe
Color and Monochrome profiles, and certain masking tools are available **only
to raw files**. JPEG2DNG re-encodes a JPEG or 16-bit TIFF as a syntactically
valid CFA DNG — a raw format Lightroom recognises — unlocking these features
for scanned film, archival images, or any pre-rendered source that warrants
full raw processing capability.

The output DNG contains **no invented pixel data**. Source tonal values are
preserved through a linearisation step and written into a 16-bit RGGB Bayer
container. Lightroom's demosaic engine reconstructs the image and applies its
raw pipeline from that point forward.

---

## Requirements

Python 3.8 or later. No C compiler, no DNG SDK, no ExifTool required.

```
pip install Pillow numpy
```

---

## Usage

```bash
# Single file — monochrome scan (recommended)
py jpg_to_cfa_dng.py scan.jpg --mode mono

# Single file — colour scan from JPEG
py jpg_to_cfa_dng.py scan.jpg --mode color

# Single file — colour scan from 16-bit LRC-exported TIFF
py jpg_to_cfa_dng.py scan.tif --mode neutral --gamma 0.75

# Half-resolution output for upload or analysis
py jpg_to_cfa_dng.py scan.jpg --mode mono --scale 0.5
```

The output DNG is written to the same folder as the source file with the same
base filename. A scale suffix (`_x50`) is appended when `--scale` is used.

---

## Modes

| Flag | Best for |
|---|---|
| `--mode mono` | Scanned B&W negatives, B&W prints, any grayscale source |
| `--mode color` | Scanned colour JPEG (negative or slide) |
| `--mode neutral` | 16-bit TIFF pre-processed by LRC; also useful as a flat starting point |
| `--mode match` | Colour JPEG where faithful approximation of source appearance is priority |

---

## Monochrome — recommended and well-tested

`--mode mono` is the most successful and reliable mode in this tool. It was
developed for scanned B&W film negatives and has been used in production on
archival documentary photography.

**Why it works so well:**

The monochrome conversion collapses all three colour channels to a single
Rec.709 luminance value in linear light:

```
Y = 0.2126 R + 0.7152 G + 0.0722 B
```

This luma value is then normalised to fill the full 0–65535 16-bit range
(`luma / luma.max()`), and written **identically to all four Bayer positions**
(R = G1 = G2 = B = Y). Because all channels carry the same value, Lightroom's
demosaic algorithm has nothing to push around — no inter-channel variation means
no colour fringing, no false colour, no camera-profile push. The result is a
true neutral-grey DNG that Lightroom processes cleanly as a monochrome raw file.

Adobe Monochrome profile, all B&W-specific controls in the Develop module, AI
Denoise, and Raw Details are fully available and behave correctly.

**Monochrome workflow:**

```
Scanner JPEG (B&W negative)
    -> jpg_to_cfa_dng.py --mode mono
    -> CFA DNG (16-bit, neutral grey Bayer)
    -> Lightroom Classic: import, AI Denoise, level, crop, heal
    -> Export neutral JPEG for tonal analysis
    -> LRCNeutralizer: statistical tonal neutralisation written to DNG XMP
    -> Lightroom Classic: Read Metadata from Files
    -> Silver Efex Pro: film emulation, grain, zone system finishing
    -> 16-bit TIFF delivery
```

The companion project [LRCNeutralizer](https://github.com/Phaedrus157/LRCNeutralizer)
automates the tonal neutralisation step by analysing exported JPEGs and writing
recommended Develop slider values (Exposure, Whites, Blacks, Highlights, Shadows)
directly into each DNG's embedded XMP metadata.

---

## Color — functional with known limitations

Color conversion is significantly more complex than monochrome because Lightroom's
raw processing pipeline was designed for camera sensor data, not for
display-referred sRGB images from a film scanner. The tool provides two paths
depending on source type.

### Colour JPEG source (`--mode color`)

For JPEG scans that have not been pre-processed in Lightroom:

- Computes a per-image **gray-world white balance** estimate in linear light
  (`R_mean/G_mean`, `1.0`, `B_mean/G_mean`) and embeds this as `AsShotNeutral`
- Embeds the standard IEC 61966-2-1 **sRGB-to-XYZ D65 ColorMatrix**
- Computes **BaselineExposure** from the ratio of gamma mean to linear mean

The gray-world estimate is per-image, computed after sRGB gamma removal (correct
radiometric domain). The result is clipped to 1.0 per channel to stay within
valid DNG range.

**Known limitation with JPEG source:** Gray-world WB assumes the average scene
is neutral grey. It fails on images with strongly dominant hues (heavy blue sky,
black rock filling the frame). These images will show a cast in LRC. The
`--gamma` parameter (see below) helps with shadow rendering but does not fix
WB for these edge cases. White balance correction in LRC with the WB picker
resolves them individually.

### 16-bit TIFF source — pre-processed by LRC (`--mode neutral`)

When the source is a 16-bit TIFF exported from Lightroom (after cropping,
levelling, initial cleanup), gray-world WB is **bypassed entirely**:

- `AsShotNeutral = (1.0, 1.0, 1.0)` — tells LRC the data is already balanced
- Identity `ColorMatrix` — no colour transform applied by LRC
- `Make="Adobe"` `Model="DNG"` — causes LRC to use Adobe Standard profile
  (flat, neutral) instead of a camera-specific profile

**Why TIFF requires different handling:** The TIFF is already rendered and
colour-balanced by Lightroom's previous processing pass. Applying gray-world WB
to a pre-balanced image produces an incorrect `AsShotNeutral` and a visible
colour cast. Similarly, using a camera-specific colour profile (such as
Olympus OM-1, which was the original Make/Model in this script) compounds
the problem by applying sensor-calibrated colour science to data that was
never from that sensor.

**Recommended workflow for colour scanned film:**

```
Original scanned JPEG
    -> Lightroom Classic: import, crop, level, initial cleanup
    -> Export as 16-bit TIFF (sRGB, no compression)
    -> color_analyze_jpg.py  (per-image gamma_blend analysis)
    -> Review/adjust analysis CSV
    -> batch_jpg_to_dng_color.py --mode neutral --csv <analysis.csv>
    -> CFA DNGs (per-image gamma, identity matrix, neutral WB)
    -> Lightroom Classic: import, WB fine-tune, AI Denoise, color grading
```

The batch pipeline scripts for this workflow live in
`C:\Users\jaa15\OneDrive\PYProjects\Scripts\` and are documented in the
[Companion batch scripts](#companion-batch-scripts) section.

---

## Color vs Monochrome: an honest assessment

Monochrome conversion via `--mode mono` is **substantially more successful**
than colour conversion. The reasons are structural, not implementation gaps.

**Why mono works cleanly:**

1. The B&W scan has no meaningful colour information — all channels carry the
   same luminance signal. There is nothing for the demosaic algorithm or LRC's
   colour pipeline to distort.
2. Placing identical luma in all four Bayer positions means Lightroom's demosaic
   reconstructs a perfectly neutral image regardless of which colour profile,
   white balance, or colour matrix is applied.
3. The output is mathematically consistent with Lightroom's expectations for a
   monochrome raw file.

**Why colour is harder:**

1. The scanned JPEG is a fully-rendered, display-referred sRGB image. It has
   already passed through the scanner's colour processing — colour balance,
   tone curve, saturation. There is **no spectral separation** left. The R, G,
   and B channels do not represent raw sensor responses to different wavelengths;
   they represent a finished colour image.
2. Lightroom's raw pipeline was designed to take spectrally-separated raw sensor
   data and reconstruct colour through a camera profile calibrated to that
   specific sensor. Applied to pre-rendered sRGB data, this pipeline applies
   colour science that has no relationship to the original film's colour
   characteristics.
3. The sRGB gamma curve bakes display-referred tonal relationships into the
   pixel values. When the script removes this gamma to produce linear-light data
   (as a real raw converter would), LRC's raw tone curve then applies on top —
   stacking two tone curves and crushing shadows. The `--gamma` blend parameter
   was developed to mitigate this.
4. The RGGB Bayer pattern gives the green channel twice the spatial representation
   of red or blue. Lightroom's demosaic interpolates red and blue from fewer
   source pixels, which can introduce subtle hue shifts even with a perfectly
   neutral white balance and identity colour matrix.

**The practical result:**

Colour DNGs from this tool are an acceptable starting point for creative editing
and provide access to AI Denoise and Raw Details that are otherwise unavailable.
They are **not** a pixel-accurate reconstruction of the source JPEG's colour
rendering. A residual cool/cyan shift relative to the source is typical and
expected. This is correctable in LRC with the WB picker (one image, sync to all)
and provides a clean creative foundation from the raw editing pipeline.

For archival work requiring faithful colour reproduction, the TIFF export from
Lightroom remains the definitive colour reference. The DNG is the raw editing
companion.

---

## Gamma blend parameter

`--gamma` controls how much of the sRGB display gamma is removed before writing
pixel data into the DNG. This was developed specifically for scanned film where
the gamma-related shadow rendering problem is most visible.

| Value | Behaviour |
|---|---|
| `1.0` (default) | Full sRGB linearisation (IEC 61966-2-1). Mathematically correct for raw. Can crush shadows when LRC's raw tone curve stacks on top. |
| `0.0` | No linearisation. Gamma-encoded values written as-is. Softest shadows; closest to original scan appearance. |
| `0.5` | Midpoint blend. Good starting point for scanned slides with heavy shadow areas. |

Recommended starting points by scene type:

| Scene | `--gamma` |
|---|---|
| Outdoor, even tones | 0.75 – 1.0 |
| Mixed interior/exterior | 0.50 – 0.75 |
| Interior, window/door frame | 0.30 – 0.50 |
| Heavy shadow interior | 0.25 – 0.40 |

The companion `color_analyze_jpg.py` script automates per-image gamma selection
by analysing luminance distribution, shadow density, and edge/centre contrast
ratios before conversion.

---

## 16-bit TIFF input

The script accepts 16-bit TIFF files (Pillow auto-detects bit depth):

- **16-bit TIFF** normalises by 65535 → float32 [0,1] → uint16 DNG
- **8-bit JPEG or TIFF** normalises by 255 → float32 [0,1] → uint16 DNG

16-bit TIFF input from an LRC export provides 256× more shadow tonal precision
than 8-bit JPEG, directly improving `--gamma` accuracy in low-key zones. For
archival colour work, the recommended pipeline uses 16-bit TIFF as the
conversion source rather than the original 8-bit scanner JPEG.

---

## Companion batch scripts

The following scripts extend JPEG2DNG into a full batch pipeline for colour
film scanning work. They are not part of this repository but are maintained at
`C:\Users\jaa15\OneDrive\PYProjects\Scripts\`.

**`color_analyze_jpg.py`**
Analyses a folder of source JPG or TIF files and recommends a per-image
`gamma_blend` value based on five metrics: mean luminance, shadow density,
luminance standard deviation, edge/centre contrast ratio (dark-frame detection),
and channel dominance (gray-world WB reliability flag). Outputs a CSV that
feeds the batch converter.

**`batch_jpg_to_dng_color.py`**
Batch converts a folder of JPEG or TIFF files to CFA DNGs using per-image
`gamma_blend` values from the analysis CSV. Includes internal QC (shadow crush,
highlight clip, shadow delta vs source), automatic retry with stepped-down gamma
on QC failure, and a NEEDS_REVIEW flag for images where all retries are
exhausted.

---

## Technical notes

**DNG structure:** Built from scratch using Python's `struct` module with no
external DNG library. The TIFF/IFD structure is constructed directly, keeping
the dependency footprint to Pillow and NumPy only.

**Make/Model:** DNGs are tagged `Make="Adobe"` `Model="DNG"`. This causes
Lightroom to use its Adobe Standard profile (flat, neutral) rather than a
camera-specific colour profile. An earlier version used `Make="Olympus"`
`Model="OM-1"` to associate files with a known profile set, but this caused
Lightroom to apply sensor-calibrated colour science to scanner data, producing
a systematic colour tint across all modes. The Adobe DNG tags avoid this.

**Bayer pattern:** RGGB. Lightroom's demosaic engine handles this natively for
all DNG files.

**Gray-world WB domain:** Computed in linear light (after gamma removal), which
is the physically correct radiometric domain. Result is clipped to ≤ 1.0 per
channel to stay within valid DNG AsShotNeutral range.

**BaselineExposure:** Computed from `−log2(mean_gamma / mean_linear)`, which
calibrates LRC's initial exposure rendering to the source image's average
brightness. With `gamma_blend < 1.0`, the blended-linear mean is higher than
full-linear, so BaselineExposure is automatically less negative — LRC compensates
less aggressively, which is correct behaviour.

**ColorMatrix in color mode:** Uses the IEC 61966-2-1 XYZ D65 to sRGB matrix,
the standard inverse transform for sRGB-encoded sources. For TIFF sources, the
identity matrix is used instead (neutral mode) to avoid double colour transform.

---

## License

MIT License. Copyright (c) 2025 Phaedrus157. See LICENSE for details.
