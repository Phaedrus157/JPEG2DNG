JPEG2DNG
========

Converts a JPEG to a synthetic CFA (Bayer) DNG that triggers Adobe Lightroom
Classic's full raw processing pipeline — including raw-gated features, AI
Denoise, Adobe Color and Monochrome profiles, masking, and the complete
Develop module.


The problem it solves
---------------------

Lightroom Classic withholds a substantial part of its processing capability
from JPEGs. AI Denoise, raw-specific tone mapping, Adobe Color and Monochrome
profiles, and certain masking tools are available only to raw files. JPEG2DNG
re-encodes a JPEG as a syntactically valid CFA DNG — a raw format Lightroom
recognises — unlocking these features for scanned film, archival images, or
any JPEG source that warrants full raw processing.

The output DNG contains no invented pixel data. The source JPEG's tonal
values are preserved through a precise linearisation step and written into a
16-bit RGGB Bayer container. Lightroom's demosaic engine then reconstructs
the image and applies its raw processing pipeline from that point forward.


Conversion modes
----------------

Four modes are available, selected with the --mode flag:

neutral (default)
    Identity color matrix, flat metadata, no pre-applied corrections.
    Lightroom opens the DNG with full headroom and its default rendering.
    Best for creative color work or any case where you want LRC to start
    from scratch.

match
    Calibrated ColorMatrix and computed BaselineExposure. LRC's default
    render approximates the visual appearance of the source JPEG.
    Best for archival work where faithful reproduction of the original
    scan is the priority.

mono
    Purpose-built for scanned B&W film and any grayscale source.
    Converts to Rec.709 luminance in linear light (correct spectral
    weighting), normalises luma to fill the full 0-65535 16-bit range,
    then maps the identical luma value to all four Bayer positions
    (R=G1=G2=B=Y). This eliminates the color fringing and false color
    artifacts that occur when a demosaic algorithm processes a signal
    with zero inter-channel variation. Output is a true neutral-gray
    DNG. Adobe Monochrome profile and all B&W-specific processing tools
    in LRC are fully available.

color
    Purpose-built for scanned color prints, color negatives, and color
    slides where the JPEG already carries fully-developed color science
    baked in by the scanner.

    The problem with converting processed color JPEGs via neutral or match
    mode is that Lightroom applies its own camera color rendering on top of
    the already-processed image, producing double color science and wrong
    hues. The color mode solves this by:

      1. Computing a per-image gray-world white balance estimate in linear
         light (R_mean/G_mean, 1.0, B_mean/G_mean), which tells LRC exactly
         where neutral is for this specific image.
      2. Embedding the standard IEC 61966-2-1 sRGB-to-XYZ D65 ColorMatrix,
         which matches the color space of the source JPEG.
      3. Computing BaselineExposure from the image's mean linear/gamma ratio
         so LRC's initial exposure rendering is calibrated to the source.

    The result: LRC's default render closely matches the source JPEG's
    colors while still providing the full raw processing pipeline — AI
    Denoise, Adobe Color profiles, masking, and 16-bit headroom for
    non-destructive editing.

    Best for scanned color film, color prints, or any color JPEG source
    that requires full raw processing features.


Bit depth
---------

All output DNGs are 16-bit regardless of mode. The source JPEG contains
256 distinct tonal levels (8-bit). Storing these in a 16-bit container
provides precision headroom for LRC's non-linear processing — tone curves,
masks, parametric adjustments — and prevents quantization banding in smooth
gradients during heavy Develop work. No tonal data is invented; the 16-bit
values are a scaled representation of the original 8-bit source.

In mono mode, luma is normalised to luma.max() before scaling to 16-bit.
This stretches the tonal range to use the full encoding space, maximising
slider headroom in LRC without clipping or modifying relative tonal
relationships.


Requirements
------------

Python 3.8 or later.
Pillow and NumPy are the only dependencies — no C compiler, no external
tools, no DNG SDK required.

    pip install Pillow numpy


Usage
-----

Single file, default neutral mode:

    py jpg_to_cfa_dng.py image.jpg

Monochrome mode for scanned B&W film (recommended for all grayscale sources):

    py jpg_to_cfa_dng.py scan.jpg --mode mono

Color mode for scanned color prints, negatives, or slides:

    py jpg_to_cfa_dng.py scan.jpg --mode color

Match mode for archival faithful reproduction of color JPEGs:

    py jpg_to_cfa_dng.py scan.jpg --mode match

Half-resolution output for upload or analysis (useful for large scans):

    py jpg_to_cfa_dng.py scan.jpg --mode mono --scale 0.5

The output DNG is written to the same folder as the source JPEG with the
same base filename. A scale tag (_x50) is appended when --scale is used.


Choosing the right mode
-----------------------

Source type                          Recommended mode
------------------------------------+----------------
Scanned B&W negative or print       mono
Scanned color negative or print     color
Color slide (Kodachrome, Ektachrome) color
Digital JPEG (camera, phone)        neutral or color
Archival faithful reproduction      match


Workflow context
----------------

JPEG2DNG is the first step in a pipeline designed for scanned film archival
work. Two typical workflows:

B&W film pipeline:

    Scanner JPEG
        -> jpg_to_cfa_dng.py --mode mono
        -> CFA DNG (16-bit, neutral gray Bayer)
        -> Lightroom Classic: AI Denoise, level, crop, tone neutralisation
        -> LRCNeutralizer (statistical tonal neutralisation written to XMP)
        -> Silver Efex Pro (film emulation, grain, selenium tone)
        -> 16-bit TIFF delivery

Color film pipeline:

    Scanner JPEG
        -> jpg_to_cfa_dng.py --mode color
        -> CFA DNG (16-bit, per-image white balance, sRGB color matrix)
        -> Lightroom Classic: AI Denoise, white balance fine-tune, color grading
        -> 16-bit TIFF delivery

The companion project LRCNeutralizer automates the tonal neutralisation step
for B&W work by analysing exported JPEGs and writing recommended LRC Develop
slider values directly into each DNG's embedded XMP metadata.


Technical notes
---------------

The DNG is built from scratch using Python's struct module — no external DNG
library is required. The TIFF/IFD structure is constructed directly, which
keeps the dependency footprint minimal and makes the format logic fully
transparent and auditable.

Color mode gray-world white balance is computed in linear light (after sRGB
gamma removal), which is the physically correct domain for radiometric
calculations. The result is clipped to a maximum of 1.0 per channel to
prevent AsShotNeutral values outside the valid DNG range.

ColorMatrix in color mode uses the standard IEC 61966-2-1 XYZ D65 to sRGB
matrix, which is the correct inverse transform for sRGB-encoded sources.
This matrix, combined with the per-image AsShotNeutral, gives LRC enough
information to reconstruct the original white balance without applying an
additional camera-profile color transform.

ColorMatrix values in match mode were measured from actual DNG pixel analysis
rather than derived from a camera profile database. They are calibrated for
the sRGB->linear linearisation path used by this script.

The Bayer pattern is RGGB. LRC's demosaic engine handles this pattern
natively for all DNG files.

Output DNGs carry Olympus as Make and OM-1 as Model, which allows
Lightroom's Library and filtering tools to identify and group them, and
associates them with a known camera profile set in LRC.


License
-------

MIT License. Copyright (c) 2025 Phaedrus157. See LICENSE for details.
