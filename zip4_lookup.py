#!/usr/bin/env python3
"""
Look up ZIP+4 codes for USA contacts exported from macOS Address Book.

Uses the same USPS REST API as gamebits/woocommerce-zip4 (read-only reference):
  - OAuth:  https://apis.usps.com/oauth2/v3/token
  - Lookup: https://apis.usps.com/addresses/v3/address

Credentials — any one of these (do not commit real values):
  1. export in the current Terminal window (session only):
       export USPS_CLIENT_ID=...
       export USPS_CLIENT_SECRET=...
  2. Fill in USPS_CLIENT_ID and USPS_CLIENT_SECRET in this file, or
  3. Copy .env.example to .env and add your credentials there.

Priority: exported env vars, then script constants, then .env file.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
US_COUNTRIES = frozenset({"", "United States", "USA", "US", "U.S.", "U.S.A."})

TOKEN_URL = "https://apis.usps.com/oauth2/v3/token"
ADDRESS_URL = "https://apis.usps.com/addresses/v3/address"
TOKEN_TTL_SECONDS = 3300

# Option 2: paste credentials here (leave empty to use export or .env).
USPS_CLIENT_ID = ""
USPS_CLIENT_SECRET = ""


@dataclass(frozen=True)
class ContactAddress:
    name: str
    address_index: int
    street: str
    city: str
    state: str
    country: str
    zip5: str


@dataclass
class LookupResult:
    contact: ContactAddress
    zip4: Optional[str]
    status: str
    detail: str = ""


def is_us_address(country: str, state: str) -> bool:
    country = country.strip()
    state = state.strip().upper()
    if country in US_COUNTRIES:
        return not state or state in US_STATES
    return False


def is_five_digit_zip(zip_code: str) -> bool:
    return bool(re.fullmatch(r"\d{5}", zip_code.strip()))


def split_street(street: str) -> tuple[str, str]:
    """Mirror plugin behavior: secondary line strips '#', primary strips '.'."""
    street = street.strip()
    if "#" in street:
        primary, secondary = street.split("#", 1)
        return primary.strip().replace(".", ""), secondary.strip()
    return street.replace(".", ""), ""


def format_zip4(zip_code: str, plus4: str) -> Optional[str]:
    zip_digits = re.sub(r"\D", "", zip_code)[:5]
    plus4_digits = re.sub(r"\D", "", plus4)[:4]
    if len(zip_digits) != 5 or len(plus4_digits) != 4:
        return None
    return f"{zip_digits}-{plus4_digits}"


def load_contacts(csv_path: Path) -> list[ContactAddress]:
    matches: list[ContactAddress] = []

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            first = row.get("First", "").strip()
            last = row.get("Last", "").strip()
            name = f"{first} {last}".strip()

            for index in range(1, 5):
                street = row.get(f"Street {index}", "").strip()
                city = row.get(f"City {index}", "").strip()
                state = row.get(f"State {index}", "").strip()
                country = row.get(f"Country {index}", "").strip()
                zip_code = row.get(f"Zip {index}", "").strip()

                if not zip_code or not is_five_digit_zip(zip_code):
                    continue
                if not is_us_address(country, state):
                    continue

                matches.append(
                    ContactAddress(
                        name=name or "(no name)",
                        address_index=index,
                        street=street,
                        city=city,
                        state=state.upper(),
                        country=country,
                        zip5=zip_code,
                    )
                )

    return matches


class UspsClient:
    def __init__(self, client_id: str, client_secret: str, timeout: int = 15) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self._token: Optional[str] = None
        self._token_expires_at = 0.0

    def _request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        data: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> tuple[int, dict]:
        body = None
        req_headers = dict(headers or {})

        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        request = urllib.request.Request(url, data=body, method=method, headers=req_headers)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
                status = response.status
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            status = exc.code

        try:
            parsed = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            parsed = {"raw": payload}

        return status, parsed

    def get_access_token(self, force_refresh: bool = False) -> Optional[str]:
        now = time.time()
        if not force_refresh and self._token and now < self._token_expires_at:
            return self._token

        status, data = self._request_json(
            TOKEN_URL,
            method="POST",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )

        token = data.get("access_token")
        if status < 200 or status >= 300 or not token:
            return None

        self._token = token
        self._token_expires_at = now + TOKEN_TTL_SECONDS
        return token

    def lookup_zip4(self, contact: ContactAddress) -> LookupResult:
        if not contact.street or not contact.city or not contact.state:
            return LookupResult(
                contact=contact,
                zip4=None,
                status="skipped",
                detail="missing street, city, or state",
            )

        token = self.get_access_token()
        if not token:
            return LookupResult(
                contact=contact,
                zip4=None,
                status="error",
                detail="could not obtain USPS access token",
            )

        addr1, addr2 = split_street(contact.street)
        query = urllib.parse.urlencode(
            {
                "streetAddress": addr1,
                "secondaryAddress": addr2.replace("#", ""),
                "city": contact.city,
                "state": contact.state,
                "ZIPCode": contact.zip5,
            }
        )
        url = f"{ADDRESS_URL}?{query}"

        status, data = self._request_json(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        if status == 401:
            token = self.get_access_token(force_refresh=True)
            if not token:
                return LookupResult(
                    contact=contact,
                    zip4=None,
                    status="error",
                    detail="USPS token expired and refresh failed",
                )
            status, data = self._request_json(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )

        if status < 200 or status >= 300:
            message = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else ""
            if not message:
                message = data.get("message") or data.get("raw") or f"HTTP {status}"
            return LookupResult(
                contact=contact,
                zip4=None,
                status="error",
                detail=str(message),
            )

        address = data.get("address") or {}
        plus4 = address.get("ZIPPlus4")
        if plus4:
            zip4 = format_zip4(address.get("ZIPCode", contact.zip5), plus4)
            if zip4:
                return LookupResult(contact=contact, zip4=zip4, status="ok")

        return LookupResult(
            contact=contact,
            zip4=None,
            status="not_found",
            detail="USPS did not return ZIP+4",
        )


def load_cache(cache_path: Path) -> dict[str, dict]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_cache(cache_path: Path, cache: dict[str, dict]) -> None:
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cache_key(contact: ContactAddress) -> str:
    addr1, addr2 = split_street(contact.street)
    payload = {
        "street": addr1,
        "secondary": addr2.replace("#", ""),
        "city": contact.city.strip(),
        "state": contact.state.upper().strip(),
        "zip5": contact.zip5,
    }
    return json.dumps(payload, sort_keys=True)


def write_results(output_path: Path, results: list[LookupResult]) -> None:
    fieldnames = [
        "name",
        "address_index",
        "street",
        "city",
        "state",
        "original_zip",
        "zip4",
        "status",
        "detail",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            contact = result.contact
            writer.writerow(
                {
                    "name": contact.name,
                    "address_index": contact.address_index,
                    "street": contact.street,
                    "city": contact.city,
                    "state": contact.state,
                    "original_zip": contact.zip5,
                    "zip4": result.zip4 or "",
                    "status": result.status,
                    "detail": result.detail,
                }
            )


def print_summary(results: list[LookupResult]) -> None:
    ok = sum(1 for result in results if result.status == "ok")
    skipped = sum(1 for result in results if result.status == "skipped")
    failed = len(results) - ok - skipped

    print(f"Processed {len(results)} address(es): {ok} ok, {skipped} skipped, {failed} failed/not found.")
    print()
    print(f"{'Name':<28} {'ZIP+4':<12} Status")
    print("-" * 60)
    for result in results:
        contact = result.contact
        label = contact.name
        if contact.address_index > 1:
            label = f"{label} (addr {contact.address_index})"
        zip4 = result.zip4 or "-"
        print(f"{label:<28} {zip4:<12} {result.status}")


def load_dotenv(env_path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file (no external dependencies)."""
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value

    return values


