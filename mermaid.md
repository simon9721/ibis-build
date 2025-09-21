```mermaid
flowchart TD
  A([Start]) --> B{Model_type?}

  B -->|Input / IO / IO_open_drain / IO_open_sink / IO_open_source| C{Vinl and Vinh present?}
  C -->|Yes| C1[Record Vinl and Vinh]
  C -->|No| C2[Use defaults: Vinl=0.8 V, Vinh=2.0 V]
  C1 --> C3[Optional: add Receiver Thresholds]
  C2 --> C3[Optional: add Receiver Thresholds]
  C3 --> K

  B -->|Input_ECL / IO_ECL| D{Vinl and Vinh present?}
  D -->|Yes| D1[Record Vinl and Vinh]
  D -->|No| D2[Use defaults (warning)]
  D1 --> D3[Proceed with IV / VT / Ramp]
  D2 --> D3[Proceed with IV / VT / Ramp]
  D3 --> K

  B -->|Output / 3-state| E[No Vinl or Vinh]
  E --> E1[Provide Pullup / Pulldown IV and VT]
  E1 --> K

  B -->|Open_drain / Open_sink| F[Open side; sinks current]
  F --> F1[Do not include Pullup (or set Pullup currents to 0)]
  F1 --> F2[Use Pulldown, clamps, VT, Ramp]
  F2 --> K

  B -->|Open_source| G[Open side; sources current]
  G --> G1[Do not include Pulldown (or set Pulldown currents to 0)]
  G1 --> G2[Use Pullup, clamps, VT, Ramp]
  G2 --> K

  B -->|Terminator| H[Analog-only input; no thresholds]
  H --> K

  B -->|Series / Series_switch| I[Series elements only; no Vinl or Vinh]
  I --> I1[Use R Series, L Series, C Series, Series MOSFET]
  I1 --> K

  B -->|Diff types (Input_diff / Output_diff / IO_diff / 3-state_diff)| J[Use External Model]
  J --> J1[Connect ports; use D_to_A / A_to_D if needed]
  J1 --> J2{Need logic thresholds at converter?}
  J2 -->|Yes| J3[Set A_to_D vlow and vhigh]
  J2 -->|No| J4[Proceed]
  J3 --> K
  J4 --> K

  K([Finish]) --> Z[Set test loads (Rref / Cref / Vref) and run golden parser]
