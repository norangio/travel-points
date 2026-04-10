"""Transfer bonus loader and lightweight web scrapers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from src.models import TransferBonus

logger = logging.getLogger(__name__)

SCRAPER_USER_AGENT = "travel-points/0.1 (+https://github.com/norangio/travel-points)"


@dataclass
class ScrapedBonusCandidate:
    """A single bonus mention scraped from a source page."""

    source_program: str
    target_program: str
    bonus_percentage: float
    source_name: str
    source_url: str
    start_date: date | None = None
    end_date: date | None = None
    notes: str = ""


@dataclass(frozen=True)
class BonusSource:
    """Configuration for a current-bonus page."""

    name: str
    url: str
    parser_name: str


BONUS_SOURCES: tuple[BonusSource, ...] = (
    BonusSource(
        name="Frequent Miler",
        url="https://frequentmiler.com/current-point-transfer-bonuses/",
        parser_name="frequent_miler",
    ),
    BonusSource(
        name="The Points Guy",
        url="https://thepointsguy.com/loyalty-programs/current-transfer-bonuses/",
        parser_name="the_points_guy",
    ),
    BonusSource(
        name="AwardWallet",
        url="https://awardwallet.com/news/credit-card-transfer-bonuses/",
        parser_name="awardwallet",
    ),
)

SOURCE_PROGRAM_ALIASES: dict[str, str] = {
    "chase": "chase_ur",
    "chase ultimate rewards": "chase_ur",
    "ultimate rewards": "chase_ur",
    "capital one": "capital_one",
    "capital one miles": "capital_one",
    "capital one rewards": "capital_one",
    "venture miles": "capital_one",
    "venture x miles": "capital_one",
    "united": "united_miles",
    "united mileageplus": "united_miles",
}

TARGET_PROGRAM_ALIASES: tuple[tuple[str, str], ...] = (
    ("british airways club", "avios"),
    ("british airways executive club", "avios"),
    ("british airways avios", "avios"),
    ("aer lingus aerclub", "avios"),
    ("aer lingus", "avios"),
    ("iberia plus", "avios"),
    ("iberia", "avios"),
    ("avios", "avios"),
    ("flying blue", "flying_blue"),
    ("air france klm flying blue", "flying_blue"),
    ("air france klm", "flying_blue"),
    ("air france", "flying_blue"),
    ("klm", "flying_blue"),
    ("air canada aeroplan", "aeroplan"),
    ("aeroplan", "aeroplan"),
    ("krisflyer", "singapore"),
    ("singapore airlines", "singapore"),
    ("singapore", "singapore"),
    ("virgin atlantic flying club", "virgin_atlantic"),
    ("virgin atlantic", "virgin_atlantic"),
    ("virgin red", "virgin_atlantic"),
    ("turkish airlines miles smiles", "turkish"),
    ("turkish miles smiles", "turkish"),
    ("turkish", "turkish"),
    ("avianca lifemiles", "avianca"),
    ("lifemiles", "avianca"),
    ("avianca", "avianca"),
    ("cathay pacific asia miles", "cathay"),
    ("asia miles", "cathay"),
    ("cathay", "cathay"),
    ("qatar privilege club", "qatar"),
    ("qatar airways privilege club", "qatar"),
    ("qatar airways", "qatar"),
    ("qatar", "qatar"),
    ("emirates skywards", "emirates"),
    ("emirates", "emirates"),
    ("etihad guest", "etihad"),
    ("etihad", "etihad"),
    ("finnair plus", "finnair"),
    ("finnair", "finnair"),
    ("qantas frequent flyer", "qantas"),
    ("qantas", "qantas"),
    ("aeromexico rewards", "aeromexico"),
    ("aeromexico", "aeromexico"),
    ("jal mileage bank", "jal"),
    ("japan airlines", "jal"),
    ("jal", "jal"),
    ("eva infinity mileage lands", "eva_air"),
    ("eva air", "eva_air"),
    ("tap air portugal miles go", "tap"),
    ("tap portugal miles go", "tap"),
    ("tap", "tap"),
    ("jetblue trueblue", "jetblue"),
    ("jetblue", "jetblue"),
    ("southwest rapid rewards", "southwest"),
    ("southwest", "southwest"),
    ("world of hyatt", "hyatt"),
    ("hyatt", "hyatt"),
    ("marriott bonvoy", "marriott"),
    ("marriott", "marriott"),
    ("ihg one rewards", "ihg"),
    ("ihg", "ihg"),
    ("wyndham rewards", "wyndham"),
    ("wyndham", "wyndham"),
    ("choice privileges", "choice"),
    ("choice", "choice"),
    ("accor live limitless", "accor"),
    ("accor", "accor"),
    ("united mileageplus", "united"),
    ("united", "united"),
)


def load_bonuses_from_config(config: dict) -> list[TransferBonus]:
    """Load transfer bonuses from YAML config."""
    bonuses_raw = config.get("transfer_bonuses") or []
    bonuses: list[TransferBonus] = []

    for bonus_raw in bonuses_raw:
        try:
            bonus = _bonus_from_mapping(bonus_raw, verified_default=True)
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping invalid bonus config: %s", exc)
            continue

        if not _is_bonus_active(bonus):
            continue

        bonuses.append(bonus)
        logger.info(
            "Active config bonus: %s → %s +%.0f%%",
            bonus.source_program,
            bonus.target_program,
            bonus.bonus_percentage * 100,
        )

    return bonuses


def load_transfer_bonuses(
    config: dict,
    transfer_partners: dict,
    *,
    enable_scrapers: bool = True,
    timeout_seconds: float = 15.0,
) -> list[TransferBonus]:
    """Load active bonuses from config and current-bonus pages."""
    bonuses = load_bonuses_from_config(config)
    if not enable_scrapers:
        return bonuses

    scraped_bonuses = scrape_active_transfer_bonuses(
        transfer_partners,
        timeout_seconds=timeout_seconds,
    )
    if not scraped_bonuses:
        return bonuses

    combined = merge_transfer_bonus_lists(bonuses, scraped_bonuses)
    logger.info(
        "Loaded %s config bonus(es) + %s scraped bonus(es) => %s active bonus(es)",
        len(bonuses),
        len(scraped_bonuses),
        len(combined),
    )
    return combined


def scrape_active_transfer_bonuses(
    transfer_partners: dict,
    *,
    timeout_seconds: float = 15.0,
) -> list[TransferBonus]:
    """Scrape current-bonus pages for issuers relevant to this project."""
    all_candidates: list[ScrapedBonusCandidate] = []
    parser_map = {
        "frequent_miler": _parse_frequent_miler_html,
        "the_points_guy": _parse_the_points_guy_html,
        "awardwallet": _parse_awardwallet_html,
    }

    headers = {
        "User-Agent": SCRAPER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for source in BONUS_SOURCES:
                parser = parser_map[source.parser_name]
                try:
                    response = client.get(source.url)
                    response.raise_for_status()
                    candidates = parser(
                        response.text,
                        page_url=str(response.url),
                        source_name=source.name,
                        transfer_partners=transfer_partners,
                    )
                    all_candidates.extend(candidates)
                    logger.info(
                        "Scraped %s transfer bonus mention(s) from %s",
                        len(candidates),
                        source.name,
                    )
                except Exception as exc:
                    logger.warning("Transfer bonus scrape failed for %s: %s", source.name, exc)
    except Exception as exc:
        logger.warning("Transfer bonus scrape setup failed: %s", exc)
        return []

    return _merge_scraped_candidates(all_candidates)


def merge_transfer_bonus_lists(*bonus_lists: list[TransferBonus]) -> list[TransferBonus]:
    """Merge duplicate bonuses across config and scraped sources."""
    grouped: dict[tuple[str, str, float], list[TransferBonus]] = {}
    for bonus_list in bonus_lists:
        for bonus in bonus_list:
            key = (
                bonus.source_program,
                bonus.target_program,
                round(bonus.bonus_percentage, 6),
            )
            grouped.setdefault(key, []).append(bonus)

    merged: list[TransferBonus] = []
    for items in grouped.values():
        base = items[0]
        verified = any(item.verified for item in items) or len(items) > 1
        start_date = _first_date(item.start_date for item in items)
        end_date = _first_date(item.end_date for item in items)
        source_url = next((item.source_url for item in items if item.source_url), "")
        notes = _merge_notes(item.notes for item in items)

        merged.append(
            TransferBonus(
                source_program=base.source_program,
                target_program=base.target_program,
                bonus_percentage=base.bonus_percentage,
                effective_rate=1.0 + base.bonus_percentage,
                start_date=start_date,
                end_date=end_date,
                source_url=source_url,
                verified=verified,
                notes=notes,
            )
        )

    merged.sort(
        key=lambda bonus: (
            bonus.source_program,
            bonus.target_program,
            -bonus.bonus_percentage,
        )
    )
    return [bonus for bonus in merged if _is_bonus_active(bonus)]


def find_active_bonus(
    source_program: str,
    target_program: str,
    bonuses: list[TransferBonus],
) -> TransferBonus | None:
    """Find the best active bonus for a specific transfer path."""
    matching = [
        bonus
        for bonus in bonuses
        if bonus.source_program == source_program and bonus.target_program == target_program
    ]
    if not matching:
        return None
    return max(matching, key=lambda bonus: bonus.bonus_percentage)


def classify_bonuses(
    bonuses: list[TransferBonus],
) -> dict[str, list[TransferBonus]]:
    """Classify bonuses by status for the email alert bar."""
    result: dict[str, list[TransferBonus]] = {
        "new": [],
        "active": [],
        "expiring_soon": [],
    }
    for bonus in bonuses:
        if bonus.is_expiring_soon:
            result["expiring_soon"].append(bonus)
        elif bonus.start_date and (date.today() - bonus.start_date).days <= 3:
            result["new"].append(bonus)
        else:
            result["active"].append(bonus)
    return result


def _parse_frequent_miler_html(
    html: str,
    *,
    page_url: str,
    source_name: str,
    transfer_partners: dict,
) -> list[ScrapedBonusCandidate]:
    return _parse_bonus_tables(
        html,
        page_url=page_url,
        source_name=source_name,
        transfer_partners=transfer_partners,
    )


def _parse_awardwallet_html(
    html: str,
    *,
    page_url: str,
    source_name: str,
    transfer_partners: dict,
) -> list[ScrapedBonusCandidate]:
    return _parse_bonus_tables(
        html,
        page_url=page_url,
        source_name=source_name,
        transfer_partners=transfer_partners,
    )


def _parse_the_points_guy_html(
    html: str,
    *,
    page_url: str,
    source_name: str,
    transfer_partners: dict,
) -> list[ScrapedBonusCandidate]:
    table_candidates = _parse_bonus_tables(
        html,
        page_url=page_url,
        source_name=source_name,
        transfer_partners=transfer_partners,
    )
    if table_candidates:
        return table_candidates

    supported_targets = _supported_target_programs(transfer_partners)
    soup = BeautifulSoup(html, "lxml")
    root = soup.find("article") or soup

    candidates: list[ScrapedBonusCandidate] = []
    current_source: str | None = None

    for element in root.find_all(["h2", "h3", "h4", "p", "li"]):
        text = " ".join(element.stripped_strings)
        if not text:
            continue

        detected_source = _resolve_source_program(text)
        if detected_source and not _resolve_target_programs(text, supported_targets):
            current_source = detected_source
            continue

        source_program = detected_source or current_source
        if not source_program:
            continue

        for bonus_percentage, segment_text in _extract_bonus_segments(text):
            target_programs = _resolve_target_programs(segment_text, supported_targets)
            if not target_programs:
                continue

            end_date = _extract_date_from_text(segment_text)
            for target_program in target_programs:
                candidates.append(
                    ScrapedBonusCandidate(
                        source_program=source_program,
                        target_program=target_program,
                        bonus_percentage=bonus_percentage,
                        source_name=source_name,
                        source_url=page_url,
                        end_date=end_date,
                    )
                )

    return _dedupe_candidates(candidates)


def _parse_bonus_tables(
    html: str,
    *,
    page_url: str,
    source_name: str,
    transfer_partners: dict,
) -> list[ScrapedBonusCandidate]:
    """Parse generic bonus tables used by Frequent Miler and AwardWallet."""
    supported_targets = _supported_target_programs(transfer_partners)
    soup = BeautifulSoup(html, "lxml")
    candidates: list[ScrapedBonusCandidate] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [
            _normalize_text(cell.get_text(" ", strip=True))
            for cell in rows[0].find_all(["th", "td"])
        ]
        if not headers or "bonus" not in " ".join(headers):
            continue

        columns = _identify_table_columns(headers)
        if columns["target"] is None or columns["bonus"] is None:
            continue

        for row in rows[1:]:
            cells = row.find_all(["th", "td"])
            if not cells:
                continue

            cell_texts = [cell.get_text(" ", strip=True) for cell in cells]
            row_text = " ".join(cell_texts)
            source_program = _resolve_source_program(
                cell_texts[columns["source"]]
                if columns["source"] is not None and columns["source"] < len(cell_texts)
                else row_text
            )
            if not source_program:
                continue

            target_text = (
                cell_texts[columns["target"]]
                if columns["target"] is not None and columns["target"] < len(cell_texts)
                else row_text
            )
            target_programs = _resolve_target_programs(target_text, supported_targets)
            if not target_programs:
                target_programs = _resolve_target_programs(row_text, supported_targets)
            if not target_programs:
                continue

            bonus_text = (
                cell_texts[columns["bonus"]]
                if columns["bonus"] is not None and columns["bonus"] < len(cell_texts)
                else row_text
            )
            bonus_percentage = _extract_percentage(bonus_text)
            if bonus_percentage is None:
                continue

            start_date = None
            if columns["start"] is not None and columns["start"] < len(cell_texts):
                start_date = _extract_date_from_text(cell_texts[columns["start"]])

            end_date = None
            if columns["end"] is not None and columns["end"] < len(cell_texts):
                end_date = _extract_date_from_text(cell_texts[columns["end"]])
            if end_date is None:
                end_date = _extract_date_from_text(row_text)

            notes = ""
            if columns["details"] is not None and columns["details"] < len(cell_texts):
                notes = cell_texts[columns["details"]]

            row_link = page_url
            for cell in cells:
                link = cell.find("a", href=True)
                if link and link["href"]:
                    row_link = httpx.URL(page_url).join(link["href"]).__str__()
                    break

            for target_program in target_programs:
                candidates.append(
                    ScrapedBonusCandidate(
                        source_program=source_program,
                        target_program=target_program,
                        bonus_percentage=bonus_percentage,
                        source_name=source_name,
                        source_url=row_link,
                        start_date=start_date,
                        end_date=end_date,
                        notes=notes,
                    )
                )

    return _dedupe_candidates(candidates)


def _merge_scraped_candidates(
    candidates: list[ScrapedBonusCandidate],
) -> list[TransferBonus]:
    """Collapse duplicate scraped bonus mentions across sources."""
    grouped: dict[tuple[str, str, float], list[ScrapedBonusCandidate]] = {}
    for candidate in candidates:
        key = (
            candidate.source_program,
            candidate.target_program,
            round(candidate.bonus_percentage, 6),
        )
        grouped.setdefault(key, []).append(candidate)

    bonuses: list[TransferBonus] = []
    for items in grouped.values():
        base = items[0]
        source_names = sorted({item.source_name for item in items if item.source_name})
        notes = _merge_notes(item.notes for item in items)
        if source_names:
            source_note = f"Sources: {', '.join(source_names)}"
            notes = source_note if not notes else f"{notes}. {source_note}"

        bonus = TransferBonus(
            source_program=base.source_program,
            target_program=base.target_program,
            bonus_percentage=base.bonus_percentage,
            effective_rate=1.0 + base.bonus_percentage,
            start_date=_first_date(item.start_date for item in items),
            end_date=_first_date(item.end_date for item in items),
            source_url=next((item.source_url for item in items if item.source_url), ""),
            verified=len(source_names) >= 2,
            notes=notes,
        )
        if _is_bonus_active(bonus):
            bonuses.append(bonus)

    bonuses.sort(
        key=lambda bonus: (
            bonus.source_program,
            bonus.target_program,
            -bonus.bonus_percentage,
        )
    )
    return bonuses


def _identify_table_columns(headers: list[str]) -> dict[str, int | None]:
    columns = {
        "source": None,
        "target": None,
        "bonus": None,
        "details": None,
        "start": None,
        "end": None,
    }

    for index, header in enumerate(headers):
        if columns["source"] is None and any(
            token in header for token in ("bank", "issuer", "credit card", "from", "rewards program")
        ):
            columns["source"] = index
        elif columns["target"] is None and any(
            token in header for token in ("program", "partner", "to", "airline", "hotel")
        ):
            columns["target"] = index
        elif columns["bonus"] is None and "bonus" in header:
            columns["bonus"] = index
        elif columns["details"] is None and any(
            token in header for token in ("detail", "notes")
        ):
            columns["details"] = index
        elif columns["start"] is None and "start" in header:
            columns["start"] = index
        elif columns["end"] is None and any(
            token in header for token in ("end", "expiry", "expire")
        ):
            columns["end"] = index

    if columns["source"] is None and len(headers) >= 3:
        columns["source"] = 0
    if columns["target"] is None and len(headers) >= 3:
        columns["target"] = 1

    return columns


def _dedupe_candidates(
    candidates: list[ScrapedBonusCandidate],
) -> list[ScrapedBonusCandidate]:
    """Deduplicate repeated mentions within a single source page."""
    seen: set[tuple[str, str, float, str]] = set()
    deduped: list[ScrapedBonusCandidate] = []
    for candidate in candidates:
        key = (
            candidate.source_program,
            candidate.target_program,
            round(candidate.bonus_percentage, 6),
            candidate.source_name,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _bonus_from_mapping(
    bonus_raw: dict,
    *,
    verified_default: bool,
) -> TransferBonus:
    start_date = _parse_date(bonus_raw.get("start_date"))
    end_date = _parse_date(bonus_raw.get("end_date"))
    bonus_percentage = float(bonus_raw.get("bonus_percentage", 0))

    return TransferBonus(
        source_program=bonus_raw["source_program"],
        target_program=bonus_raw["target_program"],
        bonus_percentage=bonus_percentage,
        effective_rate=1.0 + bonus_percentage,
        start_date=start_date,
        end_date=end_date,
        source_url=bonus_raw.get("source_url", ""),
        verified=bool(bonus_raw.get("verified", verified_default)),
        notes=bonus_raw.get("notes", ""),
    )


def _first_date(values) -> date | None:
    dates = [value for value in values if value is not None]
    if not dates:
        return None
    return min(dates)


def _merge_notes(notes_iterable) -> str:
    notes = [note.strip() for note in notes_iterable if note and note.strip()]
    unique_notes: list[str] = []
    for note in notes:
        if note not in unique_notes:
            unique_notes.append(note)
    return ". ".join(unique_notes)


def _is_bonus_active(bonus: TransferBonus) -> bool:
    today = date.today()
    if bonus.start_date and bonus.start_date > today:
        return False
    if bonus.end_date and bonus.end_date < today:
        return False
    return True


def _supported_target_programs(transfer_partners: dict) -> set[str]:
    supported: set[str] = set()
    for program in transfer_partners.values():
        for partner_key in (program.get("partners") or {}).keys():
            supported.add(partner_key)
    return supported


def _resolve_source_program(text: str) -> str | None:
    normalized = _normalize_text(text)
    for alias, source_program in SOURCE_PROGRAM_ALIASES.items():
        if alias in normalized:
            return source_program
    return None


def _resolve_target_programs(text: str, supported_targets: set[str]) -> set[str]:
    normalized = _normalize_text(text)
    target_programs: set[str] = set()
    for alias, target_program in TARGET_PROGRAM_ALIASES:
        if target_program in supported_targets and alias in normalized:
            target_programs.add(target_program)
    return target_programs


def _extract_percentage(text: str) -> float | None:
    match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", text)
    if not match:
        return None
    return float(match.group(1)) / 100.0


def _extract_bonus_segments(text: str) -> list[tuple[float, str]]:
    matches = list(re.finditer(r"(\d{1,3}(?:\.\d+)?)\s*%", text))
    if not matches:
        return []

    segments: list[tuple[float, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment_text = text[match.start():end]
        segments.append((float(match.group(1)) / 100.0, segment_text))
    return segments


def _extract_date_from_text(text: str) -> date | None:
    text = text.strip()
    if not text:
        return None

    patterns = [
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]* \d{1,2},? \d{2,4}\b",
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]* \d{1,2}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            parsed = date_parser.parse(
                match.group(0),
                fuzzy=True,
                default=datetime.combine(date.today(), datetime.min.time()),
            )
            return parsed.date()
        except (ValueError, OverflowError, TypeError):
            continue
    return None


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date_parser.parse(str(value), fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None
