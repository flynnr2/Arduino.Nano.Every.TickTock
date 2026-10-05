# Functionality Comparison

Status: exploratory / historical note. Not a normative source of current firmware behaviour.

This document focuses on product-to-product feature comparisons. For architecture, filtering strategy, and measurement guidance, see `Universal_Pendulum_Timer_Strategy.md`.

Implementation note: the current Nano Every firmware already covers the capture/CSV/tuning path described in `README.md` (serial `help`/`get`/`set`, PPS-disciplined timing, and pendulum sample logging). Items not reflected in the live firmware should be read as comparison targets or roadmap gaps, not hidden modules elsewhere in the tree.

| Functionality                             | Notes / Subtleties                             | MicroSet | PICTock                            |
| ----------------------------------------- | ---------------------------------------------- | -------- | ---------------------------------- |
| **Beat rate measurement** (BPH, sec/beat) | Core function for all models                   | ✅        | ✅                                  |
| **Beat error measurement**                | Difference between left/right swing intervals  | ✅        | ✅ *(less precise on basic builds)* |
| **Pendulum period measurement**           | Measures individual swing duration             | ✅        | ✅                                  |
| **Rate deviation (sec/day)**              | Shows how far clock is from nominal rate       | ✅        | ✅                                  |
| **Strike counting**                       | Counts chime/strike events per hour/day        | ✅        | ❌                                  |
| **Elapsed time measurement**              | Stopwatch-like mode for events                 | ✅        | ❌                                  |
| **Pendulum length calculation**           | Calculates theoretical length from period      | ✅        | ❌                                  |
| **Tachometer mode**                       | Measures rotations/minute for other mechanisms | ✅        | ❌                                  |
| **Width of tick measurement**             | Duration of acoustic impulse                   | ✅        | ❌                                  |
| **Light/dark detection**                  | Measures optical signal duty cycle             | ✅        | ❌                                  |
| **Temperature logging**                   | Uses optional sensor; can compensate rate      | ✅        | ❌                                  |
| **Barometric pressure logging**           | Uses optional sensor; can compensate rate      | ✅        | ❌                                  |
| **GPS timebase**                          | High-precision reference clock                 | ✅        | ❌                                  |
| **Accutron/tuning fork measurement**      | Measures frequency of tuning fork watches      | ✅        | ❌                                  |
| **Running average / stat. smoothing**     | Averages over set interval for stability       | ✅        | ✅ *(basic averaging)*              |
| **Data logging to PC**                    | Stores long-term measurements for analysis     | ✅        | ⚠️ *(limited/manual capture)*      |
| **Graphical display of results**          | PC software shows timing graphs                | ✅        | ❌                                  |
| **LCD/LED onboard display**               | Shows live readings                            | ✅        | ✅                                  |
