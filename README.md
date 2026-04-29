# EnergyBridge

EnergyBridge is a multi-agent coordination framework for home–grid interaction, integrating user preferences, grid signals, and control policies into a unified decision-making system.

---

## Overview

Modern energy systems increasingly require tight coordination between residential environments and grid-level signals (e.g., dynamic pricing, demand response, and virtual power plant coordination).    However, existing solutions are often fragmented across control systems, user interfaces, and optimization modules.

EnergyBridge aims to provide a unified framework that:

- bridges **user preferences** and **physical control systems**
- enables **adaptive decision-making under dynamic grid conditions**
- supports **multi-agent coordination** across heterogeneous components

The system is designed as a **modular coordination layer** that can be deployed in real-world home energy management scenarios and extended to larger-scale cyber-physical systems.

---

## Key Features


**Preference-Aware Decision Making**
- Incorporates user preferences into control strategies
- Supports dynamic and context-dependent behavior

**Grid Interaction**
- Handles time-varying signals such as RTP, TOU, and demand response events
- Enables bidirectional interaction with grid operators or aggregators

**Control Integration**
- Interfaces with control modules (e.g., MPC, rule-based systems)
- Supports safe and constrained execution

**Memory & Adaptation**
- Persistent multi user preference modeling
- Context-aware adaptation over time

**Multi-Agent Coordination**
- Modular agents for perception, decision-making, and control
- Extensible to multi-building or grid-level coordination
