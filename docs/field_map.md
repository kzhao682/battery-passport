# Field map: schema.py to the Battery Pass data model

Maps each of the 25 datasheet fields in `schema.py` to the closest attribute in the
[Battery Pass Data Model v1.2.0](https://github.com/batterypass/BatteryPassDataModel)
(General Product Information, Material Composition, Performance and Durability TTL files).

**Caveats**
- This was read from v1.2.0. The project plan's authority is the BatteryPass-Ready **longlist v2.0**
  (Excel, Aug 2026). The v1.2.0 repo is no longer maintained; its successor is "Battery Pass Ready"
  (https://git.gefeg.com/bp/BatteryPassDataModel), which has not been read. Before Day 11, download the
  longlist Excel and re-check names, access levels and Annex XIII references against it.
- v1.2.0 does not mark access levels (public / legitimate interest / authorities) and cites Annex XIII
  only for General Product Information and Material Composition. Those columns are left blank rather
  than guessed.
- Static/dynamic: everything below is static (model data). Dynamic attributes (state of health,
  capacity fade, cycle count, etc.) live in the data model's `batteryCondition` entity and come from the
  BMS and cycling data on Days 8-10, not from datasheets.

| Our field | Data model attribute (v1.2.0) | Unit in data model | Annex XIII ref | Notes |
|---|---|---|---|---|
| general.manufacturer | manufacturerInformation | contact entity | not cited | |
| general.model | none (closest: productIdentifier) | string | not cited | Datasheet-only; used to identify the document |
| general.battery_type | none (batteryCategory is lmt/ev/industrial/stationary, not cell/module/pack) | | | Form factor is datasheet-only |
| general.weight | batteryMass | kg | 1(a) | Datasheets give g or kg: normalize on Day 6 |
| general.dimensions | none | | | Datasheet-only; useful for plausibility (energy density) |
| materials.chemistry | batteryChemistry (shortName, clearName) | string | 1(b) | |
| materials.cathode_material | batteryChemistry / batteryMaterials | string | 1(b), 2(a) | Per-component chemistry |
| materials.anode_material | batteryChemistry / batteryMaterials | string | 1(b), 2(a) | |
| materials.electrolyte | batteryChemistry / batteryMaterials | string | 1(b), 2(a) | |
| performance.rated_capacity | ratedCapacity (+ optional cRate) | Ah | not cited | |
| performance.nominal_voltage | nominalVoltage | V | not cited | |
| performance.min_voltage | minimumVoltage | V | not cited | |
| performance.max_voltage | maximumVoltage | V | not cited | |
| performance.rated_energy | ratedEnergy | kWh | not cited | Datasheets use Wh: convert on Day 6; never calculate in extraction |
| performance.max_charge_current | none | | | Datasheet-only |
| performance.max_discharge_current | none | | | Datasheet-only |
| performance.internal_resistance | initialInternalResistance (batteryComponent: pack/module/cell) | ohm | not cited | Datasheets use mOhm and often a range |
| performance.self_discharge_rate | initialSelfDischarge | % per month | not cited | |
| performance.round_trip_efficiency | roundtripEfficiency | % | not cited | |
| durability.cycle_life | expectedNumberOfCycles (+ cRateLifeCycleTest) | cycles | not cited | Datasheets give several conditions; data model has one number |
| durability.end_of_life_threshold | capacityThresholdForExhaustion (partial match) | % | not cited | Data model definition is narrower (EV SOCE) |
| durability.calendar_life | expectedLifetime | years | not cited | |
| operating_conditions.charge_temperature | none | | | Datasheet-only |
| operating_conditions.discharge_temperature | none | | | Datasheet-only |
| operating_conditions.storage_temperature | temperatureRangeIdleState | degC (-20 to 60) | not cited | Data model has one range; datasheets list several by duration |

## Reading this table

- **18 of 25** fields have a match in v1.2.0 (17 direct or near, plus a partial one,
  `end_of_life_threshold`); **7** are datasheet-only fields with no passport counterpart (model,
  battery_type, dimensions, both current limits, charge and discharge temperature). They stay in the
  schema because plausibility rules and the
  gap agent use them (e.g. energy density from dimensions and weight, power from current limits), not
  because the passport requires them.
- The passport fields datasheets almost never contain (carbon footprint, recycled content, due diligence,
  hazardous substances, labels and certification) are not in `schema.py` and become gaps for the gap
  agent on Day 12.
- Units: the data model uses kg, kWh, ohm, years. `schema.py` records units as stated, so normalization
  (`pint`) is a Day 6 step, not an extraction step.
