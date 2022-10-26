#!/bin/bash

debug_level=0
me=`realpath $0`
my_dir=`dirname $me`
role_dir=`pwd`
options=`getopt -o hf:s -l debug:,help,status -n $0 -- "$@"`
if [[ $? -ne 0 ]] ; then echo "Failed parsing options." >&2 ; exit 1 ; fi

usage (){
   cat <<EOF
This script helps with importing aardsoft provided roles - either from git
or release archives as well as the release generation.

Arguments for importing roles are:

-i|--import     import a role
-d|--directory  import a role from a local directory
-f|--file       import a role from a local file
-r|--role       import the role named <role> from configured search path
-v|--version    import the role in the specified version instead of latest
-s|--status     print overall status information
-t|--target     the target directory for writing the role to

Arguments for releasing are:

EOF
}

red(){
    echo -e "\e[31m$1\e[0m"
}

green(){
    echo -e "\e[32m$1\e[0m"
}

yellow(){
    echo -e "\e[33m$1\e[0m"
}


debug (){
    _level=$1; shift
    echo "$debug_level $_level"
    if [ "$debug_level" -ge "$_level" ]; then
        echo "[D][$_level] $@"
    fi
}

status (){
    _git_dir=`git rev-parse --show-toplevel 2>&1`
    if [ $? -ne 0 ]; then
        red "Invalid git directory:"
        echo  "$_git_dir"
        return
    else
        echo "$_git_dir"
    fi

    echo "status"
}

debug 0 `green "foobar"` baz
debug 2 "baz"

build_galaxy_metadata (){
    if [ -f "$role_dir/meta/main.yml.tpl" ]; then
        _template_source="$role_dir/meta/main.yml.tpl"
    elif [ -f "$my_dir/meta/main_default.yml.tpl" ]; then
        _template_source="$my_dir/meta/main_default.yml.tpl"
    else
        echo "No template found for generating galaxy metadata"
    fi
}

build_role_metadata (){
    echo ""
}

sync_role (){
    pwd
    rsync -avp --exclude=".git*" --delete-excluded --delete "$ROLE_SRC/" "$ROLE_DST"
    VERSION=
    cd $ROLE_SRC
    VERSION=`git describe --tags 2>/dev/null`

    if [ $? -ne 0 ]; then
        VERSION=`git rev-parse --short HEAD 2>/dev/null`
        if [ $? -ne 0 ]; then
            VERSION="unknown"
        fi
    fi

    echo "version: $VERSION" > "$ROLE_DST/role-metadata.yml"
}


# not using eval here breaks whitespace in options
eval set -- $options
while true; do
    _argument=$1; shift
    case "$_argument" in
        --debug)
            debug_level=$1
            shift
            ;;
        -f|--file)
            echo "file: $1"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -s|--status)
            status
            exit 0
            ;;
        --)
            break
            ;;
        *)
            echo "Unknown parameter: $*"
            usage
            exit 1
            ;;
    esac
done
