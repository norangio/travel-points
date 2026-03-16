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
        """
        try:
            resp = await self.client.get(f"/trips/{availability_id}")
            resp.raise_for_status()
            return resp.json().get("data")
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


def parse_availability(raw: dict, trip_detail: dict | None = None) -> AwardAvailability:
    """Parse a raw seats.aero result + optional trip detail into our model."""
    availability_id = raw.get("ID", raw.get("id", ""))
    source = raw.get("Source", raw.get("source", ""))

    # Parse departure date
    date_str = raw.get("Date", raw.get("date", ""))
    try:
        departure_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, IndexError):
        departure_date = date.today()

    # Base fields
    avail = AwardAvailability(
        id=str(availability_id),
        source=source,
        origin=raw.get("OriginAirport", raw.get("origin_airport", "")),
        destination=raw.get("DestinationAirport", raw.get("destination_airport", "")),
        departure_date=departure_date,
        cabin=raw.get("Cabin", raw.get("cabin", "business")),
        points_cost=_parse_int(raw.get("MileageCost", raw.get("mileage_cost", 0))),
        taxes_usd=_parse_float(raw.get("TotalTaxes", raw.get("total_taxes", 0))),
        seats_available=_parse_int(
            raw.get("RemainingSeats", raw.get("remaining_seats", 0))
        ),
        raw_data=raw,
    )

    # If we have trip detail, parse segments and layovers
    if trip_detail:
        _parse_trip_detail(avail, trip_detail)
    else:
        # Infer from raw data if possible
        carriers = raw.get("OperatingCarriers", raw.get("operating_carriers", ""))
        if isinstance(carriers, str) and carriers:
            avail.operating_carriers = [c.strip() for c in carriers.split(",")]
        elif isinstance(carriers, list):
            avail.operating_carriers = carriers

    return avail


def _parse_trip_detail(avail: AwardAvailability, trip: dict) -> None:
    """Parse trip detail response to extract segments and layovers."""
    segments_data = trip.get("Segments", trip.get("segments", []))
    if not segments_data:
        return

    segments: list[FlightSegment] = []
    for seg in segments_data:
        departure_str = seg.get("DepartureTime", seg.get("departure_time", ""))
        arrival_str = seg.get("ArrivalTime", seg.get("arrival_time", ""))

        dep_dt = _parse_datetime(departure_str)
        arr_dt = _parse_datetime(arrival_str)

        duration = 0.0
        if dep_dt and arr_dt:
            duration = (arr_dt - dep_dt).total_seconds() / 3600

        carrier = seg.get(
            "OperatingCarrier",
            seg.get("operating_carrier", seg.get("Carrier", "")),
        )

        segment = FlightSegment(
            origin=seg.get("Origin", seg.get("origin", "")),
            destination=seg.get("Destination", seg.get("destination", "")),
            operating_carrier=carrier,
            flight_number=seg.get(
                "FlightNumber", seg.get("flight_number", "")
            ),
            departure=dep_dt,
            arrival=arr_dt,
            duration_hours=round(duration, 1),
            aircraft=seg.get("Aircraft", seg.get("aircraft", "")),
        )
        segments.append(segment)

    avail.segments = segments
    avail.operating_carriers = list(
        {s.operating_carrier for s in segments if s.operating_carrier}
    )
    avail.num_connections = max(0, len(segments) - 1)

    # Calculate layovers
    layovers: list[LayoverInfo] = []
    total_layover = 0.0
    max_layover = 0.0
    for i in range(len(segments) - 1):
        prev_seg = segments[i]
        next_seg = segments[i + 1]
        if prev_seg.arrival and next_seg.departure:
            layover_hours = (
                next_seg.departure - prev_seg.arrival
            ).total_seconds() / 3600
            layover_hours = round(layover_hours, 1)
            total_layover += layover_hours
            max_layover = max(max_layover, layover_hours)

            layover = LayoverInfo(
                airport=prev_seg.destination,
                duration_hours=layover_hours,
                is_long=layover_hours > 4.0,
            )
            layovers.append(layover)

    avail.layovers = layovers
    avail.max_layover_hours = max_layover

    # Total travel time
    if segments and segments[0].departure and segments[-1].arrival:
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
