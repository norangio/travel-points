import unittest

from src.sources.seats_aero import parse_availability


class SeatsAeroParsingTest(unittest.TestCase):
    def test_trip_detail_overrides_remaining_seats(self) -> None:
        raw = {
            "ID": "avail-1",
            "Source": "avios",
            "Date": "2026-06-05",
            "JMileageCost": 60000,
            "JRemainingSeats": 2,
            "Route": {"OriginAirport": "LAX", "DestinationAirport": "LHR"},
        }
        trip = {
            "RemainingSeats": 1,
            "Connections": 0,
            "TotalDuration": 640,
            "Carriers": "BA",
            "AvailabilitySegments": [],
        }

        availability = parse_availability(raw, trip)

        self.assertEqual(availability.seats_available, 1)
        self.assertEqual(availability.operating_carriers, ["BA"])
        self.assertEqual(availability.total_travel_hours, 10.7)
