#pragma once

#include <avr/io.h>

void evsys_init();
void tcb0_init_free_running();
void tcb1_init_IR_capt();
void tcb2_init_PPS_capt();

#ifndef TCB_OVF_bm
  #define TCB_OVF_bm (1<<1)
#endif

