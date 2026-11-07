"""Validate physical cross-tape boundaries and slice a 313-DPI raster safely."""
from io import BytesIO
import math

from fastapi import HTTPException
from PIL import Image

DPI = 313
MIN_SEGMENT_MM = 10
MAX_MARKERS = 15


def validate_cuts(cuts: list[float], length_mm: float, cut_mode: str) -> list[float]:
    if len(cuts) > MAX_MARKERS:
        raise HTTPException(422, f'At most {MAX_MARKERS} cut markers are supported')
    if cuts and cut_mode == 'none':
        raise HTTPException(422, 'Cut markers require full or half cut')
    boundaries = [0.0, *cuts, length_mm]
    if not math.isfinite(length_mm) or any(not math.isfinite(value) for value in cuts):
        raise HTTPException(422, 'Cut positions must be finite')
    if any(right - left < MIN_SEGMENT_MM - 1e-6 for left, right in zip(boundaries, boundaries[1:])):
        raise HTTPException(422, f'Each segment must be at least {MIN_SEGMENT_MM} mm and markers must be ordered')
    return boundaries


def split_raster(png: bytes, length_mm: float, cuts: list[float], cut_mode: str) -> list[bytes]:
    """Split along label length X, without dropping or duplicating a pixel column."""
    boundaries = validate_cuts(cuts, length_mm, cut_mode)
    if not cuts:
        return [png]
    with Image.open(BytesIO(png)) as image:
        pixel_edges = [round(mm * DPI / 25.4) for mm in boundaries[:-1]] + [image.width]
        pieces = []
        for left, right in zip(pixel_edges, pixel_edges[1:]):
            if right <= left:
                raise HTTPException(422, 'Cut markers are too close at printer resolution')
            output = BytesIO()
            image.crop((left, 0, right, image.height)).save(output, format='PNG')
            pieces.append(output.getvalue())
    return pieces
