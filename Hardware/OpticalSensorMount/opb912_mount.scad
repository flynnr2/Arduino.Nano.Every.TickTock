// OPB912W55Z weighted mount. Millimetres. Print flat, as exported.
// Drawing: TT Electronics OPB900-913, Issue A 09/2016, p2 W series.
// Public reference and mechanical inputs: README.md.
// X = emitter-to-receiver; Y = pendulum travel; Z = up.
sensor_count = 1; // [1,2]
sensor_pitch = 18; // centre-to-centre along travel; not a calibrated baseline
show_sensors = true; // preview only: never included in STL
part = "mount"; // [mount,coupon]

base_width = 128;
base_length = max(64, (sensor_count-1)*sensor_pitch + 46);
floor_thickness = 3;
seat_z = 15; // underside of sensor mounting flange
rail_inner = 8.8; // clears 16.38 +/-0.13 underside moulding
rail_outer = 19;
rail_length = (sensor_count-1)*sensor_pitch + 20;
hole_pitch = 25.4;
screw_clearance = 3.4;
nut_af = 5.8;
nut_depth = 3.4; // bottom-open M3 nut recess; bridge above this pocket
tray_width = 35;
tray_wall = 2.4;
tray_depth = 10;
tray_length = base_length-8;
tray_x = base_width/2 - 4 - tray_width/2;
$fn = 64;
eps = 0.02;

assert(sensor_count == 1 || sensor_count == 2, "Use one or two sensors");
assert(sensor_pitch >= 10, "Sensor bodies need separation");
assert(rail_inner > (16.38+0.13)/2, "Underside housing clearance");
assert(tray_x-tray_width/2 > rail_outer+3, "Tray/rail clearance");

module block(w,l,h,z=0) { translate([-w/2,-l/2,z]) cube([w,l,h]); }
module rounded_plate(w,l,h,r=3) {
    hull() for(x=[-w/2+r,w/2-r], y=[-l/2+r,l/2-r])
        translate([x,y,0]) cylinder(r=r,h=h);
}
module fastener(x,y) {
    translate([x,y,-eps]) cylinder(d=screw_clearance,h=seat_z+2*eps);
    translate([x,y,-eps]) rotate([0,0,30])
        cylinder(d=nut_af/cos(30),h=nut_depth+eps,$fn=6);
}
module tray(x) {
    translate([x,0,0]) difference() {
        block(tray_width,tray_length,floor_thickness+tray_depth);
        block(tray_width-2*tray_wall,tray_length-2*tray_wall,
              tray_depth+eps,floor_thickness);
    }
}
module mount() {
    difference() {
        union() {
            rounded_plate(base_width,base_length,floor_thickness);
            for(s=[-1,1]) {
                translate([s*(rail_inner+rail_outer)/2,0,0])
                    block(rail_outer-rail_inner,rail_length,seat_z);
                tray(s*tray_x);
            }
            // Raised cable-tie bridge at the front. Short 8 mm bridge only.
            translate([0,-base_length/2+8,floor_thickness])
                difference() {
                    block(14,5,5);
                    block(8,5+eps*2,2.5,-eps);
                }
        }
        for(i=[0:sensor_count-1], s=[-1,1])
            fastener(s*hole_pitch/2,(i-(sensor_count-1)/2)*sensor_pitch);
    }
}
// Small initial print checks screw, nut, flange pitch and wire clearance.
module coupon() {
    difference() {
        union() {
            block(40,16,floor_thickness);
            for(s=[-1,1]) translate([s*(rail_inner+rail_outer)/2,0,0])
                block(rail_outer-rail_inner,16,seat_z);
        }
        for(s=[-1,1]) fastener(s*hole_pitch/2,0);
    }
}
// Simplified reference envelope, NOT a manufacturer CAD model.
module sensor_reference(y) {
    translate([0,y,seat_z]) color([0.12,0.13,0.15]) {
        difference() {
            hull() for(s=[-1,1]) translate([s*12.7,0,0])
                cylinder(d=6.35,h=2.03);
            for(s=[-1,1]) translate([s*12.7,0,-eps])
                cylinder(d=3.18,h=2.1);
        }
        for(s=[-1,1]) translate([s*(9.53/2+(18.67-9.53)/4),0,2.03])
            block((18.67-9.53)/2,6.35,10.8-2.03);
        block(16.38,4.83,4.83,-4.83);
    }
    for(s=[-1,1]) color("orange")
        translate([s*6.5,y,seat_z-9]) cylinder(d=1.6,h=5);
}

if(part == "coupon") coupon();
else {
    color([0.24,0.42,0.48]) mount();
    if($preview && show_sensors)
        for(i=[0:sensor_count-1])
            sensor_reference((i-(sensor_count-1)/2)*sensor_pitch);
}
