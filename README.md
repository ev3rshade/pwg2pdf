# pwg2pdf

made for my cousin's research (lol)


Convert Epson PWG Raster print-spool files (`.prn`) into PDF.

When you "print to file" on an Epson printer such as the EW-M757T (EPSON5D5ACE)
using its driver, the spool file is not ESC/P — it is **PWG Raster**, a
standardized bitmap format identified by the magic bytes `RaS2PwgRaster` at the
start of the file. `pwg2pdf` parses that format directly, with no external
libraries, and produces a PDF with one image page per raster page.

## Requirements

- Python 3.6 or newer
- No third-party packages (uses only the standard library: `struct`, `zlib`)

## Usage

```
python pwg2pdf.py input.prn output.pdf
```

Example:

```
python pwg2pdf.py pdf.prn pdf.pdf
```

The program prints progress as it decodes each page:

```
Decoded page 1
Decoded page 2
...
Wrote pdf.pdf (114 pages)
```

## How it works

1. **Sync word.** The file begins with a 4-byte sync word (`RaS2` for
   big-endian, `2SaR` for little-endian) that sets the byte order for all
   header fields.

2. **Per-page headers.** Each page has a 1796-byte header that begins with the
   ASCII string `PwgRaster`. The first page's header sits immediately after the
   sync word; every later page is located by scanning forward to the next
   `PwgRaster` marker. Key fields read from each header:
   - resolution (x, y) in DPI
   - width and height in pixels
   - bits per color / bits per pixel
   - bytes per line
   - number of colors

3. **Raster decode.** Each page's pixel data is compressed with PWG's PackBits
   variant. For every scan line, a leading byte gives how many times the line
   repeats, then control bytes describe runs: values below 128 repeat a single
   pixel, values above 128 introduce a block of literal pixels.

4. **PDF assembly.** Each decoded page becomes a Flate-compressed `DeviceRGB`
   image XObject placed on its own PDF page, sized from the raster dimensions
   and DPI. Objects are streamed to disk one page at a time so memory stays
   roughly flat rather than growing with page count.

## Format assumptions

This converter was written against a specific Epson PWG Raster file and makes a
few assumptions that hold for that output:

- 24-bit RGB pixel data (3 bytes per pixel), `DeviceRGB` color space
- Header field offsets follow the PWG Raster 1.0 layout, measured from the
  start of the `PwgRaster` string
- 360 DPI default if the resolution field is absent

Grayscale (1 color) input is also handled. **CMYK input is not** — if your
printer emits CMYK raster, the color conversion branch would need to be added.

## Troubleshooting

**"Not a PWG Raster file."**
The input does not start with `RaS2` or `2SaR`. It may be ESC/P or another
spool format, which this tool does not handle.

**Output image is sheared into diagonal streaks.**
The PackBits control-byte interpretation is inverted for your file. Below-128
must repeat one pixel and above-128 must copy literal pixels; if yours is the
opposite, swap those two branches in `decode_page_raster`.

**Only one page comes out, or it stops early.**
Page boundaries are found by seeking to each `PwgRaster` marker. If it stops at
page N, that page's header likely has an unexpected width/height of zero; check
the header fields for that page.

**MemoryError.**
The whole input file is read into memory at once. For very large files on
low-memory machines, the read itself is the limit; decoded pages are already
freed one at a time.

**Very slow.**
PackBits decoding runs in pure Python. Hundreds of pages can take several
minutes. Decoding could be vectorized with NumPy if speed matters.

## Files

- `pwg2pdf.py` — the converter
- `README.md` — this file