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

Three modes are available, selected with the --mode flag:

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
    weighting), normalises luma to fill the full 0–65535 16-bit range,
    then maps the identical luma value to all four Bayer positions
    (R=G1=G2=B=Y). This eliminates the color fringing and false color
    artifacts that occur when a demosaic algorithm processes a signal
    with zero inter-channel variation. Output is a true neutral-gray
    DNG. Adobe Monochrome profile and all B&W-specific processing tools
    in LRC are fully available.


Bit depth
---------

All output DNGs are 16-bit regardless of mode. The source JPEG contains
256 distinct tonal levels (8-bit). Storing these in a 16-bit container
provides precision headroom for LRC's non-linear processing — tone curves,
masks, parametric adjustments — and prevents quantization banding in smooth
gradients during heavy Develop work. No tonal data is invented or invented;
the 16-bit values are a scaled representation of the original 8-bit source.

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

Monochrome mode for B&W scans (recommended for scanned film):

    py jpg_to_cfa_dng.py scan.jpg --mode mono

Match mode for archival faithful reproduction:

    py jpg_to_cfa_dng.py scan.jpg --mode match

Half-resolution output for upload or analysis (useful for large scans):

    py jpg_to_cfa_dng.py scan.jpg --mode mono --scale 0.5

The output DNG is written to the same folder as the source JPEG with the
same base filename. A scale tag (_x50) is appended when --scale is used.


Workflow context
----------------

JPEG2DNG is the first step in a pipeline designed for scanned B&W film
archival work:

    Scanner JPEG
        → jpg_to_cfa_dng.py --mode mono
        → CFA DNG (16-bit, neutral gray Bayer)
        → Lightroom Classic: AI Denoise, level, crop, heal, tone neutralization
        → LRCNeutralizer (statistical tonal neutralization written to XMP)
        → Silver Efex Pro (film emulation, selenium tone, grain)
        → 16-bit TIFF delivery

The companion project LRCNeutralizer automates the tonal neutralization step
by analyzing exported JPEGs and writing recommended LRC Develop slider values
directly into each DNG's embedded XMP metadata.


Technical notes
---------------

The DNG is built from scratch using Python's struct module — no external DNG
library is required. The TIFF/IFD structure is constructed directly, which
keeps the dependency footprint minimal and makes the format logic fully
transparent and auditable.

ColorMatrix values in match mode were measured from actual DNG pixel analysis
rather than derived from a camera profile database. They are calibrated for
the sRGB→linear linearisation path used by this script.

The Bayer pattern is RGGB. LRC's demosaic engine handles this pattern
natively for all DNG files.

Output DNGs carry PhaedrusMedia as Make and JPEG2DNG as Model, which allows
Lightroom's Library and filtering tools to identify and group them.


License
-------

MIT License. Copyright (c) 2025 Phaedrus157. See LICENSE for details.
