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

from typing import Optional, Union

from pydantic import BaseModel, Field


# ---------- building blocks ----------

class Range(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None


class Dimensions(BaseModel):
    length: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None


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
    page: Optional[int] = Field(None, description="1-based page number of the quote")
    quote: Optional[str] = Field(
        None,
        description="Exact text copied character for character from ONE table cell or sentence on that page",
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
    dimensions: One = Field(None, description="Length, width, height; note whether terminals are included")


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
    internal_resistance: Many = Field(default_factory=list, description="Initial internal resistance (AC 1 kHz or DC), with method")
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


if __name__ == "__main__":
    # Print the JSON Schema so you can see exactly what Claude will be asked to fill.
    import json

    print(json.dumps(DatasheetExtraction.model_json_schema(), indent=2))
