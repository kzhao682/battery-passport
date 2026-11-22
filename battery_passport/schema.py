"""Passport fields we extract from cell and module datasheets (Day 2).

Field choice follows the "Performance and durability" and general-information
attributes of the EU battery passport (Battery Pass longlist / DIN DKE SPEC 99100),
limited to what a datasheet usually states. Everything else in the passport
(carbon footprint, recycled content, due diligence, unique ID, state of health)
is out of scope here and becomes a gap for the gap agent from Day 11.

Every extracted value carries its evidence: unit, page and a verbatim quote.
Values that depend on test conditions (cycle life, currents) are lists, so
"6000 cycles at 25 C" and "2500 cycles at 45 C" stay separate entries.
"""

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------- building blocks ----------

# extra="forbid" on Range and Dimensions: both are all-optional, so without it
# {"min": 0, "max": 55} could silently validate as an empty Dimensions inside the
# Evidence.value Union.

class Range(BaseModel):
    """A min-max range as stated, e.g. a temperature range or '0.1~0.6 mOhm'."""

    model_config = ConfigDict(extra="forbid")

    min: Optional[float] = None
    max: Optional[float] = None


class Dimensions(BaseModel):
    """Outer dimensions in the unit stated. Cylindrical cells: diameter + length/height.
    Prismatic and pouch cells: length, width (or thickness) and height."""

    model_config = ConfigDict(extra="forbid")

    length: Optional[float] = None
    width: Optional[float] = None
    thickness: Optional[float] = None
    height: Optional[float] = None
    diameter: Optional[float] = None


class Evidence(BaseModel):
    """One value plus where it came from in the document."""

    value: Union[float, str, Range, Dimensions, None] = Field(
        description="The value as stated. null if the document does not state it."
    )
    unit: Optional[str] = Field(None, description="Unit as stated, e.g. 'Ah', 'mOhm', 'degC'")
    conditions: Optional[str] = Field(
        None,
        description="Test or use conditions attached to this value, e.g. '25 degC, 0.5C/0.5C, to 80% capacity'",
    )
    document_id: Optional[str] = Field(
        None, description="Which uploaded document the value came from (filled by the pipeline, not the model)"
    )
    page: Optional[int] = Field(
        None,
        description="Position of the page in the PDF file, first page = 1. Ignore printed page numbers.",
    )
    quote: Optional[str] = Field(
        None,
        description="Exact text copied character for character from ONE table cell or sentence on that page",
    )
    confidence: Optional[Literal["high", "medium", "low"]] = Field(
        None,
        description="high: stated directly in a labelled cell or sentence; medium: needed interpreting "
        "a table layout or drawing; low: ambiguous or contradictory in the document",
    )
    note: Optional[str] = Field(
        None,
        description="Short caveat: a tolerance, 'only a C-rate is given', a conflict between two places. "
        "Never put the value itself here.",
    )


# Shorthand for the two shapes a field can take
One = Optional[Evidence]  # a single value, or null if not in the document
Many = list[Evidence]     # zero or more values, each with its own conditions


# ---------- the passport fields ----------

class GeneralInfo(BaseModel):
    manufacturer: One = Field(None, description="Company that makes the cell or battery")
    model: One = Field(None, description="Model name or part number")
    battery_type: One = Field(None, description="Cell, module or pack; form factor (prismatic, pouch, cylindrical)")
    weight: One = Field(None, description="Mass, unit as stated (g or kg)")
    dimensions: One = Field(None, description="Outer dimensions as an object with any of length, width, thickness, height, diameter "
                    "(cylindrical cells: diameter and height); say in 'note' whether terminals are included")


class Materials(BaseModel):
    chemistry: One = Field(None, description="Cell chemistry, e.g. LFP, NMC811")
    cathode_material: One = Field(None, description="Cathode active material if stated")
    anode_material: One = Field(None, description="Anode active material if stated")
    electrolyte: One = Field(None, description="Electrolyte type if stated")


class Performance(BaseModel):
    rated_capacity: Many = Field(default_factory=list, description="Rated or nominal capacity, with the C-rate it was measured at")
    nominal_voltage: One = Field(None, description="Nominal or typical voltage")
    min_voltage: One = Field(None, description="Minimum voltage, i.e. discharge cut-off voltage")
    max_voltage: One = Field(None, description="Maximum voltage, i.e. charge cut-off voltage")
    rated_energy: One = Field(None, description="Rated energy in Wh or kWh, only if stated, never calculated")
    max_charge_current: Many = Field(default_factory=list, description="Maximum continuous charge current, with conditions")
    max_discharge_current: Many = Field(default_factory=list, description="Maximum continuous and pulse discharge current, with duration and conditions")
    internal_resistance: Many = Field(
        default_factory=list,
        description="Initial internal resistance (AC 1 kHz or DC), with method. If given as a range such "
        "as '0.1~0.6', use a Range value {min, max}",
    )
    self_discharge_rate: One = Field(None, description="Self-discharge or capacity retention during storage")
    round_trip_efficiency: One = Field(None, description="Round-trip energy efficiency if stated")


class Durability(BaseModel):
    cycle_life: Many = Field(default_factory=list, description="Expected lifetime in cycles; one entry per set of test conditions")
    end_of_life_threshold: One = Field(None, description="Capacity at which the cycle life ends, e.g. 80% of initial capacity")
    calendar_life: One = Field(None, description="Expected calendar or design life in years")


class OperatingConditions(BaseModel):
    charge_temperature: One = Field(None, description="Charging temperature range")
    discharge_temperature: One = Field(None, description="Discharging temperature range")
    storage_temperature: Many = Field(default_factory=list, description="Storage temperature ranges, each with its allowed duration")


class DatasheetExtraction(BaseModel):
    """Everything extracted from one datasheet."""

    general: GeneralInfo = Field(default_factory=GeneralInfo)
    materials: Materials = Field(default_factory=Materials)
    performance: Performance = Field(default_factory=Performance)
    durability: Durability = Field(default_factory=Durability)
    operating_conditions: OperatingConditions = Field(default_factory=OperatingConditions)


# ---------- constants shared by extract.py and the eval scripts ----------

# Fields whose value must be a number (or Range/Dimensions), never free text. Evidence.value
# also allows str (for things like chemistry), so this is checked separately in extract.validate().
NUMERIC_PATHS = {
    "general.weight",
    "performance.nominal_voltage",
    "performance.min_voltage",
    "performance.max_voltage",
    "performance.rated_energy",
    "performance.self_discharge_rate",
    "performance.round_trip_efficiency",
    "durability.calendar_life",
    "operating_conditions.charge_temperature",
    "operating_conditions.discharge_temperature",
}


if __name__ == "__main__":
    # Print the JSON Schema so you can see exactly what Claude will be asked to fill.
    import json

    print(json.dumps(DatasheetExtraction.model_json_schema(), indent=2))
