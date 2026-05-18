"""
Photo upload pipeline · all the safety we need before a foreign byte
hits disk.

The order of operations matters and is intentional:

    1. Read the file into memory once (cheap; size is already capped
       by Flask's MAX_CONTENT_LENGTH at request level)
    2. Hard cap on individual file size  (cheapest reject)
    3. Magic-byte sniff via libmagic     (the real type, not the claim)
    4. MIME whitelist                    (images only, never video)
    5. Storage quota check               (don't bust the disk)
    6. Decode with Pillow                (rejects corrupted files)
    7. EXIF auto-orient                  (iPhone portrait rotation)
    8. Strip GPS by re-encoding          (privacy: never publish coords)
    9. Downscale if huge                 (save disk + bandwidth)
   10. Save as JPEG + generate thumbnail
"""
from __future__ import annotations

import io
import os
import uuid
from dataclasses import dataclass
from typing import Optional

import magic
import pillow_heif
from PIL import Image, ImageOps

# Register HEIC/HEIF support so Image.open() handles iPhone photos
pillow_heif.register_heif_opener()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_PATH = os.environ.get("DATA_PATH", "/data")
PHOTOS_DIR = os.path.join(DATA_PATH, "photos")
THUMBS_DIR = os.path.join(DATA_PATH, "thumbs")

STORAGE_QUOTA_GB = int(os.environ.get("STORAGE_QUOTA_GB", "20"))
STORAGE_QUOTA_BYTES = STORAGE_QUOTA_GB * 1024 * 1024 * 1024
# Warn admin between 95% and 100% so we hear about it before it's a problem
STORAGE_WARN_BYTES = int(STORAGE_QUOTA_BYTES * 0.95)

# Per-file cap. iPhones can produce 5-10MB shots; 25MB gives headroom
# without letting someone send a 200MB file.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Image dimensions
MAX_DIMENSION = 2400      # longest side of stored photo
THUMB_WIDTH = 480         # masonry grid thumbnail width

# JPEG quality
ORIG_QUALITY = 88
THUMB_QUALITY = 82

# Explicit image-only whitelist. Anything detected outside this set is
# rejected · including ALL video MIME types.
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}


# ---------------------------------------------------------------------------
# Exceptions · caught by the route to render friendly Dutch messages
# ---------------------------------------------------------------------------

class PhotoError(Exception):
    """Base class for any user-visible upload error."""


class FileTooLarge(PhotoError):
    pass


class UnsupportedFormat(PhotoError):
    pass


class CorruptImage(PhotoError):
    pass


class StorageFull(PhotoError):
    pass


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class SavedPhoto:
    filename: str
    thumb_filename: str
    width: int
    height: int
    file_size: int


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    for d in (PHOTOS_DIR, THUMBS_DIR):
        os.makedirs(d, exist_ok=True)


def get_storage_used_bytes() -> int:
    """Total size of stored photos + thumbnails, in bytes."""
    total = 0
    for d in (PHOTOS_DIR, THUMBS_DIR):
        if not os.path.isdir(d):
            continue
        for entry in os.scandir(d):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    return total


def storage_warning_level() -> Optional[str]:
    """Return 'full' if over quota, 'warn' if approaching, None otherwise."""
    used = get_storage_used_bytes()
    if used >= STORAGE_QUOTA_BYTES:
        return "full"
    if used >= STORAGE_WARN_BYTES:
        return "warn"
    return None


def save_photo(file_storage) -> SavedPhoto:
    """
    Run the full pipeline on a single werkzeug FileStorage.

    Raises a PhotoError subclass on any failure · never writes to disk
    unless every check passes.
    """
    ensure_dirs()

    raw = file_storage.read()
    size = len(raw)

    # 1. Size cap
    if size == 0:
        raise CorruptImage("Het bestand lijkt leeg te zijn.")
    if size > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise FileTooLarge(
            f"Dit bestand is groter dan {mb} MB. "
            "Probeer 'm kleiner te maken of stuur een andere foto."
        )

    # 2. Magic-byte sniff · the real MIME type, not the claim
    detected_mime = magic.from_buffer(raw, mime=True)

    # 3. Whitelist
    if detected_mime not in ALLOWED_MIME_TYPES:
        if detected_mime.startswith("video/"):
            raise UnsupportedFormat(
                "Video's zijn (nog) niet toegestaan · alleen foto's."
            )
        raise UnsupportedFormat(
            f"Dit bestandstype ({detected_mime}) wordt niet ondersteund. "
            "Stuur een foto in JPG, PNG, WEBP of HEIC formaat."
        )

    # 4. Quota check (pessimistic: based on raw size, real save is smaller)
    used = get_storage_used_bytes()
    if used + size > STORAGE_QUOTA_BYTES:
        raise StorageFull(
            "Het fotoalbum zit vol · de beheerder krijgt automatisch een seintje. "
            "Probeer het later opnieuw."
        )

    # 5. Decode with Pillow
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()  # force decode now so we catch corruption here
    except Exception as exc:
        raise CorruptImage(
            "Deze foto kon niet geopend worden · mogelijk is hij beschadigd."
        ) from exc

    # 6. EXIF auto-orient (iPhone portrait photos arrive sideways)
    img = ImageOps.exif_transpose(img)

    # 7. Convert to RGB so JPEG save works for HEIC, PNG with alpha, etc.
    if img.mode != "RGB":
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            alpha = img.convert("RGBA").split()[-1]
            background.paste(img, mask=alpha)
        else:
            background.paste(img)
        img = background

    # 8. Downscale if larger than MAX_DIMENSION on the long side
    if max(img.size) > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    # 9. Save as JPEG (re-encoding strips EXIF, including GPS coords)
    uid = uuid.uuid4().hex
    filename = f"{uid}.jpg"
    thumb_filename = f"{uid}_thumb.jpg"

    orig_path = os.path.join(PHOTOS_DIR, filename)
    thumb_path = os.path.join(THUMBS_DIR, thumb_filename)

    img.save(orig_path, "JPEG", quality=ORIG_QUALITY, optimize=True)

    # 10. Generate proportional thumbnail
    ratio = THUMB_WIDTH / img.width
    thumb_h = max(1, int(img.height * ratio))
    thumb = img.resize((THUMB_WIDTH, thumb_h), Image.LANCZOS)
    thumb.save(thumb_path, "JPEG", quality=THUMB_QUALITY, optimize=True)

    return SavedPhoto(
        filename=filename,
        thumb_filename=thumb_filename,
        width=img.width,
        height=img.height,
        file_size=os.path.getsize(orig_path),
    )


def delete_files(filename: str, thumb_filename: Optional[str]) -> None:
    """Hard-delete the actual files from disk. Used by the admin trash purge."""
    for path in (
        os.path.join(PHOTOS_DIR, filename),
        os.path.join(THUMBS_DIR, thumb_filename) if thumb_filename else None,
    ):
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass


def directory_size_bytes() -> int:
    """Som van alle bestanden in PHOTOS_DIR + THUMBS_DIR.
    Gebruikt door admin-dashboard om disk-gebruik te tonen.
    Lekker simpel: één os.walk per dir, geen recursie-acrobatiek."""
    total = 0
    for base in (PHOTOS_DIR, THUMBS_DIR):
        if not os.path.isdir(base):
            continue
        for entry in os.scandir(base):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    return total
