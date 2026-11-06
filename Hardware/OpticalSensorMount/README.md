# OPB912W55Z weighted sensor mount

First prototype, dimensioned from the **W-series wire-termination drawing** on
page 2 of the TT Electronics OPB900–OPB913 datasheet, **Issue A, September 2016**.
The [manufacturer's public series datasheet](https://www.ttelectronics.com/TTElectronics/media/ProductFiles/Datasheet/OPB900-913.pdf)
provides the current reference; it may be a later revision. The mechanical inputs
used for this prototype are recorded below so the model can be checked without
the excluded local PDF. Confirm them against the actual sensor and pendulum
envelope before a final print. Do not substitute the L-series solder-lead outline.

## Mechanical reference used by this prototype

All dimensions are millimetres. These are drawing inputs, not measurements of a
particular sensor or a guarantee of printed fit. Nominal geometry is used by the
model; the physical fit coupon is still required.

| Drawing feature                    | Reference dimension | Model use                                        |
| ---------------------------------- | ------------------- | ------------------------------------------------ |
| Mounting-hole centres              | 25.40 ± 0.25        | `hole_pitch = 25.4`                              |
| Mounting-hole diameter             | 3.18 ± 0.13         | 3.4 clearance holes in the printed mount         |
| Flange width                       | 30.99 ± 0.25        | Reference sensor envelope                        |
| Flange thickness                   | 2.03 ± 0.13         | Reference sensor envelope                        |
| Top above the flange seating plane | 10.80 ± 0.25        | Top height is `seat_z + 10.8`                    |
| Optical centre below the top       | 2.79 nominal        | Axis height is `seat_z + 10.8 - 2.79`            |
| Underside moulding width           | 16.38 ± 0.13        | Cleared by the 17.6-wide space between the rails |
| Underside projection below flange  | 4.83 ± 0.25         | Clearance below the 15-high seating plane        |

The 15 mm seating height, 18 mm dual-sensor pitch and base dimensions are mount
design choices. They are not dimensions specified by the sensor manufacturer.
The reference envelope and wire stubs are illustrative; the STL contains only
the printable mount.

## Files and geometry

- `opb912_mount.scad`: editable, parametric OpenSCAD source.
- `opb912_single.stl`: one sensor at the centre.
- `opb912_dual.stl`: two sensors, 18 mm centre-to-centre along travel.
- `opb912_fit_coupon.stl`: a 40 x 16 mm test piece with the same mounting interface.
- `preview-single.png` and `preview-dual.png`: assembled previews; black sensor
  envelopes and orange wire stubs are illustrative and excluded from the STLs.

Both full bases are 128 x 64 mm at the default settings. The flat floor is 3 mm
thick; the sensor flange seats at 15 mm above the table. Nominal optical axis is
23.01 mm above the table (15 + 10.80 - 2.79), with the sensor top at 25.8 mm.
This is a fixed-height starting design: change `seat_z` for the actual clock.
Changing height also changes the required screw length.

X runs from emitter to receiver, Y follows pendulum travel, and Z is vertical.
The two sensors share a straight, unobstructed slot corridor along Y; ballast
sits outside it on both X sides. `sensor_pitch` changes their separation and
the base grows automatically for larger separations. The 18 mm default is a
mechanical starting point, not a calibrated measurement baseline. Confirm that
the shutter reaches both sensors at the smallest swing amplitude of interest.

## Sensor mounting and wiring

Two M3 screws per sensor pass through the original flange holes, on 25.4 mm
centres, into ordinary M3 hex nuts recessed from underneath. Start with M3 x
16 mm screws without washers, then check engagement against your actual nuts
and sensor: the tips must remain above the underside of the base. Printed
clearance holes are 3.4 mm; nut pockets are 5.8 mm across flats, 3.4 mm deep.
Snug the screws gently; the sensor flange should not flex. Avoid spring clips
or a permanently stressed printed clamp for this application.

The space between the rails is 17.6 mm wide. This clears the drawing's
16.38 +/-0.13 mm underside moulding. Its nominal 4.83 mm downward projection
leaves 7.17 mm between the moulding and the floor, allowing the leads to turn
towards either Y edge. The trough is open above and at both ends, so there is
no blind tunnel to thread. Install and route the leads before tightening the
sensor screws. The exact wire bend radius is not specified in the drawing;
check the real wires and increase `seat_z` if needed.

A small bridge near the front edge accepts a narrow cable tie around the
outgoing bundle. Leave slack beneath each sensor and anchor the external cable
so cable drag cannot shift the base. Keep the bundle below the slot floor.
The bridge is not intended as a handle or a highly loaded structural feature.

## Ballast and seating

Each side has a 30.2 x 51.2 x 10 mm internal tray. This accepts coins up to
30 mm diameter laid flat, or steel plates/washers. Add equal mass on both
sides, keep stacks below the rim, and restrain them with a thin removable
cover or a little removable putty so they cannot rattle. Covers are not
included in this prototype. Weigh the assembled unit rather than assuming a
particular coin count guarantees stability.

Use the flat underside on a clean, flat, rigid surface. If necessary use very
thin non-slip patches beneath the outer edges; soft foam feet can introduce
movement. Ballast reduces movement from cable forces but cannot guarantee
position retention. Check for rocking, sliding and pendulum clearance in situ.

## Material and printing

**Preferred for environmental stability: PPS-CF**, printed by a service or
printer equipped for that specific filament. Bambu's material data reports
0.05% saturated water absorption at 25 C / 55% RH and describes high dimensional
stability. This is a moisture-uptake figure, not a dimensional-change figure.
Use the manufacturer's drying, nozzle, bed and chamber requirements; abrasive
fibre-filled filament requires suitable wear-resistant hardware.

**More accessible alternative: PET-CF**, rather than PETG-CF. The manufacturer
identifies low moisture absorption and dimensional stability relative to nylon.
Use unfilled PETG or PLA for an inexpensive fit check if convenient, rather
than treating that print as the final stable reference.

Sources: [PPS-CF manufacturer data](https://bambulab-us.myshopify.com/products/pps-cf)
and [PET-CF manufacturer data](https://bambulab-us.myshopify.com/products/pet-cf).
These are material-specific recommendations, not a guarantee of zero drift.
Print orientation, moisture conditioning, warping and creep matter. The sensor
housing and supporting shelf also move with temperature. For demanding timing
measurements, measure the assembled mount's drift; a machined metal datum may
ultimately be preferable to relying entirely on a polymer baseline.

Print flat-side down, typically 0.2 mm layers, 5-6 walls and 35-50% infill as a
starting point, adjusted to the chosen filament's profile. Keep automatic
supports out of the nut pockets and cable-tie opening: these require only
short bridges. Check their ceilings in the slicer. Dry and condition the print
according to the filament supplier before final fitting. Deburr the screw
holes and remove elephant foot from the nut recesses as required.

Print the coupon first. Check nut fit, both screws, underside housing clearance
and lead bend clearance. Then print the full base and check the entire shutter
sweep by hand before running the clock. No physical fit or stability testing
has yet been performed.

Digital validation: exported all three STLs using OpenSCAD 2021.01 with
`--hardwarnings`; each mesh is watertight and consists of one connected body.
Checked both assembled preview images. STL bounds are 128 x 64 x 15 mm for
each full mount and 40 x 16 x 15 mm for the coupon, excluding sensor hardware.

## OpenSCAD

Open `opb912_mount.scad`; set `sensor_count` to 1 or 2. F5 shows reference
sensors, F6 evaluates only the printable mount. Export STL after F6.
Set `part="coupon"` for the fit-test piece. `show_sensors=false` hides the
reference envelopes in preview. All dimensions are millimetres.
