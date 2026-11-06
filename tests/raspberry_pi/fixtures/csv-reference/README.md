# Synthetic CSV compatibility fixture

These two files contain 20 deliberately constructed rows each. They preserve the
legacy PCPS/PCSW column layouts, including the earlier diagnostic columns, without
copying any instrument recording. PPS intervals are exactly 16,000,000 ticks;
full swings are 32,000,000 ticks with 400,000-tick blocked intervals. Environmental
values are artificial. The compatibility tests check headers, round trips and
loading by the analysis package.
