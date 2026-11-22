# Battery passport extractor

Extract EU battery passport fields from a battery cell datasheet PDF using the Claude API. Every
field comes back with its value, unit, the page it was found on, and a verbatim quote, and a
verification step checks that each quote really appears on that page.

## Setup

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then put your Anthropic API key in .env
```

## Usage

```
python extract.py path/to/datasheet.pdf                 # extract -> outputs/<name>.json
python extract.py path/to/datasheet.pdf --verify        # extract, then check quotes
python extract.py path/to/datasheet.pdf --verify-only   # re-check saved JSON, no API call
```

Batch mode runs every PDF in `documents/` and prints a summary table (also saved to
`outputs/_batch_summary.json`). Files that already have output are reused, so you can resume:

```
python extract.py --all --verify --limit 5   # at most 5 new API calls this run
python extract.py --all --verify             # the rest
python extract.py --all --verify-only        # re-check saved outputs, no API calls
python extract.py --all --force              # re-extract everything
```

Example field in the output:

```json
"nominal_voltage_v": {
  "value": 3.2,
  "unit": "V",
  "page": 6,
  "quote": "Typical Voltage 3.2V"
}
```

If the datasheet doesn't state a value, it is `null` with a `note`; nothing is guessed or calculated.

## Fields (25)

- **Identity:** manufacturer, model, chemistry, cell format
- **Electrical:** nominal / max charge / cut-off voltage, rated capacity, energy, internal resistance,
  max charge and discharge current
- **Physical:** weight, diameter (cylindrical) or width and thickness (prismatic/pouch), height
- **Lifetime:** cycle life (a list of test conditions), charge / discharge / storage temperature ranges
- **Compliance:** certifications

The list is defined in `FIELDS` in [extract.py](extract.py). It is drawn from the Battery Pass data
model and Regulation (EU) 2023/1542 Annex XIII, limited to what datasheets typically contain.

## Datasheets

The PDFs used for development are third-party manufacturer documents and are not included in this
repo. [documents/SOURCES.md](documents/SOURCES.md) lists them; put your own PDFs in `documents/`.

## Limitations

- `--verify` confirms a quote appears on the stated page. It does not confirm the extracted value is
  correct. Spot-check values against the PDF.
- Model output varies between runs, so verify every extraction.
- Only fields a cell datasheet can contain are covered (no carbon footprint, recycled content, etc.).

## License

MIT, see [LICENSE](LICENSE).
