"""What a map shows to one reader: visible locations, the route and the party."""
from .models import MapLocation


def map_data(map_, is_master, until_session=None):
    locations = list(map_.locations.all())
    if not is_master:
        locations = [loc for loc in locations if loc.status != MapLocation.Status.OCULTO]
    moves = list(map_.moves.select_related("location", "session"))
    if until_session is not None:
        moves = [m for m in moves if m.session is None or m.session.number <= until_session]
    route = " ".join(f"{m.location.x},{m.location.y}" for m in moves)
    return {
        "map": map_,
        "locations": locations,
        "moves": moves,
        "route": route if len(moves) > 1 else "",
        "party": moves[-1].location if moves else None,
    }


def party_location(campaign):
    """Where the party is: the last move on the first map with a route."""
    for map_ in campaign.maps.all():
        last = map_.moves.select_related("location").last()
        if last:
            return last.location
    return None
