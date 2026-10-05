"""Run one dedicated receiver service, or exercise capture with replay/demo input."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import secrets
import signal
import threading

from .config import Settings, load_settings, save_settings, settings_to_dict


def main(argv=None):
    parser = argparse.ArgumentParser(description='Arduino Pendulum Timer Raspberry Pi receiver')
    sub = parser.add_subparsers(dest='action', required=True)
    initialize = sub.add_parser('init', help='write a new local configuration; never overwrite an existing file')
    initialize.add_argument('--config', type=Path, default=Path('config.json'))
    initialize.add_argument('--lan', action='store_true', help='listen on all interfaces instead of localhost')
    for name in ('acquire', 'web', 'sensors', 'oled', 'storage', 'replay', 'demo', 'check-config', 'thingspeak', 'analyze', 'views'):
        command = sub.add_parser(name)
        command.add_argument('--config', type=Path, required=True)
        if name == 'thingspeak':
            command.add_argument('--publisher-config', type=Path,
                                 default=Path('/var/lib/pendulum/thingspeak.json'))
            mode = command.add_mutually_exclusive_group()
            mode.add_argument('--dry-run', action='store_true',
                              help='show eligible public data without credentials or network access')
            mode.add_argument('--validate-key', action='store_true',
                              help='write one commissioning status entry to verify the key and channel')
            mode.add_argument('--check-ready', action='store_true',
                              help='check local configuration and key validation receipt without network access')
        if name == 'replay':
            command.add_argument('--input', type=Path, required=True)
            command.add_argument('--pace', type=float, default=0.01, help='seconds between replayed lines')
            command.add_argument('--loop', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'init':
        if args.config.exists():
            parser.error('configuration already exists; edit it or choose another path')
        settings = replace(Settings(), api_token=secrets.token_urlsafe(32),
                           web_host='0.0.0.0' if args.lan else '127.0.0.1')
        save_settings(args.config, settings)
        print(f'Created {args.config}. Administration token is in that file; it is not printed here.')
        return
    settings = load_settings(args.config)
    if args.action == 'thingspeak':
        from .thingspeak import run_thingspeak
        raise SystemExit(run_thingspeak(settings, args.publisher_config,
                                        dry_run=args.dry_run, validate_key=args.validate_key,
                                        check_ready=args.check_ready))
    elif args.action == 'check-config':
        print(json.dumps(settings_to_dict(settings, redact=True), indent=2))
    elif args.action == 'web':
        from .web import run_web
        run_web(settings, args.config)
    elif args.action in ('analyze', 'views'):
        from .analysis import run_analysis
        from .views import run_views
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        (run_analysis if args.action == 'analyze' else run_views)(settings, args.config, stop)
    elif args.action == 'storage':
        from .storage import run_storage
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        run_storage(settings, args.config, stop=stop)
    elif args.action in ('sensors', 'oled'):
        from .peripherals import run_sensors, run_oled
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        (run_sensors if args.action == 'sensors' else run_oled)(settings, stop)
    else:
        from .service import run_acquisition
        if args.action == 'replay' and (args.pace < 0 or args.pace > 60):
            parser.error('--pace must be between 0 and 60 seconds')
        run_acquisition(settings, args.config,
                        source='serial' if args.action == 'acquire' else args.action,
                        replay=getattr(args, 'input', None), pace=getattr(args, 'pace', 0),
                        loop=getattr(args, 'loop', False))


if __name__ == '__main__':
    main()
