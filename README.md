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
python -m battery_passport path/to/datasheet.pdf                 # extract -> outputs/<name>.json
python -m battery_passport path/to/datasheet.pdf --verify        # extract, then check quotes
python -m battery_passport path/to/datasheet.pdf --verify-only   # re-check saved JSON, no API call
```

Batch mode runs every PDF in `documents/` and prints a summary table (also saved to
`outputs/_batch_summary.json`). Files that already have output are reused, so you can resume:

```
python -m battery_passport --all --verify --limit 5   # at most 5 new API calls this run
python -m battery_passport --all --verify             # the rest
python -m battery_passport --all --verify-only        # re-check saved outputs, no API calls
python -m battery_passport --all --force              # re-extract everything
```

`--devset` processes only the 10 files in [evals/devset.json](evals/devset.json) (`make devset`).

Example field in the output (`outputs/<name>.json`, grouped as general / materials / performance /
durability / operating_conditions):

```json
"performance": {
  "nominal_voltage": {
    "value": 3.2, "unit": "V", "page": 6,
    "quote": "Typical Voltage 3.2V", "confidence": "high", "document_id": "LF280K.pdf"
  }
}
```

Values that depend on test conditions (capacity, cycle life, currents) are lists with one entry per
condition. If the datasheet doesn't state a value it is `null`; nothing is guessed or calculated.
Units are kept as stated.

## Fields (25)

- **General:** manufacturer, model, battery type, weight, dimensions
- **Materials:** chemistry, cathode, anode, electrolyte
- **Performance:** rated capacity, nominal / min / max voltage, rated energy, max charge and discharge
  current, internal resistance, self-discharge, round-trip efficiency
- **Durability:** cycle life, end-of-life threshold, calendar life
- **Operating conditions:** charge, discharge and storage temperature ranges

The fields are defined in [battery_passport/schema.py](battery_passport/schema.py) (`python -m battery_passport.schema` prints the JSON Schema).
[docs/field_map.md](docs/field_map.md) maps each one to the Battery Pass data model; the passport
fields datasheets rarely contain (carbon footprint, recycled content, due diligence) are out of scope here.

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
