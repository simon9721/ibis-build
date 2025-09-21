```mermaid
flowchart TD
  A[[Start]] --> B{[Model] → Model_type?}

  %% --- BRANCH 1: Input-like types (must have Vinl/Vinh) ---
  B -->|Input / I/O / I/O_open_drain / I/O_open_sink / I/O_open_source| C{Are Vinl & Vinh present?}
  C -->|Yes| C1[OK. Record Vinl & Vinh under [Model].]
  C1 --> C2{Do you also need detailed thresholds?}
  C2 -->|Yes| C3[[Add optional [Receiver Thresholds] with Vth/Vinh_ac/Vinl_ac/Vinh_dc/Vinl_dc, etc.]]
  C3 --> C4[[Continue with IV/VT data, [Ramp], test loads, etc.]]
  C2 -->|No| C4
  C -->|No| C5[Parser assumes defaults: Vinl=0.8 V, Vinh=2.0 V (with warning).]
  C5 --> C4

  %% --- BRANCH 2: ECL input-like types (also need Vinl/Vinh) ---
  B -->|Input_ECL / I/O_ECL| D{Are Vinl & Vinh present?}
  D -->|Yes| D1[OK. Record Vinl & Vinh.]
  D1 --> D2[[ECL conventions apply for pull structures later.]]
  D2 --> D3[[Continue with IV/VT data, [Ramp], etc.]]
  D -->|No| D4[Parser assumes defaults: Vinl=0.8 V, Vinh=2.0 V (with warning).]
  D4 --> D3

  %% --- BRANCH 3: Output-like types (no Vinl/Vinh) ---
  B -->|Output / 3-state| E[No Vinl/Vinh in [Model].]
  E --> E1[[Provide [Pullup]/[Pulldown] I–V and waveforms; 3-state adds enable behavior.]]

  %% --- BRANCH 4: Open types (pull side rules) ---
  B -->|Open_drain / Open_sink| F[Output has OPEN side and SINKS current.]
  F --> F1[[Do NOT use [Pullup] (or set all Pullup currents to 0).]]
  F1 --> F2[[Use [Pulldown], clamps, VT/Ramp as applicable.]]

  B -->|Open_source| G[Output has OPEN side and SOURCES current.]
  G --> G1[[Do NOT use [Pulldown] (or set all Pulldown currents to 0).]]
  G1 --> G2[[Use [Pullup], clamps, VT/Ramp as applicable.]]

  %% --- BRANCH 5: Terminator (analog only) ---
  B -->|Terminator| H[Input-only analog element; no digital thresholds.]
  H --> H1[[Model analog loading (caps, diodes, resistors).]]

  %% --- BRANCH 6: Series elements ---
  B -->|Series / Series_switch| I[Series modeling only; no Vinl/Vinh.]
  I --> I1[[Use [R Series], [L Series], [C Series], [Series MOSFET], etc.; define tables/limits.]]

  %% --- BRANCH 7: True differential model types ---
  B -->|Input_diff / Output_diff / I/O_diff / 3-state_diff| J[Define via [External Model] (true differential).]
  J --> J1[[Hook up ports; add D_to_A/A_to_D if using SPICE/IBIS-ISS/V*-A(MS).]]
  J1 --> J2{Need logic thresholds at the converter boundary?}
  J2 -->|Yes| J3[[Set A_to_D vlow/vhigh (e.g., Vinl/Vinh values) in converter params.]]
  J2 -->|No| J4[[Proceed with analog/digital co-modeling as designed.]]

  %% --- COMMON: Test loads & timing references ---
  C4 --> K[[Set timing test loads / references: Rref/Cref/Vref and rising/falling variants as needed.]]
  D3 --> K
  E1 --> K
  F2 --> K
  G2 --> K
  H1 --> K
  I1 --> K
  J3 --> K
  J4 --> K

  %% --- END ---
  K --> Z[[Finish: Validate with Golden Parser; fix warnings/errors; iterate.]]
```
