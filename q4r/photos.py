"""Local, metadata-free reference PNGs. No recognition, matching or upload."""
import base64
import binascii
import hashlib
import struct
import zlib


MAX_PHOTO_BYTES = 6 * 1024 * 1024
SIGNATURE = b"\x89PNG\r\n\x1a\n"


def clean_reference_png(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_PHOTO_BYTES or not raw.startswith(SIGNATURE):
        raise ValueError("Choose a PNG reference image under 6 MB")
    offset, chunks, compressed, width, height = 8, [], [], 0, 0
    color = None
    ended = False
    while offset < len(raw):
        if offset + 12 > len(raw):
            raise ValueError("Incomplete PNG")
        size = struct.unpack(">I", raw[offset:offset+4])[0]
        kind = raw[offset+4:offset+8]
        end = offset+12+size
        if end > len(raw):
            raise ValueError("Incomplete PNG chunk")
        data = raw[offset+8:offset+8+size]
        crc = struct.unpack(">I", raw[offset+8+size:end])[0]
        if binascii.crc32(kind+data) & 0xffffffff != crc:
            raise ValueError("Corrupt PNG")
        if kind == b"IHDR":
            if chunks or size != 13:
                raise ValueError("Invalid PNG header")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if not 1 <= width <= 2048 or not 1 <= height <= 2048 or depth != 8 or color not in (2, 6) or compression or filtering or interlace:
                raise ValueError("Reference image must be a non-interlaced RGB PNG, up to 2048 pixels per side")
        elif kind == b"IDAT":
            if not chunks:
                raise ValueError("Missing PNG header")
            compressed.append(data)
        elif kind == b"IEND":
            if size:
                raise ValueError("Invalid PNG ending")
            ended = True
        elif kind[:1].isupper():
            raise ValueError("Unsupported PNG format")
        # Discard text, EXIF, timestamps, profiles, GPS and all ancillary chunks.
        if kind in (b"IHDR", b"IDAT", b"IEND"):
            chunks.append(raw[offset:end])
        offset = end
        if ended:
            break
    if not ended or not compressed or offset != len(raw):
        raise ValueError("Incomplete reference image")
    expected = height * (1 + width*(3 if color == 2 else 4))
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(b"".join(compressed), expected+1)
    except zlib.error as exc:
        raise ValueError("Invalid image pixels") from exc
    if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("Invalid image dimensions")
    return SIGNATURE + b"".join(chunks), width, height


def decode_photo(value):
    if not value:
        return None
    if not isinstance(value, str) or len(value) > MAX_PHOTO_BYTES*4//3+128 or not value.startswith("data:image/png;base64,"):
        raise ValueError("Invalid reference image")
    try:
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid reference image encoding") from exc
    return clean_reference_png(raw)


def attach_photo(case, image):
    raw, width, height = image
    folder = case.directory / "attachments"
    folder.mkdir(mode=0o700, exist_ok=True)
    (folder / "reference.png").write_bytes(raw)
    case.attachments.append(dict(file="attachments/reference.png", mime="image/png", width=width, height=height,
                                 sha256=hashlib.sha256(raw).hexdigest(), purpose="User supplied visual reference only; no identity matching or upload"))