def resolve_credentials(env_file: Path) -> tuple[str, str]:
    """
    Resolve USPS credentials.

    Priority: exported env vars > script constants > .env file.
    """
    env_values = load_dotenv(env_file)

    client_id = (
        os.environ.get("USPS_CLIENT_ID", "").strip()
        or USPS_CLIENT_ID.strip()
        or env_values.get("USPS_CLIENT_ID", "").strip()
    )
    client_secret = (
        os.environ.get("USPS_CLIENT_SECRET", "").strip()
        or USPS_CLIENT_SECRET.strip()
        or env_values.get("USPS_CLIENT_SECRET", "").strip()
    )
    return client_id, client_secret


def prompt_output_path() -> Path:
    while True:
        try:
            path = input("Output CSV filename: ").strip()
        except EOFError:
            print("No output filename provided.", file=sys.stderr)
            raise SystemExit(1)
        if path:
            return Path(path)
        print("Please enter a filename.", file=sys.stderr)


def resolve_output_path(args: argparse.Namespace) -> Optional[Path]:
    """Return output path when CSV export is requested, else None."""
    if args.output_csv:
        return Path(args.output_csv)
    if args.csv:
        return prompt_output_path()
    return None


def parse_args() -> argparse.Namespace:
    default_input = Path(__file__).resolve().parent / "2026-06-27 13.42.20 - output.csv"

    parser = argparse.ArgumentParser(
        description="Find 5-digit US ZIP contacts and look up ZIP+4 via USPS."
    )
    parser.add_argument(
        "output_csv",
        nargs="?",
        default=None,
        help="Output CSV path (implies --csv; prompts if omitted while --csv is set)",
    )
    parser.add_argument(
        "-i",
        "--input",
        dest="input_csv",
        default=str(default_input),
        help="Tab-delimited Address Book export to read",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Write results to a CSV file (prompts for filename if not provided)",
    )
    parser.add_argument(
        "--cache",
        default=".zip4_cache.json",
        help="Cache file for USPS responses (default: .zip4_cache.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching addresses without calling USPS",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Seconds to wait between USPS requests (default: 0.25)",
    )
    parser.add_argument(
        "--env-file",
        default=str(Path(__file__).resolve().parent / ".env"),
        help="Path to .env file with USPS credentials (default: .env next to this script)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_csv)
    output_path = resolve_output_path(args)
    cache_path = Path(args.cache)

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    contacts = load_contacts(input_path)
    if not contacts:
        print("No USA addresses with 5-digit ZIP codes found.")
        return 0

    print(f"Found {len(contacts)} USA address(es) with 5-digit ZIP only.")

    if args.dry_run:
        for contact in contacts:
            suffix = f" (addr {contact.address_index})" if contact.address_index > 1 else ""
            print(
                f"{contact.name}{suffix}: "
                f"{contact.street}, {contact.city}, {contact.state} {contact.zip5}"
            )
        return 0

    env_file = Path(args.env_file)
    client_id, client_secret = resolve_credentials(env_file)
    if not client_id or not client_secret:
        print(
            "USPS credentials not configured.\n"
            "Use export in this Terminal window, set values at the top of zip4_lookup.py,\n"
            f"or copy .env.example to {env_file.name} and add your credentials there.",
            file=sys.stderr,
        )
        return 1

    client = UspsClient(client_id, client_secret)
    cache = load_cache(cache_path)
    results: list[LookupResult] = []

    for index, contact in enumerate(contacts):
        key = cache_key(contact)
        if key in cache:
            cached = cache[key]
            results.append(
                LookupResult(
                    contact=contact,
                    zip4=cached.get("zip4") or None,
                    status=cached.get("status", "cached"),
                    detail=cached.get("detail", ""),
                )
            )
            continue

        result = client.lookup_zip4(contact)
        results.append(result)
        cache[key] = {
            "zip4": result.zip4,
            "status": result.status,
            "detail": result.detail,
        }
        save_cache(cache_path, cache)

        if index + 1 < len(contacts) and args.delay > 0:
            time.sleep(args.delay)

    print_summary(results)
    if output_path is not None:
        write_results(output_path, results)
        print()
        print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
