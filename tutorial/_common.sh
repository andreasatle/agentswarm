# Shared by the tutorial's shell scripts: run from the repository root, print each command before it runs,
# and keep every tutorial project under one folder ($AGENTSWARM_RUNS/tutorial, default ~/agentswarm-runs).
# Project roots must be under your home folder so Docker (Colima, Docker Desktop) can mount them.
set -euo pipefail
tutorial=$(dirname "${BASH_SOURCE[0]}")
cd "$tutorial/.."
RUNS="${AGENTSWARM_RUNS:-$HOME/agentswarm-runs}/tutorial"
mkdir -p "$RUNS"

# Print one argument so the shell reads it back unchanged: as it is when it's a plain word, else in double
# quotes, or single quotes when it contains a character double quotes would expand (or zsh's !).
quoted() {
    case "$1" in
        "" | *[!A-Za-z0-9_./:=@%+,-]*)
            case "$1" in
                *[\"\$\`\\!]*)
                    local quote="'" escaped="'\\''"  # inside single quotes, ' is written as '\''
                    printf " '%s'" "${1//$quote/$escaped}" ;;
                *) printf ' "%s"' "$1" ;;
            esac ;;
        *) printf ' %s' "$1" ;;
    esac
}

# Print a command, quoted so it can be copied and run as it is, then run it.
run() {
    printf '\n+'
    for argument in "$@"; do
        quoted "$argument"
    done
    printf '\n'
    "$@"
}

# Start a step's own folder afresh: only folders under $RUNS, never anything else.
fresh() {
    case "$1" in
        "$RUNS"/*) rm -rf "$1" ;;
        *) echo "refusing to remove $1: not under $RUNS" >&2; exit 1 ;;
    esac
}

# Stop with a hint when an earlier step's project is missing.
needs() {
    if [ ! -e "$1" ]; then
        echo "$1 doesn't exist yet: run $2 first." >&2
        exit 1
    fi
}

# Say where a step's project is, so it can be inspected afterwards.
where() {
    printf '\nThe project is in %s\n' "$1"
}
