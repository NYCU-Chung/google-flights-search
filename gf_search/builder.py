"""
Google Flights tfs URL parameter builder.
Builds URL-safe base64-encoded protobuf for the Google Flights search endpoint.
"""

from __future__ import annotations

# Google Flights city/metro entity IDs for airports that need city-level search.
# Format: IATA -> entity_id (entity_type=2 = city/metro area)
# Regular airports use IATA + entity_type=1 (handled automatically).
CITY_ENTITIES: dict[str, str] = {
    "RMQ": "/m/01r8pt",   # 台中清泉崗 → 台中市 entity
    "KHH": "/m/0h7h6",    # 高雄小港
    "TSA": "/m/02kg86",   # 台北松山
}


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
    import base64 as _b64

    def _varint(n: int) -> bytes:
        buf = []
        while n > 0x7F:
            buf.append((n & 0x7F) | 0x80)
            n >>= 7
        buf.append(n & 0x7F)
        return bytes(buf)

    def _field_varint(field_no: int, value: int) -> bytes:
        return _varint((field_no << 3) | 0) + _varint(value)

    def _field_len(field_no: int, data: bytes) -> bytes:
        return _varint((field_no << 3) | 2) + _varint(len(data)) + data

    def _airport_bytes(iata_or_entity: str) -> bytes:
        if iata_or_entity in CITY_ENTITIES:
            entity_id = CITY_ENTITIES[iata_or_entity]
            entity_type = 2   # city/metro
        else:
            entity_id = iata_or_entity.upper()
            entity_type = 1   # airport
        return _field_varint(1, entity_type) + _field_len(2, entity_id.encode())

    def _flight_data_bytes(date: str, frm: str, to: str) -> bytes:
        f2  = _field_len(2, date.encode())         # date
        f13 = _field_len(13, _airport_bytes(frm))  # from_airport
        f14 = _field_len(14, _airport_bytes(to))   # to_airport
        return f2 + f13 + f14

    # Assemble Info message
    info = (
        _field_varint(1, 28)                 # field 1 = 28 (query type flag)
        + _field_varint(2, 2)                # field 2 = 2 (query type flag)
        + _field_len(3, _flight_data_bytes(departure_date, origin, destination))
    )

    if return_date:
        info += _field_len(3, _flight_data_bytes(return_date, destination, origin))

    # passengers (one field 8 per adult)
    for _ in range(adults):
        info += _field_varint(8, 1)          # Passenger.ADULT = 1

    # seat class
    info += _field_varint(9, seat)           # field 9 = Seat

    # field 14 = 1 (display settings flag)
    info += _field_varint(14, 1)

    # field 16 = sub-message { field 1 = -1/all-bits }
    # (all-results flag, makes Google return small-airport data)
    # observed bytes: 08 ff ff ff ff ff ff ff ff ff 01 (field tag 0x08 + 10-byte varint = -1)
    _field16_content = b'\x08' + b'\xff' * 9 + b'\x01'  # 11 bytes
    info += _field_len(16, _field16_content)

    # field 19 = trip type (1=round-trip, 2=one-way)
    trip = 1 if return_date else 2
    info += _varint((19 << 3) | 0) + _varint(trip)

    # URL-safe base64
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
    - Does NOT include field 16 (all-results flag); multi-city uses batchexecute
      rather than on-demand SSR, so the flag is unnecessary
    """
    import base64 as _b64

    def _varint(n: int) -> bytes:
        buf = []
        while n > 0x7F:
            buf.append((n & 0x7F) | 0x80)
            n >>= 7
        buf.append(n & 0x7F)
        return bytes(buf)

    def _field_varint(field_no: int, value: int) -> bytes:
        return _varint((field_no << 3) | 0) + _varint(value)

    def _field_len(field_no: int, data: bytes) -> bytes:
        return _varint((field_no << 3) | 2) + _varint(len(data)) + data

    def _airport_bytes(iata_or_entity: str) -> bytes:
        if iata_or_entity in CITY_ENTITIES:
            entity_id = CITY_ENTITIES[iata_or_entity]
            entity_type = 2   # city/metro
        else:
            entity_id = iata_or_entity.upper()
            entity_type = 1   # airport
        return _field_varint(1, entity_type) + _field_len(2, entity_id.encode())

    def _flight_data_bytes(date: str, frm: str, to: str) -> bytes:
        f2  = _field_len(2, date.encode())         # date
        f13 = _field_len(13, _airport_bytes(frm))  # from_airport
        f14 = _field_len(14, _airport_bytes(to))   # to_airport
        return f2 + f13 + f14

    # Assemble Info message
    info = (
        _field_varint(1, 28)   # field 1 = 28 (query type flag)
        + _field_varint(2, 2)  # field 2 = 2 (query type flag)
    )

    # One FlightData block per segment
    for seg in segments:
        info += _field_len(3, _flight_data_bytes(seg["date"], seg["from"], seg["to"]))

    # passengers (one field 8 per adult)
    for _ in range(adults):
        info += _field_varint(8, 1)   # Passenger.ADULT = 1

    # seat class
    info += _field_varint(9, seat)    # field 9 = Seat

    # field 14 = 1 (display settings flag)
    info += _field_varint(14, 1)

    # field 19 = 3 (MULTI_CITY trip type)
    info += _field_varint(19, 3)

    # URL-safe base64
    return _b64.urlsafe_b64encode(info).rstrip(b'=').decode('ascii')
