flowchart TD
  A([Start]) --> B{Model_type?}

  B -->|Input / IO / IO_open_drain / IO_open_sink / IO_open_source| C{Vinl & Vinh defined?}
  C -->|Yes| C1[Record Vinl and Vinh]
  C -->|No| C2[Defaults used: Vinl=0.8V, Vinh=2.0V (warn)]
  C1 --> C3[Optionally add Receiver Thresholds]
  C2 --> C3
  C3 --> K

  B -->|Input_ECL / IO_ECL| D{Vinl & Vinh defined?}
  D -->|Yes| D1[Record Vinl and Vinh]
  D -->|No| D2[Defaults used (warn)]
  D1 --> D3[Continue with IV/VT/Ramp]
  D2 --> D3
  D3 --> K

  B -->|Output / 3-state| E[No Vinl/Vinh]
  E --> E1[Provide Pullup/Pulldown IV and VT; 3-state adds enable]
  E1 --> K

  B -->|Open_drain / Open_sink| F[OPEN side; sinks current]
  F --> F1[Do NOT include Pullup (or set to 0)]
  F1 --> F2[Use Pulldown, clamps, VT/Ramp]
  F2 --> K

  B -->|Open_source| G[OPEN side; sources current]
  G --> G1[Do NOT include Pulldown (or set to 0)]
  G1 --> G2[Use Pullup, clamps, VT/Ramp]
  G2 --> K

  B -->|Terminator| H[Analog-only input element; no thresholds]
  H --> K

  B -->|Series / Series_switch| I[Series elements only; no Vinl/Vinh]
  I --> I1[Use R/L/C Series, Series MOSFET, etc.]
  I1 --> K

  B -->|*_diff types| J[Use External Model (true differential)]
  J --> J1[Connect ports; use D_to_A / A_to_D if needed]
  J1 --> J2{Need logic thresholds at converter?}
  J2 -->|Yes| J3[Set A_to_D vlow/vhigh]
  J2 -->|No| J4[Proceed]
  J3 --> K
  J4 --> K

  K([Finish]) --> Z[Set test loads (Rref/Cref/Vref); run golden parser]
