#!/usr/bin/env python3
"""Test the production C++ reconstruction math and optional linked AVR read pairs.

No simulator claim: hardware reads are replaced by a clock model; the actual
production arithmetic/overflow branch is compiled by the host C++ compiler.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'Nano.Every/src/PendulumCapture.cpp'
CONFIG = ROOT / 'Nano.Every/src/CaptureEvctrl.h'


def check_disassembly(path):
    instructions = []
    for line in path.read_text().splitlines():
        match = re.match(r'\s*[0-9a-f]+:\s+(?:[0-9a-f]{2}\s+)+\s*([a-z]+)\s+([^;]*)', line)
        if match:
            instructions.append((match[1], match[2].strip()))
    for address in (0xA9A, 0xAAA):
        expected = (0xA8A, 0xA8B, address, address + 1)
        count = 0
        for i in range(len(instructions) - 3):
            block = instructions[i:i + 4]
            if all(m == 'lds' and re.search(r',\s*0x' + format(a, '04X') + r'\b', operands)
                   for (m, operands), a in zip(block, expected)):
                regs = [operands.split(',')[0] for _, operands in block]
                assert len(set(regs)) == 4, f'overlapping output registers: {block}'
                count += 1
        assert count == 2, f'expected initial/retry pairs for 0x{address:X}, found {count}'
        print(f'AVR TCB{1 if address == 0xA9A else 2}: both four-LDS pairs verified (6-tick skew)')


def host_test():
    source = SOURCE.read_text()
    structure = source[source.index('struct CaptureMathResult {'):source.index('// Read both counters')]
    start = source.index('template <uint16_t CaptureCntAddress, uint8_t CaptureEvctrl>\nstatic inline CaptureMathResult')
    math = source[start:source.index('\nstatic inline void resetCaptureSoftwareState()', start)]
    skew = re.search(r'constexpr uint8_t CAPTURE_SAMPLE_SKEW_TICKS = (\d+)U;', source)[1]
    harness = r'''
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include "CaptureEvctrl.h"
static uint16_t tcb0Ovf;
static uint32_t coherentOvfFlagSeenCount, coherentOvfAppliedCount;
static uint64_t tick, softwareEpoch, lastCaptureSample, flagSample;
static unsigned reads, flagAdvance, retryDelay;
static int counterPhase, earlyFlag;
constexpr uint8_t TCB_CAPT_bm = 1;
struct FlagRegister {
  operator uint8_t() const {
    // Model flag recognition on any cycle of the LDS. An early flag at TOP
    // (one cycle before reset) is tested as well as at wrap.
    tick += flagAdvance;
    flagSample = tick;
    bool pending = tick + earlyFlag >= (softwareEpoch + 1) * 65536;
    tick += retryDelay;
    return pending ? 1 : 0;
  }
};
static struct { FlagRegister INTFLAGS; } TCB0;
template <uint16_t Address>
void read_capture_counter_pair(uint16_t& low, uint16_t& local) {
  low = uint16_t(tick);
  lastCaptureSample = tick + 6;
  local = uint16_t(lastCaptureSample + counterPhase);
  tick += 12;
  ++reads;
}
'''
    harness += structure + f'constexpr uint8_t CAPTURE_SAMPLE_SKEW_TICKS = {skew}U;\n' + math
    harness += r'''
template <uint16_t Address, uint8_t Evctrl, unsigned FilterDelay>
int sweep() {
  uint64_t cases = 0, captureWraps = 0, wrapsAfterFlag = 0, fullWraps = 0;
  const unsigned epochs[] = {0, 1, 0xFFFF, 0x10000};
  const unsigned ages[] = {0, 1, 151, 65000};
  const int phases[] = {0, -118, 65530};
  for (unsigned epoch : epochs)
    for (unsigned phase = 0; phase != 65536; ++phase)
      for (unsigned age : ages)
        for (int offset : phases)
          for (int pending = 0; pending <= 1; ++pending) {
            // A pending overflow may not survive into a second whole period.
            if (pending && (epoch == 0 || phase > 65400)) continue;
            tick = uint64_t(epoch) * 65536 + phase;
            const uint64_t firstSample = tick;
            const uint64_t hardwareEdge = tick - age;
            const uint64_t physicalEdge = hardwareEdge - FilterDelay;
            softwareEpoch = epoch - pending;
            tcb0Ovf = uint16_t(softwareEpoch);
            counterPhase = offset;
            flagAdvance = phase % 3;
            retryDelay = 4 + phase % 7;
            earlyFlag = phase % 2;
            reads = 0;
            coherentOvfFlagSeenCount = coherentOvfAppliedCount = 0;
            const uint16_t captured = uint16_t(hardwareEdge + offset);
            CaptureMathResult result = capture_math_from_regs_isr_only<Address, Evctrl>(captured);
            if (result.now32 != uint32_t(lastCaptureSample) ||
                result.edge32 != uint32_t(physicalEdge) ||
                result.latency16 != lastCaptureSample - hardwareEdge ||
                uint16_t(captured + result.latency16) != uint16_t(lastCaptureSample + offset) ||
                uint32_t(result.now32 - result.edge32) != result.latency16 + FilterDelay ||
                coherentOvfFlagSeenCount != reads - 1 ||
                coherentOvfAppliedCount != reads - 1 ||
                tcb0Ovf != uint16_t(softwareEpoch)) {
              std::cerr << "FAIL epoch=" << epoch << " phase=" << phase
                        << " pending=" << pending << " age=" << age << '\n';
              return 1;
            }
            if (uint16_t(firstSample + offset) > uint16_t(firstSample + offset + 6)) ++captureWraps;
            if (reads == 1 && flagSample / 65536 != tick / 65536) ++wrapsAfterFlag;
            if (firstSample / 0x100000000ULL != lastCaptureSample / 0x100000000ULL) ++fullWraps;
            ++cases;
          }
  if (!captureWraps || !wrapsAfterFlag || !fullWraps) return 2;
  std::cout << "PASS " << cases << " production C++ reconstruction cases, EVCTRL="
            << unsigned(Evctrl) << ", delay=" << FilterDelay << '\n'
            << "Includes capture counter wraps between paired reads: " << captureWraps
            << "; TCB0 wraps after flag sample: " << wrapsAfterFlag
            << "; full 32-bit wraps: " << fullWraps << '\n';
  return 0;
}

template <uint16_t Address, uint8_t Evctrl, unsigned FilterDelay>
CaptureMathResult project(uint64_t physicalEdge) {
  const uint64_t hardwareEdge = physicalEdge + FilterDelay;
  tick = hardwareEdge + 151;
  softwareEpoch = tick / 65536;
  tcb0Ovf = uint16_t(softwareEpoch);
  counterPhase = -118;
  flagAdvance = retryDelay = earlyFlag = 0;
  return capture_math_from_regs_isr_only<Address, Evctrl>(uint16_t(hardwareEdge + counterPhase));
}

int main() {
  if (sweep<0xA9A, EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH, EXPECTED_IR_DELAY>() ||
      sweep<0xA9A, EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW, EXPECTED_IR_DELAY>() ||
      sweep<0xAAA, EVCTRL_PPS_CAPTURE, 0>()) return 1;
  // Independent physical-edge oracle spans both timer and timeline rollover.
  // Same-input PPS and both IR polarities must agree; intervals must be intact.
  const uint64_t edges[] = {0, 1, 2, 3, 4, 65533, 65535, 65536,
                           0xFFFFFFFDULL, 0xFFFFFFFFULL, 0x100000000ULL};
  for (uint64_t edge : edges) {
    const auto rising = project<0xA9A, EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH, EXPECTED_IR_DELAY>(edge);
    const auto falling = project<0xA9A, EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW, EXPECTED_IR_DELAY>(edge);
    const auto pps = project<0xAAA, EVCTRL_PPS_CAPTURE, 0>(edge);
    const auto next = project<0xA9A, EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW, EXPECTED_IR_DELAY>(edge + 20000000);
    if (rising.edge32 != uint32_t(edge) || falling.edge32 != pps.edge32 ||
        rising.edge32 != pps.edge32 || uint32_t(next.edge32 - rising.edge32) != 20000000) return 3;
  }
  std::cout << "PASS physical-edge alignment, both polarities, intervals and rollover\n";
}
'''
    # Unsigned underflow is intentional for edge32, but latency comparison must
    # use modular subtraction as well, matching the modeled timeline.
    with tempfile.TemporaryDirectory(prefix='capture-reconstruction-') as temp:
        path = Path(temp)
        (path / 'check.cpp').write_text(harness)
        (path / 'avr').mkdir()
        # Only register constants are stubbed; configuration and projection are
        # production C++. Keep the disabled variant local to this test directory.
        (path / 'avr/io.h').write_text('''#include <cstdint>
constexpr uint8_t TCB_CAPTEI_bm = 0x01, TCB_EDGE_bm = 0x10, TCB_FILTER_bm = 0x40;
constexpr uint8_t TCB_CLKSEL_gm = 0x06, TCB_CLKSEL_CLKDIV1_gc = 0x00, TCB_ENABLE_bm = 0x01;
''')
        config = CONFIG.read_text()
        disabled = config.replace(
            'EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH = TCB_CAPTEI_bm | TCB_FILTER_bm;',
            'EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH = TCB_CAPTEI_bm;')
        assert disabled != config, 'Update the disabled-config fixture for the current IR configuration'
        for header, delay in ((config, 4), (disabled, 0)):
            (path / 'CaptureEvctrl.h').write_text(header)
            subprocess.run(['c++', '-std=c++11', '-O2', '-I', str(path),
                            f'-DEXPECTED_IR_DELAY={delay}', str(path / 'check.cpp'),
                            '-o', str(path / 'check')], check=True)
            subprocess.run([str(path / 'check')], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--disassembly', type=Path, help='avr-objdump -d -C output for the linked sketch')
    parser.add_argument('--build-dir', type=Path, help='Arduino build directory; discover the selected objdump from compile_commands.json')
    args = parser.parse_args()
    host_test()
    if args.disassembly:
        check_disassembly(args.disassembly)
    if args.build_dir:
        commands = json.loads((args.build_dir / 'compile_commands.json').read_text())
        compiler = Path(commands[0]['arguments'][0])
        objdump = compiler.with_name('avr-objdump')
        binaries = list(args.build_dir.glob('*.elf'))
        if len(binaries) != 1:
            raise ValueError('Expected exactly one linked ELF in the build directory')
        listing = subprocess.check_output([str(objdump), '-d', '-C', str(binaries[0])], text=True)
        with tempfile.TemporaryDirectory(prefix='capture-assembly-') as temp:
            path = Path(temp) / 'linked.disasm'
            path.write_text(listing)
            check_disassembly(path)
