"""EnergyPlus co-simulation adapter for EnergyBridge.

This package provides a thin adapter layer between the EnergyBridge agent
and EnergyPlus via pyenergyplus.  The agent loop itself is unchanged; this
layer only handles:

- reading EnergyPlus output variables into the home_state dict format
- writing agent control_plan decisions back as EnergyPlus actuators
- managing the EnergyPlus process lifecycle and the VPP event queue
"""
