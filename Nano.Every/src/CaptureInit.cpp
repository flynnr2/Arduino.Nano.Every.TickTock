#include "CaptureInit.h"

// EVSYS channel-to-port quirk (ATmega4809 Table 14-5-2):
// CH0/1: PORT0→PORTA, PORT1→PORTB   <-- our case (IR Sensor on PB0)    
// CH2/3: PORT0→PORTC, PORT1→PORTD   <-- our case (PPS on PD0)
// CH4/5: PORT0→PORTE, PORT1→PORTF
void evsys_init() {
  EVSYS.CHANNEL1 = EVSYS_GENERATOR_PORT1_PIN0_gc;               // channel1 -> PORTB.PIN0 generator
  EVSYS.USERTCB1 = EVSYS_CHANNEL_CHANNEL1_gc;                   // channel1 -> TCB1 for IR sensor edge

  EVSYS.CHANNEL2 = EVSYS_GENERATOR_PORT1_PIN0_gc;               // channel2 -> PORTD.PIN0 generator
  EVSYS.USERTCB2 = EVSYS_CHANNEL_CHANNEL2_gc;                   // channel2 -> TCB2 for PPS edge
}

void tcb0_init_free_running() {
  TCB0.CTRLA    = 0x0;                                          // turn off
  TCB0.CTRLB    = TCB_CNTMODE_INT_gc;                           // "periodic interrupt" mode behaves as free-run with OVF
  TCB0.CCMP     = 0xFFFF;                                       // not used, but keep at max
  TCB0.INTCTRL  = TCB_CAPT_bm;                                  // overflow interrupt on
  TCB0.INTFLAGS = TCB_CAPT_bm | TCB_OVF_bm;                     // clear flags
  TCB0.CTRLA    = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;        // turn on
}

void tcb1_init_IR_capt() {
  TCB1.CTRLA    = 0x0;                                          // turn off
  TCB1.CTRLB    = TCB_CNTMODE_CAPT_gc;                          // capture mode
//TCB1.EVCTRL   = TCB_CAPTEI_bm | TCB_EDGE_bm | TCB_FILTER_bm;  // capture events EDGE = 1, i.e. HIGH -> LOW (open)
  TCB1.EVCTRL   = TCB_CAPTEI_bm | TCB_FILTER_bm;                // capture events EDGE = 0, i.e. LOW -> HIGH (inverted)
  TCB1.INTCTRL  = TCB_CAPT_bm;                                  // capture interrupt
  TCB1.INTFLAGS = TCB_CAPT_bm | TCB_OVF_bm;                     // clear flags
  TCB1.CTRLA    = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;        // clock select still needed for state machine

//PORTD.PIN0CTRL |= PORT_PULLUPEN_bm;                           // Enable pull-up on PD0 - EXTERNAL IS ASSUMED THOUGH
}

void tcb2_init_PPS_capt() {
  TCB2.CTRLA    = 0x0;                                          // turn off
  TCB2.CTRLB    = TCB_CNTMODE_CAPT_gc;                          // capture mode
  TCB2.EVCTRL   = TCB_CAPTEI_bm | TCB_FILTER_bm;                // capture events EDGE = 0
  TCB2.INTCTRL  = TCB_CAPT_bm;                                  // capture interrupt
  TCB2.INTFLAGS = TCB_CAPT_bm | TCB_OVF_bm;                     // clear flags
  TCB2.CTRLA    = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;        // clock select still needed for state machine
}
