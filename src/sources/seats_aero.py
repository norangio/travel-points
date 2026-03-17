"""seats.aero Pro API client."""

from __future__ import annotations

import logging
from datetime import date, datetime

import httpx

from src.models import (
    AwardAvailability,
    FlightSegment,
    LayoverInfo,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://seats.aero/partnerapi"


class SeatsAeroClient:
    """Client for the seats.aero Partner API."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Partner-Authorization": api_key},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def cached_search(
        self,
        origin: str,
        destination: str,
        cabin: str = "business",
        start_date: date | None = None,
        end_date: date | None = None,
        source: str | None = None,
    ) -> list[dict]:
        """
        Query cached award availability.

        Returns raw availability results from seats.aero.
        """
        params: dict = {
            "origin_airport": origin,
            "destination_airport": destination,
            "cabin": cabin.lower(),
        }
        if start_date:
            params["start_date"] = start_date.isoformat()
        if end_date:
            params["end_date"] = end_date.isoformat()
        if source:
            params["source"] = source

        try:
            resp = await self.client.get("/search", params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("data", [])
            logger.info(
                f"seats.aero search {origin}->{destination}: {len(results)} results"
            )
            return results
        except httpx.HTTPStatusError as e:
            logger.error(f"seats.aero search error {e.response.status_code}: {e}")
            return []
        except Exception as e:
            logger.error(f"seats.aero search failed: {e}")
            return []

    async def get_trip(self, availability_id: str) -> dict | None:
        """
        Get detailed trip/routing info for an availability result.

        This reveals segments, layovers, operating carriers.
        The trips endpoint returns {"data": [...]}, a list of trip options.
        We take the first one (shortest/best routing).
        """
        try:
            resp = await self.client.get(f"/trips/{availability_id}")
            resp.raise_for_status()
            data = resp.json().get("data")
            if isinstance(data, list):
                if not data:
                    return None
                # Take the first trip option
                return data[0]
            # If it's already a dict, return as-is
            return data
        except httpx.HTTPStatusError as e:
            logger.error(f"seats.aero trip {availability_id} error: {e}")
            return None
        except Exception as e:
            logger.error(f"seats.aero trip {availability_id} failed: {e}")
            return None

    async def get_availability(self, source: str) -> list[dict]:
        """
        Bulk availability for an entire mileage program.
        Use sparingly — burns API quota.
        """
        try:
            resp = await self.client.get(
                "/availability",
                params={"source": source},
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error(f"seats.aero bulk availability ({source}) failed: {e}")
            return []


_LOGGED_RAW_KEYS = False  # one-shot diagnostic flag


def parse_availability(raw: dict, trip_detail: dict | None = None) -> AwardAvailability:
    """Parse a raw seats.aero result + optional trip detail into our model."""
    global _LOGGED_RAW_KEYS
    if not _LOGGED_RAW_KEYS:
        logger.info(f"[DIAG] raw keys: {sorted(raw.keys())}")
        if trip_detail:
            logger.info(f"[DIAG] trip_detail keys: {sorted(trip_detail.keys())}")
        route_obj = raw.get("Route")
        if route_obj and isinstance(route_obj, dict):
            logger.info(f"[DIAG] Route sub-keys: {sorted(route_obj.keys())}")
        _LOGGED_RAW_KEYS = True

    availability_id = raw.get("ID", raw.get("id", ""))
    source = raw.get("Source", raw.get("source", ""))

    # Parse departure date
    date_str = raw.get("Date", raw.get("date", ""))
    try:
        departure_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, IndexError):
        departure_date = date.today()

    # Determine cabin and its IATA code (J=business, F=first, W=premium, Y=economy)
    cabin_val = raw.get("Cabin", raw.get("cabin", "business"))
    cabin_code = _cabin_code(cabin_val)

    # Mileage cost: seats.aero may use per-cabin fields (JMileageCost) OR a flat field.
    # Try per-cabin first, then generic fallback.
    points_cost = (
        _parse_int(raw.get(f"{cabin_code}MileageCost"))
        or _parse_int(raw.get("MileageCost", raw.get("mileage_cost", 0)))
    )

    # Seats: same per-cabin pattern
    seats_available = (
        _parse_int(raw.get(f"{cabin_code}RemainingSeats"))
        or _parse_int(raw.get("RemainingSeats", raw.get("remaining_seats", 0)))
    )

    # Origin/destination: may be flat or nested inside Route object
    route_obj = raw.get("Route", {}) if isinstance(raw.get("Route"), dict) else {}
    origin = (
        raw.get("OriginAirport")
        or route_obj.get("OriginAirport")
        or raw.get("origin_airport", "")
    )
    destination = (
        raw.get("DestinationAirport")
        or route_obj.get("DestinationAirport")
        or raw.get("destination_airport", "")
    )

    # Base fields
    avail = AwardAvailability(
        id=str(availability_id),
        source=source,
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        cabin=cabin_val,
        points_cost=points_cost,
        taxes_usd=_parse_float(raw.get("TotalTaxes", raw.get("total_taxes", 0))),
        seats_available=seats_available,
        raw_data=raw,
    )

    # If we have trip detail, parse segments and layovers
    if trip_detail:
        _parse_trip_detail(avail, trip_detail)

    # Fallback: extract operating carriers from raw data if not set by trip detail
    if not avail.operating_carriers:
        # seats.aero raw data has per-cabin airlines: JAirlines, FAirlines, etc.
        airlines_str = (
            raw.get(f"{cabin_code}Airlines")
            or raw.get("OperatingCarriers")
            or raw.get("operating_carriers", "")
        )
        if isinstance(airlines_str, str) and airlines_str:
            avail.operating_carriers = [
                c.strip() for c in airlines_str.split(",") if c.strip()
            ]
        elif isinstance(airlines_str, list):
            avail.operating_carriers = airlines_str

    return avail


_LOGGED_TRIP_KEYS = False  # one-shot trip diagnostic


def _parse_trip_detail(avail: AwardAvailability, trip: dict) -> None:
    """Parse trip detail response to extract segments and layovers.

    seats.aero trip detail uses these top-level fields:
      Carriers, Connections, TotalDuration (minutes), DepartsAt, ArrivesAt,
      AvailabilitySegments (NOT "Segments"), Aircraft, FlightNumbers
    """
    global _LOGGED_TRIP_KEYS
    if not _LOGGED_TRIP_KEYS:
        logger.info(f"[DIAG] trip detail keys: {sorted(trip.keys())}")
        _LOGGED_TRIP_KEYS = True

    # --- Top-level trip fields (most reliable) ---
    carriers_str = trip.get("Carriers", "")
    if isinstance(carriers_str, str) and carriers_str:
        avail.operating_carriers = [
            c.strip() for c in carriers_str.split(",") if c.strip()
        ]

    connections = _parse_int(trip.get("Connections", trip.get("Stops", 0)))
    avail.num_connections = connections

    # TotalDuration is in minutes
    total_duration_min = _parse_int(trip.get("TotalDuration", 0))
    if total_duration_min > 0:
        avail.total_travel_hours = round(total_duration_min / 60, 1)

    # --- Parse segments: seats.aero uses "AvailabilitySegments" ---
    segments_data = (
        trip.get("AvailabilitySegments")
        or trip.get("Segments")
        or trip.get("segments")
        or []
    )

    # Log segment structure once for diagnostics
    if segments_data:
        if not hasattr(_parse_trip_detail, "_logged_seg"):
            _parse_trip_detail._logged_seg = True
            first_seg = segments_data[0]
            if isinstance(first_seg, dict):
                logger.info(f"[DIAG] segment keys: {sorted(first_seg.keys())}")

    if not segments_data:
        return

    segments: list[FlightSegment] = []
    for seg in segments_data:
        departure_str = (
            seg.get("DepartsAt")
            or seg.get("DepartureTime")
            or seg.get("DepartureDateTime")
            or seg.get("departure_time", "")
        )
        arrival_str = (
            seg.get("ArrivesAt")
            or seg.get("ArrivalTime")
            or seg.get("ArrivalDateTime")
            or seg.get("arrival_time", "")
        )

        dep_dt = _parse_datetime(departure_str)
        arr_dt = _parse_datetime(arrival_str)

        duration = 0.0
        if dep_dt and arr_dt:
            duration = (arr_dt - dep_dt).total_seconds() / 3600

        carrier = (
            seg.get("Carrier")
            or seg.get("OperatingCarrier")
            or seg.get("OperatingAirline")
            or seg.get("AirlineCode")
            or seg.get("operating_carrier", "")
        )

        segment = FlightSegment(
            origin=(
                seg.get("OriginAirport")
                or seg.get("Origin")
                or seg.get("origin", "")
            ),
            destination=(
                seg.get("DestinationAirport")
                or seg.get("Destination")
                or seg.get("destination", "")
            ),
            operating_carrier=carrier,
            flight_number=(
                seg.get("FlightNumber")
                or seg.get("flight_number", "")
            ),
            departure=dep_dt,
            arrival=arr_dt,
            duration_hours=round(duration, 1),
            aircraft=seg.get("Aircraft", seg.get("aircraft", "")),
        )
        segments.append(segment)

    avail.segments = segments

    # If we got carriers from segments but not from top-level, set them
    if not avail.operating_carriers:
        avail.operating_carriers = list(
            {s.operating_carrier for s in segments if s.operating_carrier}
        )

    # If connections weren't set from top-level, infer from segments
    if avail.num_connections == 0 and len(segments) > 1:
        avail.num_connections = len(segments) - 1

    # Calculate layovers from segments
    layovers: list[LayoverInfo] = []
    max_layover = 0.0
    for i in range(len(segments) - 1):
        prev_seg = segments[i]
        next_seg = segments[i + 1]
        if prev_seg.arrival and next_seg.departure:
            layover_hours = (
                next_seg.departure - prev_seg.arrival
            ).total_seconds() / 3600
            layover_hours = round(layover_hours, 1)
            max_layover = max(max_layover, layover_hours)

            layover = LayoverInfo(
                airport=prev_seg.destination,
                duration_hours=layover_hours,
                is_long=layover_hours > 4.0,
            )
            layovers.append(layover)

    avail.layovers = layovers
    avail.max_layover_hours = max_layover

    # Total travel time from segments (if not already set from TotalDuration)
    if (
        avail.total_travel_hours == 0.0
        and segments
        and segments[0].departure
        and segments[-1].arrival
    ):
        total = (
            segments[-1].arrival - segments[0].departure
        ).total_seconds() / 3600
        avail.total_travel_hours = round(total, 1)


def _parse_datetime(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _cabin_code(cabin: str) -> str:
    """Map a cabin name or code to its single-letter IATA code."""
    c = cabin.strip().upper()
    if c in ("J", "C", "D", "I", "Z"):  # IATA business class codes
        return "J"
    if c in ("F", "P", "A"):  # IATA first class codes
        return "F"
    if c in ("W", "S"):  # IATA premium economy
        return "W"
    # Map common full-name strings
    mapping = {
        "BUSINESS": "J",
        "FIRST": "F",
        "PREMIUM": "W",
        "PREMIUM ECONOMY": "W",
        "ECONOMY": "Y",
    }
    return mapping.get(c, "J")  # default to J (business) if unrecognised


def _parse_int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _parse_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0
