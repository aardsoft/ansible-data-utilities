#!/bin/zsh
# {{ ansible_managed }}

__clean_shutdown=0

# if shutdown gets started by a signal this may get triggered
# up to three times (INT/TERM, TERM, EXIT) - so make sure the
# shutdown code only executes on the first try
_service_shutdown(){
    if [ "$__clean_shutdown" -eq 0 ]; then
        echo "Shutting down service"
        __clean_shutdown=1
        if ! [ -z "$ExecStop" ]; then
            eval "$ExecStop"
            # better would be checking if everything is down,
            # but that requires going through the process group
            # once support for forking services is implemented
            # this should be doable
            sleep 1
        fi
        kill -TERM $$
    else
        exit
    fi
}

TRAPCHLD(){
    echo "Child exited"
}

# C-c, for debugging
TRAPINT(){
    _service_shutdown
    return 1
}

# kill, indicating shutdown from launchd
TRAPTERM(){
    _service_shutdown
    return 1
}

# exiting script, check if state is clean
TRAPEXIT(){
    _service_shutdown
    return 1
}

if [ -z "$1" ]; then
    echo "Usage: $0 service-name" >&2
    exit 1
fi

_service_dir=/usr/local/etc/launchd-fork-wrapper
_service_config="$_service_dir/$1"

if ! [ -f "$_service_config" ]; then
    echo "Service configuration $_service_config not found" >&2
    exit 1
fi

. "$_service_config"

if [ -z "$Type" ]; then
    echo "Service type empty, using default (oneshot)"
    Type="oneshot"
elif ! [ "$Type" = "oneshot" ] && ! [ "$Type" = "forking" ]; then
    echo "Invalid service type $Type" >&2
    exit 1
fi

if [ "$Type" = "forking" ]; then
    echo "forking services are not yet implemented" >&2
    exit 1
fi

if [ -z "$ExecStart" ]; then
    echo "ExecStart missing" >&2
    exit 1
fi

eval "$ExecStart"
_ret=$?
if [ $_ret -ne 0 ]; then
    echo "Starting service failed with code $_ret" >&2
    exit 1
fi

# wait until something happens
# for a forking service we also should monitor the pid
read
