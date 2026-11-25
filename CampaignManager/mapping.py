"""What a map shows to one reader: visible locations, the route and the party."""
import math

from .models import MapLocation

MAX_PATH_POINTS = 300


def clean_path(path):
    """The stored waypoints of a stretch, as (x, y) floats in 0..100. Anything else is dropped."""
    points = []
    for point in path if isinstance(path, list) else []:
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if math.isfinite(x) and math.isfinite(y) and 0 <= x <= 100 and 0 <= y <= 100:
            points.append((x, y))
    return points[:MAX_PATH_POINTS]


def parse_path(text):
    """Waypoints sent by the route editor: "x,y x,y ..." in % of the image. None if not valid."""
    points = []
    for pair in (text or "").split():
        try:
            x, y = (float(v) for v in pair.split(","))
        except ValueError:
            return None
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= 100 and 0 <= y <= 100):
            return None
        points.append([round(x, 2), round(y, 2)])
    return points if len(points) <= MAX_PATH_POINTS else None


def _fmt(value):
    return f"{round(value, 2):g}"


def route_segments(moves):
    """One stretch per move after the first: from the previous stop, through its waypoints, to
    this stop. The arrow sits on the last part of the stretch that has some length, pointing
    to the stop (dx, dy: direction of that part, in % of the image)."""
    segments = []
    for previous, move in zip(moves, moves[1:]):
        points = [(previous.location.x, previous.location.y), *clean_path(move.path), (move.location.x, move.location.y)]
        arrow = None
        for (ax, ay), (bx, by) in reversed(list(zip(points, points[1:]))):
            if abs(bx - ax) > 0.01 or abs(by - ay) > 0.01:
                arrow = {"x": _fmt(bx), "y": _fmt(by), "dx": _fmt(bx - ax), "dy": _fmt(by - ay)}
                break
        segments.append({"move": move, "start": previous.location, "arrow": arrow,
                         "points": " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in points)})
    return segments


def map_data(map_, is_master, until_session=None, since_session=None):
    """Locations and the route. With `since_session`, only the way from where the party was
    when that session began (its last stop before it) to the end of `until_session`."""
    locations = list(map_.locations.all())
    if not is_master:
        locations = [loc for loc in locations if loc.status != MapLocation.Status.OCULTO]
    # The secondary places first: drawn later, the main ones stay on top where pins overlap.
    locations.sort(key=lambda loc: loc.importance == MapLocation.Importance.PRINCIPAL)
    moves = list(map_.moves.select_related("location", "session"))
    if until_session is not None:
        moves = [m for m in moves if m.session is None or m.session.number <= until_session]
    if since_session is not None and since_session > 1:
        before = [m for m in moves if m.session is not None and m.session.number < since_session]
        start = [before[-1]] if before else []
        moves = start + [m for m in moves if m.session is None or m.session.number >= since_session]
    return {
        "map": map_,
        "locations": locations,
        "moves": moves,
        "segments": route_segments(moves),
        "party": moves[-1].location if moves else None,
    }


def party_location(campaign):
    """Where the party is: the last move on the first map with a route."""
    for map_ in campaign.maps.all():
        last = map_.moves.select_related("location").last()
        if last:
            return last.location
    return None
