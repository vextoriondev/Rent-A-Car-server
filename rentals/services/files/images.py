"""Image validation and optimization (Pillow).

* the file must really decode as JPEG / PNG / WebP and stay under the pixel limit (decompression-bomb guard)
* EXIF orientation is applied, then ALL metadata (GPS included) is dropped by re-encoding
* variants are WebP, longest side limited, never enlarged
* photos keep only the variants; scans of documents also keep the original and use a higher quality, so text stays legible
"""
import io
from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = None   # we enforce our own limit below, before any pixel data is decoded

FORMATS = {'JPEG': ('image/jpeg', 'jpg'), 'PNG': ('image/png', 'png'), 'WEBP': ('image/webp', 'webp')}


class ImageRejected(Exception):
    pass


def inspect(data: bytes):
    """-> (mime, ext, width, height). Raises ImageRejected for anything that is not a safe, decodable image."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format
            w, h = im.size
            if fmt not in FORMATS:
                raise ImageRejected('only_jpeg_png_or_webp_images')
            if w * h > settings.ATTACH_MAX_PIXELS or w < 1 or h < 1:
                raise ImageRejected('the_image_is_too_large_in_pixels')
            im.verify()
    except ImageRejected:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ImageRejected('the_file_is_not_a_valid_image')
    mime, ext = FORMATS[fmt]
    return mime, ext, w, h


def has_gps(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as im:
            exif = im.getexif()
            return bool(exif and exif.get_ifd(0x8825))
    except Exception:
        return False


def _encode(im: Image.Image, quality: int):
    out = io.BytesIO()
    im.save(out, 'WEBP', quality=quality, method=4)
    return out.getvalue()


def build_variants(data: bytes, doc_like: bool):
    """-> (variants, (width, height) of the oriented source). variants: name -> (bytes, width, height).
    Raises ImageRejected if the pixels cannot be decoded."""
    sizes_cfg = dict(settings.ATTACH_VARIANT_SIZES)
    try:
        with Image.open(io.BytesIO(data)) as src:
            ow, oh = src.size
            orientation = src.getexif().get(0x0112, 1)
            if src.format == 'JPEG' and not doc_like:
                # decode a JPEG photo at reduced size straight from the file (much faster for 12 MP phone pictures); still >= the largest variant
                s_ = min(1.0, sizes_cfg['large'] / max(ow, oh))
                src.draft('RGB', (max(1, int(ow * s_) + 1), max(1, int(oh * s_) + 1)))
            im = ImageOps.exif_transpose(src)          # honour the phone's rotation, then metadata is not copied
            im.load()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ImageRejected('the_image_could_not_be_decoded')
    if im.mode not in ('RGB', 'RGBA'):
        im = im.convert('RGBA' if 'A' in im.getbands() or im.info.get('transparency') is not None else 'RGB')
    w0, h0 = (oh, ow) if orientation in (5, 6, 7, 8) else (ow, oh)      # the original's size as displayed (upright)
    cw, ch = im.size                                                   # what we actually hold (equal to the original unless decoded smaller)
    sizes = dict(settings.ATTACH_VARIANT_SIZES)
    quality = settings.ATTACH_DOC_QUALITY if doc_like else settings.ATTACH_PHOTO_QUALITY
    if doc_like:
        sizes['large'] = settings.ATTACH_DOC_LARGE_SIZE
    variants = {}
    for name, longest in sizes.items():
        scale = min(1.0, longest / max(w0, h0))        # never upscale (relative to the ORIGINAL size)
        tw, th = max(1, round(w0 * scale)), max(1, round(h0 * scale))
        v = im if (tw, th) == (cw, ch) else im.resize((tw, th), Image.LANCZOS, reducing_gap=2.0)
        q = quality if name != 'thumb' else min(quality, 75)
        variants[name] = (_encode(v, q), v.size[0], v.size[1])
    return variants, (w0, h0)


def strip_metadata(data: bytes, mime: str):
    """Re-encode an original scan without metadata at high quality (only used when GPS data is present)."""
    with Image.open(io.BytesIO(data)) as src:
        im = ImageOps.exif_transpose(src)
        im.load()
        out = io.BytesIO()
        if mime == 'image/jpeg':
            im.convert('RGB').save(out, 'JPEG', quality=95, subsampling=0)
        elif mime == 'image/png':
            im.save(out, 'PNG', optimize=True)
        else:
            im.save(out, 'WEBP', quality=95, method=6)
        return out.getvalue()
