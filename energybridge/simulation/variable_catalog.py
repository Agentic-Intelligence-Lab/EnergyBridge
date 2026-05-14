"""Catalog of EnergyPlus output variables and actuators used by EnergyBridge.

All EnergyPlus variable/actuator names are centralised here so that
state_reader.py and actuator_writer.py never contain raw strings.

Variable tuples: (variable_name, key_value)
Actuator tuples: (component_type, control_type, actuator_key)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Output variables to request and read
# ---------------------------------------------------------------------------

# (variable_name, key_value)
VARIABLES: dict[str, tuple[str, str]] = {
    # Zone thermal state
    "indoor_temp": ("Zone Mean Air Temperature", "living_unit1"),
    # Outdoor temperature reported at zone level (Zone-type variable, key = zone name)
    # "Zone Outdoor Air Drybulb Temperature" is more reliably accessible via the
    # Python API than the site-level "Site Outdoor Air Drybulb Temperature".
    "outdoor_temp": ("Zone Outdoor Air Drybulb Temperature", "living_unit1"),
    # HVAC *thermal* cooling rate (W) – Coil:Cooling:DX:SingleSpeed in the IDF.
    # NOTE: This is THERMAL output of the refrigerant cycle (heat removed from air),
    # NOT electrical power.  Electrical HVAC ≈ this value / COP (≈3–4).
    "hvac_cooling_thermal_w": ("Cooling Coil Total Cooling Rate", "DX Cooling Coil_unit1"),
    # Whole-building electricity demand: key is "Whole Building" per EnergyPlus convention
    "facility_power_w": ("Facility Total Electricity Demand Rate", "Whole Building"),
    # EV charger electricity rate (W) – ElectricEquipment object in IDF
    "ev_power_w": ("Electric Equipment Electricity Rate", "EV_Charger"),
    # Electric water heater electricity rate (W)
    "ewh_power_w": ("Water Heater Electricity Rate", "Water Heater_Tank_unit1"),
    # Water heater tank temperature (°C) – used by EWH controller logic
    "ewh_tank_temp_c": ("Water Heater Tank Temperature", "Water Heater_Tank_unit1"),
}

# ---------------------------------------------------------------------------
# Actuators to obtain handles for and write
# ---------------------------------------------------------------------------

# (component_type, control_type, actuator_key)
ACTUATORS: dict[str, tuple[str, str, str]] = {
    # HVAC thermostat setpoints – both schedules are Schedule:Compact in the IDF
    "cooling_setpoint": ("Schedule:Compact", "Schedule Value", "cooling_sch"),
    "heating_setpoint": ("Schedule:Compact", "Schedule Value", "heating_sch"),
    # EV charger fraction (0–1 multiplier on 7 kW charger) – Schedule:Constant
    "ev_fraction": ("Schedule:Constant", "Schedule Value", "EV_Charging_Fraction_Control"),
    # Electric water heater setpoint (°C) – Schedule:Constant
    "ewh_setpoint": ("Schedule:Constant", "Schedule Value", "EWH_Setpoint_Control"),
    # Electric water heater availability (0/1) – Schedule:Constant
    "ewh_availability": ("Schedule:Constant", "Schedule Value", "EWH_Availability_Control"),
}
