#!/bin/bash

test -e /usr/bin/python3 || (
    . /etc/os-release
    case "$NAME" in
        openSUSE*)
            test -e /usr/sbin/transactional-update && transactional-update -n pkg install python3; reboot || zypper --non-interactive in python3
            ;;
        *)
            echo "Unsupported OS: $NAME"
            echo "Manually install python, and open a bug"
            exit 1
            ;;
    esac
)
