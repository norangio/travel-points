"""Data models for the Points Deal Finder."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class TransferBonus:
    """An active transfer bonus promotion."""

    source_program: str  # "chase_ur", "capital_one"
    target_program: str  # "avios", "flying_blue"
    bonus_percentage: float  # 0.20 for 20% bonus
    effective_rate: float  # 1.2 (1:1.2)
    start_date: date | None = None
    end_date: date | None = None
    source_url: str = ""
    verified: bool = False
    notes: str = ""

    @property
    def days_remaining(self) -> int | None:
        if self.end_date is None:
            return None
        return (self.end_date - date.today()).days

    @property
    def is_expiring_soon(self) -> bool:
        remaining = self.days_remaining
        return remaining is not None and remaining <= 7


@dataclass
class TransferPath:
    """A specific way to pay for an award using your points."""

    source_program: str  # "chase_ur"
    source_display_name: str  # "Chase Ultimate Rewards"
    target_program: str  # "avios"
    points_needed_per_person: int
    points_needed_total: int  # for all travelers
    has_active_bonus: bool = False
    bonus: TransferBonus | None = None
    effective_rate: float = 1.0
    affordable_one: bool = False
    affordable_both: bool = False
    balance_remaining: int = 0


@dataclass
class FlightSegment:
    """A single flight segment within an itinerary."""

    origin: str
    destination: str
    operating_carrier: str  # IATA code
    flight_number: str = ""
    departure: datetime | None = None
    arrival: datetime | None = None
    duration_hours: float = 0.0
    aircraft: str = ""


@dataclass
class LayoverInfo:
    """Information about a layover between segments."""

    airport: str  # IATA code
    city: str = ""
    duration_hours: float = 0.0
    is_long: bool = False  # > 4 hours


@dataclass
class LayoverAnalysis:
    """Analysis for a long layover — hotels and transit."""

    airport: str
    city: str
    country: str
    duration_hours: float
    airport_hotel_usd: int | None = None
    city_center_hotel_usd: int | None = None
    transit_options: list[TransitOption] = field(default_factory=list)
    notes: str = ""


@dataclass
class TransitOption:
    """A transit option from the airport."""

    mode: str  # "Metro", "Train", "Bus", "Taxi"
    cost_usd: float
    time_min: int
    notes: str = ""


@dataclass
class AwardAvailability:
    """Raw award availability from seats.aero."""

    id: str
    source: str  # seats.aero source (mileage program)
    origin: str
    destination: str
    departure_date: date
    return_date: date | None = None
    cabin: str = "business"
    points_cost: int = 0
    taxes_usd: float = 0.0
    seats_available: int = 0
    segments: list[FlightSegment] = field(default_factory=list)
    layovers: list[LayoverInfo] = field(default_factory=list)
    total_travel_hours: float = 0.0
    num_connections: int = 0
    max_layover_hours: float = 0.0
    operating_carriers: list[str] = field(default_factory=list)
    aircraft_types: list[str] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)


@dataclass
class ScoredDeal:
    """A fully scored and ranked deal ready for the email."""

    availability: AwardAvailability
    score: float
    best_path: TransferPath
    all_paths: list[TransferPath]
    airline_name: str = ""
    airline_tier: str = "neutral"
    airline_rating: float = 0.0
    product_name: str = ""
    cash_price_usd: float | None = None
    cpp_value: float | None = None
    layover_analyses: list[LayoverAnalysis] = field(default_factory=list)

    # For matching a trip definition
    trip_name: str = ""
    direction: str = ""  # "outbound" or "return"

    # Deal history tracking
    first_seen: date | None = None  # when this deal was first found
    days_tracked: int = 0  # how many days we've been seeing this deal
    is_new: bool = True  # first time appearing

    @property
    def route_display(self) -> str:
        a = self.availability
        return f"{a.origin} → {a.destination}"

    @property
    def direction_label(self) -> str:
        if self.direction == "outbound":
            return "Outbound"
        elif self.direction == "return":
            return "Return"
        return ""

    @property
    def freshness_label(self) -> str:
        """Label for how long this deal has been tracked."""
        if self.is_new:
            return "NEW"
        if self.days_tracked == 1:
            return "Day 2"
        return f"Day {self.days_tracked + 1}"

    @property
    def has_long_layover(self) -> bool:
        return any(la.duration_hours > 4 for la in self.layover_analyses)
