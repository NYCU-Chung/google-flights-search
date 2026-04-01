"""
Google Flights tfs URL parameter builder.
Builds URL-safe base64-encoded protobuf for the Google Flights search endpoint.
"""

from __future__ import annotations
import base64 as _b64

from ._utils import _varint, _field_varint, _field_len

# Google Flights city/metro entity IDs for airports that need city-level search.
# Format: IATA -> entity_id (entity_type=2 = city/metro area)
# Regular airports use IATA + entity_type=1 (handled automatically).
CITY_ENTITIES: dict[str, str] = {
    # Browser observation (2026-04-01): RMQ uses entity_type=1 (IATA) in Google Flights frontend.
    # Encoding RMQ as entity_type=2 + city entity causes Google to return empty results.
    # KHH: entity_type=1 works correctly
    # TSA: entity_type=1 works correctly
}

# field 16 sub-message: tells Google to run on-demand calculation for low-traffic airports
# observed bytes: 08 ff ff ff ff ff ff ff ff ff 01 (varint -1 at field 1)
_FIELD16_ALL_RESULTS = b'\x08' + b'\xff' * 9 + b'\x01'


def _airport_bytes(iata_or_entity: str) -> bytes:
    if iata_or_entity in CITY_ENTITIES:
        entity_id   = CITY_ENTITIES[iata_or_entity]
        entity_type = 2   # city/metro
    else:
        entity_id   = iata_or_entity.upper()
        entity_type = 1   # airport
    return _field_varint(1, entity_type) + _field_len(2, entity_id.encode())


def _flight_data_bytes_with_selection(date: str, frm: str, to: str, flight_id: str) -> bytes:
    """Flight data with field 4 (selected flight) embedded — same layout as build_tfs_selected.

    flight_id must be a single carrier+number string (e.g. "CI107").
    Pipe-separated connecting flights (e.g. "AA123|BA456") are NOT supported;
    callers must filter for direct/single-segment flights before calling this function.
    """
    if "|" in flight_id or len(flight_id) < 3:
        raise ValueError(
            f"flight_id must be a single carrier+number (e.g. 'CI107'), got: {flight_id!r}. "
            "Pipe-separated connecting flights are not supported."
        )
    carrier   = flight_id[:2]
    flight_no = flight_id[2:]
    sel = (
        _field_len(1, frm.encode())
        + _field_len(2, date.encode())
        + _field_len(3, to.encode())
        + _field_len(5, carrier.encode())
        + _field_len(6, flight_no.encode())
    )
    return (
        _field_len(2,  date.encode())
        + _field_len(4,  sel)
        + _field_len(13, _airport_bytes(frm))
        + _field_len(14, _airport_bytes(to))
    )


def _flight_data_bytes(date: str, frm: str, to: str) -> bytes:
    return (
        _field_len(2,  date.encode())           # date
        + _field_len(13, _airport_bytes(frm))   # from_airport
        + _field_len(14, _airport_bytes(to))    # to_airport
    )


def build_tfs_multi_city_partial(
    segments: list[dict],
    selections: dict[int, str],   # {leg_idx: "CI107", ...} — legs already chosen
    seat: int = 1,
    adults: int = 1,
) -> str:
    """
    Build a multi-city tfs with field 4 (flight selection) embedded for chosen legs.

    Used for step-by-step GSR: after selecting legs 0..K-1, build a tfs with
    field 4 in those legs' FlightData blocks so Google returns leg-K options.

    selections maps leg index → flight-id string (e.g. {0: "CI107", 1: "CI915"}).
    Unselected legs use plain _flight_data_bytes (no field 4).
    """
    info = _field_varint(1, 28) + _field_varint(2, 2)
    for i, seg in enumerate(segments):
        frm = seg["from"].upper()
        to  = seg["to"].upper()
        if i in selections:
            fd = _flight_data_bytes_with_selection(seg["date"], frm, to, selections[i])
        else:
            fd = _flight_data_bytes(seg["date"], frm, to)
        info += _field_len(3, fd)
    for _ in range(adults):
        info += _field_varint(8, 1)
    info += _field_varint(9, seat)
    info += _field_varint(14, 1)
    info += _field_len(16, _FIELD16_ALL_RESULTS)
    info += _field_varint(19, 3)   # MULTI_CITY
    return _b64.urlsafe_b64encode(info).rstrip(b'=').decode('ascii')


