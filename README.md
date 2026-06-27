# Address ZIP+4 Lookup

Find USA contacts in a macOS Address Book export whose ZIP code is five digits only, then look up ZIP+4 codes via the [USPS Addresses API](https://developers.usps.com/). The API integration matches the approach used in [gamebits/woocommerce-zip4](https://github.com/gamebits/woocommerce-zip4).

## Requirements

- macOS (or any system with Python 3)
- Python 3.9+
- USPS REST API credentials (`client_id` and `client_secret`)

Register for USPS developer access at [developers.usps.com](https://developers.usps.com/) if you do not already have credentials.

## Setup

1. Export contacts from macOS Contacts (see [AB2CSV export](#macos-contacts-export-with-ab2csv) below), or use any tab-delimited CSV with the expected columns.
2. Place the export in this folder, or pass its path with `-i`.
3. Provide USPS credentials using one of the methods below.

### Credentials

Use **one** of these options. Do not commit real credentials to git.

**Option 1 — `export` in the current Terminal window (recommended for one-off runs)**

```bash
export USPS_CLIENT_ID="your-client-id"
export USPS_CLIENT_SECRET="your-client-secret"
```

These variables exist only in that Terminal session.

**Option 2 — `.env` file**

```bash
cp .env.example .env
```

Edit `.env` and add your credentials:

```
USPS_CLIENT_ID=your-client-id
USPS_CLIENT_SECRET=your-client-secret
```

**Option 3 — inline in the script**

Edit the `USPS_CLIENT_ID` and `USPS_CLIENT_SECRET` constants near the top of `zip4_lookup.py`.

**Priority:** exported env vars → script constants → `.env` file.

## Usage

Preview matching addresses without calling USPS:

```bash
python3 zip4_lookup.py --dry-run
```

Run lookups and print results to the terminal:

```bash
python3 zip4_lookup.py
```

Write results to a CSV file:

```bash
python3 zip4_lookup.py addresses.csv
```

Or prompt for the output filename:

```bash
python3 zip4_lookup.py --csv
```

Use a specific Address Book export as input:

```bash
python3 zip4_lookup.py -i "my-contacts-export.csv" addresses.csv
```

### Options

| Flag | Description |
|------|-------------|
| `-i`, `--input` | Input Address Book export (tab-delimited CSV) |
| `--csv` | Write results to CSV; prompts for filename if not provided |
| `--dry-run` | List matches without calling USPS |
| `--delay` | Seconds between API requests (default: `0.25`) |
| `--cache` | Cache file for USPS responses (default: `.zip4_cache.json`) |
| `--env-file` | Path to `.env` file (default: `.env` next to this script) |

## Output

Terminal output shows each contact name, ZIP+4 (when found), and status.

CSV output (when requested) includes:

- `name`, `address_index`, `street`, `city`, `state`
- `original_zip`, `zip4`, `status`, `detail`

Successful lookups have `status` of `ok`. Rows may also be `skipped` (incomplete address), `not_found`, or `error`.

## macOS Contacts export with AB2CSV

This script works with any properly formatted tab-delimited CSV, but it is designed around exports from **[AB2CSV](https://ab2csv.app)** — a macOS app for exporting Apple Contacts to CSV or vCard.

AB2CSV produces the column layout this script expects (`First`, `Last`, `Street 1`, `City 1`, `State 1`, `Country 1`, `Zip 1`, and additional address slots up to four per contact). That matches how macOS Contacts stores multiple addresses for a single person.

**Typical workflow:**

1. Open [AB2CSV](https://ab2csv.app) and export your contacts as **CSV**.
2. Run the script against that file:

```bash
python3 zip4_lookup.py -i "path/to/ab2csv-export.csv" zip4-results.csv
```

AB2CSV can also export by contact group and handles large address books quickly. Use **vCard** export for other apps; for this script, choose **CSV**.

## Input format

The script expects a tab-delimited export with columns such as `First`, `Last`, `Street 1`, `City 1`, `State 1`, `Country 1`, `Zip 1`, and optional additional address slots (`Street 2` … `Zip 4`).

It selects USA addresses where:

- Country is blank, `United States`, or similar, and
- The ZIP field contains exactly five digits (no ZIP+4 extension).

## Notes

- USPS API responses are cached in `.zip4_cache.json` so re-runs skip already-looked-up addresses.
- Data files (`.csv`, `.tsv`, `.txt`, `.xls`, `.xlsx`) and `.env` are gitignored to keep contact exports and credentials out of version control.
