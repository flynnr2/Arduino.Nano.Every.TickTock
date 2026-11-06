#!/usr/bin/env bash
# Shared optional-publisher lifecycle. Sourcing this file does not change services.

thingspeak_stop_for_upgrade() {
    thingspeak_was_enabled=0
    case "$(systemctl is-enabled pendulum-thingspeak.timer 2>/dev/null || true)" in
        enabled|enabled-runtime) thingspeak_was_enabled=1 ;;
    esac
    local unit
    for unit in pendulum-thingspeak.timer pendulum-thingspeak.service; do
        if [[ $(systemctl show "$unit" --property=LoadState --value) != not-found ]]; then
            systemctl stop "$unit"
        fi
    done
}

thingspeak_resume_after_upgrade() {
    # Only locally verify the receipt; an upgrade must not create a cloud entry.
    if [[ ${thingspeak_was_enabled:-0} -eq 1 ]]; then
        if runuser -u pendulum -- /opt/pendulum/venv/bin/python -m pendulum_pi thingspeak \
            --config /var/lib/pendulum/config.json \
            --publisher-config /var/lib/pendulum/thingspeak.json --check-ready; then
            if ! systemctl start pendulum-thingspeak.timer; then
                echo 'Could not resume optional ThingSpeak timer; local services are running.' >&2
            fi
        else
            systemctl disable pendulum-thingspeak.timer >/dev/null 2>&1 || true
            echo 'Optional ThingSpeak timer left disabled: current configuration/key is not validated.' >&2
        fi
    fi
}

thingspeak_validate_and_enable() {
    # Recommissioning must not leave a timer using a rejected replacement key.
    systemctl disable --now pendulum-thingspeak.timer || return 1
    systemctl stop pendulum-thingspeak.service || return 1
    if ! timeout --kill-after=2s 20s runuser -u pendulum -- \
        /opt/pendulum/venv/bin/python -m pendulum_pi thingspeak \
        --config /var/lib/pendulum/config.json \
        --publisher-config /var/lib/pendulum/thingspeak.json --validate-key; then
        echo 'ThingSpeak validation failed or timed out; the hourly timer remains disabled.' >&2
        return 1
    fi
    if ! runuser -u pendulum -- /opt/pendulum/venv/bin/python -m pendulum_pi thingspeak \
        --config /var/lib/pendulum/config.json \
        --publisher-config /var/lib/pendulum/thingspeak.json --check-ready; then
        echo 'ThingSpeak configuration is not ready/enabled; the hourly timer remains disabled.' >&2
        return 1
    fi
    systemctl enable --now pendulum-thingspeak.timer || return 1
    echo 'ThingSpeak key validated; the optional hourly timer is enabled.'
}