def build_tfs(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    seat: int = 1,      # 1=economy, 2=premium-economy, 3=business, 4=first
    adults: int = 1,
) -> str:
    """
    Build the Google Flights `tfs` URL parameter
    (URL-safe base64-encoded protobuf).

    Differences from fast_flights default:
    1. Airport message includes field 1 (entity type): 1=airport IATA, 2=city entity ID
    2. Info message includes fields 1=28, 2=2 (query type flags)
    3. Info field 16 contains 0xFFFF…FF (all-results flag, required for small airports)
    These fields cause Google to perform on-demand calculation for low-traffic airports,
    returning data[3] list instead of null.
    """
    info = (
        _field_varint(1, 28)   # query type flag
        + _field_varint(2, 2)  # query type flag
        + _field_len(3, _flight_data_bytes(departure_date, origin, destination))
    )

    if return_date:
        info += _field_len(3, _flight_data_bytes(return_date, destination, origin))

    for _ in range(adults):
        info += _field_varint(8, 1)     # Passenger.ADULT = 1

    info += _field_varint(9, seat)      # seat class
    info += _field_varint(14, 1)        # display settings flag
    info += _field_len(16, _FIELD16_ALL_RESULTS)
    info += _field_varint(19, 1 if return_date else 2)  # trip type

    return _b64.urlsafe_b64encode(info).rstrip(b'=').decode('ascii')


def build_tfs_selected(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str,
    flight_id: str,          # e.g. "JX316"; pipe-joined connecting flights not supported
    seat: int = 1,
    adults: int = 1,
) -> str:
    """
    Build a round-trip tfs with an explicit flight selection embedded in the
    outbound FlightData (field 4).  Google uses this to identify which outbound
    leg the user has picked so it can return the matching return-leg options.

    flight_id must be a plain carrier+number string (e.g. "JX316").
    Pipe-separated connecting flights are NOT supported; callers should
    filter for direct flights before calling this function.

    Field 4 sub-message layout (from observed browser traffic):
      field 1 (str): origin IATA
      field 2 (str): outbound date
      field 3 (str): destination IATA
      field 5 (str): carrier IATA (first 2 chars of flight_id)
      field 6 (str): flight number  (chars 2 onwards)
    """
    carrier   = flight_id[:2]
    flight_no = flight_id[2:]

    sel = (
        _field_len(1, origin.encode())
        + _field_len(2, outbound_date.encode())
        + _field_len(3, destination.encode())
        + _field_len(5, carrier.encode())
        + _field_len(6, flight_no.encode())
    )
    outbound_fd = (
        _field_len(2,  outbound_date.encode())
        + _field_len(4,  sel)
        + _field_len(13, _airport_bytes(origin))
        + _field_len(14, _airport_bytes(destination))
    )
    return_fd = (
        _field_len(2,  return_date.encode())
        + _field_len(13, _airport_bytes(destination))
        + _field_len(14, _airport_bytes(origin))
    )

    info = (
        _field_varint(1, 28)
        + _field_varint(2, 2)
        + _field_len(3, outbound_fd)
        + _field_len(3, return_fd)
    )
    for _ in range(adults):
        info += _field_varint(8, 1)
    info += _field_varint(9, seat)
    info += _field_varint(14, 1)
    info += _field_len(16, _FIELD16_ALL_RESULTS)
    info += _field_varint(19, 1)   # round-trip

    return _b64.urlsafe_b64encode(info).rstrip(b'=').decode('ascii')


def build_tfs_multi_city(
    segments: list[dict],   # [{"from": "TPE", "to": "NRT", "date": "2026-05-01"}, ...]
    seat: int = 1,          # 1=economy 2=premium-economy 3=business 4=first
    adults: int = 1,
) -> str:
    """
    Build the Google Flights `tfs` URL parameter for a multi-city itinerary
    (URL-safe base64-encoded protobuf).

    Unlike build_tfs(), this function:
    - Sets field 19 = 3 (MULTI_CITY trip type)
    - Generates one field 3 (FlightData) per segment
    - Includes field 16 (all-results flag), consistent with browser-observed traffic
    """
    info = (
        _field_varint(1, 28)
        + _field_varint(2, 2)
    )

    for seg in segments:
        info += _field_len(3, _flight_data_bytes(seg["date"], seg["from"], seg["to"]))

    for _ in range(adults):
        info += _field_varint(8, 1)

    info += _field_varint(9, seat)
    info += _field_varint(14, 1)
    info += _field_len(16, _FIELD16_ALL_RESULTS)  # on-demand flag (same as SSR searches)
    info += _field_varint(19, 3)   # MULTI_CITY

    return _b64.urlsafe_b64encode(info).rstrip(b'=').decode('ascii')
