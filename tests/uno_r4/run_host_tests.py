#!/usr/bin/env python3
"""Run the dependency-free host regressions; no board or credentials required."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
tests = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='pendulum-tests-') as directory:
    for source in sorted(tests.glob('*_test.cpp')):
        executable = Path(directory) / source.stem
        extra = []
        if source.name == 'eeprom_config_test.cpp':
            extra = ['-I', str(tests / 'config_stubs')]
        if source.name == 'sd_logger_integration_test.cpp':
            extra = ['-DSDLOGGER_HOST_TEST', '-I', str(tests),
                     str(root / 'Uno.R4.Deprecated/src/SDLogger.cpp')]
        subprocess.run(['c++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                        str(source), *extra, '-o', str(executable)], check=True)
        subprocess.run([str(executable)], check=True)
for script in sorted(tests.glob('*_test.py')):
    subprocess.run([sys.executable, str(script)], check=True, cwd=root)
print('All host regressions passed.')
