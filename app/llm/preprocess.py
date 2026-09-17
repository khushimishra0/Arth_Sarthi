"""What happens to an upload before it costs anything.

Three cost controls from §3, and one privacy control that happens to live in the
same place.

**Downscale.** Screenshots go to 1568px on the long edge. Larger images cost more
and read no better — a phone screenshot at 1568px contains every pixel of text the
model needs, and the original 4000px photo of that screenshot contains the same
words at four times the price.

**EXIF strip.** A photo *of* a phone screen carries the GPS coordinates of wherever
it was taken, usually somebody's house. §4 lists stripping it as step 1 of ingest;
it is done here by rebuilding the image from raw pixels, which drops every metadata
block rather than the one we remembered to name.

**Text before vision.** A digital loan PDF goes through pdfplumber for free. Only a
scan reaches the vision model. `pdf_text_yield` decides which one it is.
"""

from __future__ import annotations

import io
import logging

__all__ = [
    "ImagePrepError",
    "PdfReadError",
    "pdf_text",
    "pdf_text_yield",
    "prepare_image",
]

log = logging.getLogger(__name__)

JPEG_QUALITY = 85


class ImagePrepError(ValueError):
    """The upload was not a readable image. The user gets "send a clearer photo"."""


class PdfReadError(ValueError):
    """The upload was not a readable PDF."""


def prepare_image(data: bytes, max_edge: int) -> bytes:
    """Downscale to `max_edge`, strip all metadata, return JPEG bytes.

    Small images are still re-encoded rather than passed through: the re-encode is
    what removes the EXIF, and skipping it for a 900px photo would quietly keep the
    GPS tag on exactly the small screenshots most people send.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            # Apply the orientation tag before discarding it, or a photo taken in
            # portrait arrives at the model sideways and reads as gibberish.
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            image.thumbnail((max_edge, max_edge), Image.LANCZOS)

            # Rebuild from raw pixels. Anything the decoder was carrying — EXIF,
            # GPS, ICC, XMP, the maker note — does not survive this line.
            clean = Image.frombytes(image.mode, image.size, image.tobytes())

            buffer = io.BytesIO()
            clean.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImagePrepError(f"could not read this as an image: {exc}") from exc


def pdf_text(data: bytes) -> tuple[str, int]:
    """Extract the embedded text layer. Returns `(text, page_count)`.

    Free. No model involved. A digital sanction letter is fully readable this way,
    and every rupee not spent here is a rupee left for the scans that need eyes.
    """
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = pdf.pages
            extracted = "\n".join((page.extract_text() or "") for page in pages)
            return extracted, len(pages)
    except Exception as exc:  # pdfplumber raises a wide variety on malformed input
        raise PdfReadError(f"could not read this as a PDF: {exc}") from exc


def pdf_text_yield(data: bytes) -> tuple[str, int, float]:
    """`(text, page_count, chars_per_page)` — the number that routes the document.

    Above the configured threshold it is a digital PDF and the text is enough.
    Below it, the pages are images of text and only a vision model can read them.
    A scanned page usually yields single digits; a digital one yields hundreds.
    """
    text, pages = pdf_text(data)
    if pages == 0:
        return text, 0, 0.0
    return text, pages, len(text.strip()) / pages
