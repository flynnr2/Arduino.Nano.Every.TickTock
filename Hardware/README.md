# Hardware designs

The public hardware selection contains the current A.9 Nano Every carrier's
editable KiCad design and selected build files, plus the optical sensor mount.
These are prototypes. CAD checks and fabrication exports do not establish
electrical performance, physical fit or measured timing accuracy.

## Nano Every carrier A.9

Open [the KiCad project](NanoEveryCarrierA9/NanoEveryCarrier.kicad_pro) with
KiCad 10.0.4 or later. Native schematic sheets, PCB, local symbols, footprints
and 3D models are included. Edit these native files in KiCad; private generators
and historical bundles are not required to open them. The local library
[licence](NanoEveryCarrierA9/LICENSE-KiCad-libraries.txt) applies to included
KiCad library material.

- [Schematic PDF](NanoEveryCarrierA9/output/pdf/NanoEveryCarrier-Schematic-A9.pdf)
- [Wired schematic PDF](NanoEveryCarrierA9/output/pdf/NanoEveryCarrier-Schematic-A9-Wired.pdf)
- [Assembly and acceptance guide](NanoEveryCarrierA9/output/pdf/NanoEveryCarrier-Assembly-A9.pdf)
- [PCB-component BOM](NanoEveryCarrierA9/output/assembly/Carrier-A9-BOM.csv)
- [Accessories](NanoEveryCarrierA9/output/assembly/Carrier-A9-Accessories.csv)
- [SMD placements](NanoEveryCarrierA9/output/assembly/Carrier-A9-SMD-positions.csv)
- [Gerber/drill archive](NanoEveryCarrierA9/output/NanoEveryCarrier-A9-Gerbers.zip)

The saved PCB includes the 6 November 2026 via-clearance update. Before building,
read the assembly guide's supply-voltage, GNSS jumper, back-powering, power-path
and acceptance requirements. GNSS and TCXO regulators are separate. Remove JP2
for an independently powered GNSS receiver. Hardware files do not configure the
Nano firmware or the receiver.

Earlier A.6/A.8 wiring discussions describe their named revisions. Use the current
schematic and assembly guide for A.9 connector and power details. The
[system wiring guide](../Docs/Wiring.md) and
[Pi wiring schedule](../Raspberry.Pi/WIRING.md) describe capture connections.

## Optical sensor mount

The [mount guide](OpticalSensorMount/README.md) describes the parametric OpenSCAD
source, printable meshes and fit coupon. Confirm actual sensor dimensions and
pendulum clearances before printing a final mount.
