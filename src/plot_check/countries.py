"""
Approximate bounding boxes (min lon, min lat, max lon, max lat) for major coffee and cocoa producers.
Enough to catch swapped coordinates and wrong countries; a production deployment would load real
boundaries into PostGIS instead.
"""

BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "BR": (-74.0, -33.8, -34.7, 5.3),
    "CI": (-8.6, 4.3, -2.5, 10.8),
    "CM": (8.4, 1.6, 16.2, 13.1),
    "CO": (-79.1, -4.3, -66.8, 12.5),
    "EC": (-81.1, -5.0, -75.2, 1.7),
    "ET": (33.0, 3.4, 48.0, 15.0),
    "GH": (-3.3, 4.7, 1.2, 11.2),
    "GT": (-92.3, 13.7, -88.2, 17.9),
    "HN": (-89.4, 12.9, -83.1, 16.5),
    "ID": (95.0, -11.0, 141.0, 6.1),
    "KE": (33.9, -4.7, 41.9, 5.0),
    "NG": (2.7, 4.2, 14.7, 13.9),
    "PE": (-81.4, -18.4, -68.7, -0.04),
    "RW": (28.8, -2.9, 30.9, -1.0),
    "TZ": (29.3, -11.8, 40.5, -0.9),
    "UG": (29.5, -1.5, 35.0, 4.3),
    "VN": (102.1, 8.2, 109.5, 23.4),
}


def contains(country: str, lon: float, lat: float) -> bool | None:
    """None when the country isn't known: the check is skipped rather than guessed."""
    box = BOUNDS.get(country.upper())
    if box is None:
        return None
    min_lon, min_lat, max_lon, max_lat = box
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
