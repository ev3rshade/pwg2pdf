import sys
import struct
import zlib


PAGE_HEADER_SIZE = 1796


def parse_header(header, endian):
    def u32(pos):
        return struct.unpack(endian + "I", header[pos:pos + 4])[0]

    return {
        "res_x": u32(276) or 360,
        "res_y": u32(280) or 360,
        "width": u32(372),
        "height": u32(376),
        "bits_per_color": u32(384),
        "bits_per_pixel": u32(388),
        "bytes_per_line": u32(392),
        "color_space": u32(400),
        "num_colors": u32(420),
    }


def decode_page_raster(data, offset, hdr):
    # PWG PackBits decode. Returns (raster_bytes, new_offset).
    width = hdr["width"]
    height = hdr["height"]
    bytes_per_line = hdr["bytes_per_line"]
    pixel_bytes = hdr["bits_per_pixel"] // 8

    raster = bytearray()
    line_count = 0

    while line_count < height:
        if offset >= len(data):
            break
        line_repeat = data[offset] + 1
        offset += 1

        line = bytearray()
        while len(line) < bytes_per_line:
            if offset >= len(data):
                break
            ctrl = data[offset]
            offset += 1
            if ctrl == 128:
                break
            elif ctrl < 128:
                count = ctrl + 1
                px = data[offset:offset + pixel_bytes]
                offset += pixel_bytes
                line.extend(px * count)
            else:
                count = 257 - ctrl
                chunk = data[offset:offset + pixel_bytes * count]
                offset += pixel_bytes * count
                line.extend(chunk)

        if len(line) < bytes_per_line:
            line.extend(b"\x00" * (bytes_per_line - len(line)))
        else:
            line = line[:bytes_per_line]

        for _ in range(line_repeat):
            raster.extend(line)
            line_count += 1
            if line_count >= height:
                break

    return bytes(raster), offset


def raster_to_rgb(raster, hdr):
    w = hdr["width"]
    h = hdr["height"]
    bpp = hdr["bits_per_pixel"] // 8
    ncolors = hdr["num_colors"]

    if ncolors >= 3 and bpp == 3:
        # already RGB, use directly
        needed = w * h * 3
        if len(raster) >= needed:
            return raster[:needed]
        return raster + b"\x00" * (needed - len(raster))

    out = bytearray(w * h * 3)
    src = 0
    for i in range(w * h):
        if src + bpp > len(raster):
            break
        if ncolors == 1:
            g = raster[src]
            out[i * 3] = g
            out[i * 3 + 1] = g
            out[i * 3 + 2] = g
        else:
            out[i * 3] = raster[src]
            out[i * 3 + 1] = raster[src + 1]
            out[i * 3 + 2] = raster[src + 2]
        src += bpp
    return bytes(out)


class PdfWriter:
    # Streams objects to disk so memory stays flat.
    def __init__(self, f):
        self.f = f
        self.offsets = []  # byte offset of each object, index 0 = obj 1
        self.page_refs = []
        self.f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        # object 1 = catalog, object 2 = pages tree: reserved, written last.
        self._next_obj = 3

    def _write_obj(self, num, body):
        # Pad offsets list so index num-1 exists.
        while len(self.offsets) < num:
            self.offsets.append(0)
        self.offsets[num - 1] = self.f.tell()
        self.f.write(b"%d 0 obj\n" % num)
        self.f.write(body)
        self.f.write(b"\nendobj\n")

    def add_page(self, rgb, w, h, res_x, res_y):
        img_num = self._next_obj
        content_num = self._next_obj + 1
        page_num = self._next_obj + 2
        self._next_obj += 3

        img_data = zlib.compress(rgb)
        img_dict = (
            b"<< /Type /XObject /Subtype /Image "
            b"/Width %d /Height %d "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
            b"/Filter /FlateDecode /Length %d >>\nstream\n"
            % (w, h, len(img_data))
        )
        self._write_obj(img_num, img_dict + img_data + b"\nendstream")

        pw = w * 72.0 / res_x
        ph = h * 72.0 / res_y
        content = b"q\n%.2f 0 0 %.2f 0 0 cm\n/Im0 Do\nQ\n" % (pw, ph)
        content_stream = (
            b"<< /Length %d >>\nstream\n" % len(content)
            + content + b"\nendstream"
        )
        self._write_obj(content_num, content_stream)

        page_dict = (
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 %.2f %.2f] "
            b"/Resources << /XObject << /Im0 %d 0 R >> >> "
            b"/Contents %d 0 R >>"
            % (pw, ph, img_num, content_num)
        )
        self._write_obj(page_num, page_dict)
        self.page_refs.append(page_num)

    def finish(self):
        kids = b" ".join(b"%d 0 R" % n for n in self.page_refs)
        pages_body = (
            b"<< /Type /Pages /Count %d /Kids [ %s ] >>"
            % (len(self.page_refs), kids)
        )
        self._write_obj(2, pages_body)
        self._write_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")

        xref_pos = self.f.tell()
        n = len(self.offsets) + 1
        self.f.write(b"xref\n0 %d\n" % n)
        self.f.write(b"0000000000 65535 f \n")
        for off in self.offsets:
            self.f.write(b"%010d 00000 n \n" % off)
        self.f.write(
            b"trailer\n<< /Size %d /Root 1 0 R >>\n" % n
        )
        self.f.write(b"startxref\n%d\n%%%%EOF\n" % xref_pos)


def main():
    if len(sys.argv) != 3:
        print("Usage: python prn2pdf.py input.prn output.pdf")
        sys.exit(1)

    with open(sys.argv[1], "rb") as f:
        data = f.read()

    sync = data[0:4]
    if sync == b"RaS2":
        endian = ">"
    elif sync == b"2SaR":
        endian = "<"
    else:
        print("Not a PWG Raster file. First bytes: %r" % data[:16])
        sys.exit(1)

    offset = 4
    page_index = 0

    with open(sys.argv[2], "wb") as out_f:
        writer = PdfWriter(out_f)

        while offset < len(data):
            if offset != 4:
                next_hdr = data.find(b"PwgRaster", offset)
                if next_hdr < 0:
                    break
                offset = next_hdr
            if offset + PAGE_HEADER_SIZE > len(data):
                break

            header = data[offset:offset + PAGE_HEADER_SIZE]
            offset += PAGE_HEADER_SIZE
            hdr = parse_header(header, endian)

            if hdr["width"] == 0 or hdr["height"] == 0:
                break

            raster, offset = decode_page_raster(data, offset, hdr)
            rgb = raster_to_rgb(raster, hdr)
            writer.add_page(
                rgb, hdr["width"], hdr["height"],
                hdr["res_x"], hdr["res_y"]
            )
            page_index += 1
            print("Decoded page %d" % page_index)

        writer.finish()

    print("Wrote %s (%d pages)" % (sys.argv[2], page_index))


if __name__ == "__main__":
    main()