#!/bin/bash
# Module: run_script — run one local Python script inside the scheduler pod's ml-venv, on demand.
# Sourced by deploy.sh; all variables from common.sh are available here.
# I use this for one-off checks that need the live cluster (pgvector data, the embedding model, MLflow)
# without bypassing deploy.sh. It never runs as part of a normal deploy — only with --run-script.
#
# Usage:  ./scripts/deploy.sh --run-script <local_script.py> [script args...]
#   - Any argument that is an existing local file is copied into the pod and replaced by its pod path,
#     so input files (e.g. a .jsonl) travel with the script.
#   - The script runs from /opt/airflow (so project packages import) with /opt/ml-venv/bin/python.
#   - Its stdout is saved locally under .run_script/ (gitignored); its stderr streams to the terminal.
#   - Env: RUN_SCRIPT_TIMEOUT (seconds, default 1800) caps how long the script may run.

RUN_SCRIPT_POD="airflow-scheduler-0"            # the pod that owns /opt/ml-venv
RUN_SCRIPT_NS="airflow-my-namespace"
RUN_SCRIPT_CONTAINER="scheduler"
RUN_SCRIPT_POD_DIR="/tmp/run_script"           # scratch folder inside the pod, removed afterwards
RUN_SCRIPT_LOCAL_DIR="$PROJECT_ROOT/.run_script"  # where the script's stdout is saved on this machine

_run_script_safe_arg() {
    # Allow only plain characters in arguments so they pass through two remote shells unchanged.
    [[ "$1" =~ ^[A-Za-z0-9._/=:,@+-]+$ ]]
}

_run_script_copy_in() {
    # Stream one local file into the pod's scratch folder over SSH (no temp files left on the EC2 host).
    local _src="$1" _dest="$2"
    ssh "$EC2_HOST" "kubectl exec -i $RUN_SCRIPT_POD -n $RUN_SCRIPT_NS -c $RUN_SCRIPT_CONTAINER -- \
        sh -c 'cat > $_dest'" < "$_src" \
        || { echo "ERROR: --run-script could not copy '$_src' into $RUN_SCRIPT_POD:$_dest"; return 1; }
}

_run_script_cleanup() {
    # Remove the pod scratch folder; a failure here is logged but never hides the script's own result.
    ssh "$EC2_HOST" "kubectl exec $RUN_SCRIPT_POD -n $RUN_SCRIPT_NS -c $RUN_SCRIPT_CONTAINER -- \
        rm -rf $RUN_SCRIPT_POD_DIR" 2>/dev/null \
        || echo "WARNING: --run-script could not remove $RUN_SCRIPT_POD_DIR in $RUN_SCRIPT_POD (harmless; wiped on pod restart)."
}

step_run_script() {
    # Validate inputs, copy the script (and any file arguments) into the pod, run it, save its stdout locally.
    local _script="${1:-}"
    shift || true
    local _timeout="${RUN_SCRIPT_TIMEOUT:-1800}"

    if [ -z "$_script" ] || [ ! -f "$_script" ]; then
        echo "ERROR: --run-script needs an existing local script path (got '${_script}')."
        return 1
    fi
    case "$_script" in
        *.py) ;;
        *) echo "ERROR: --run-script only runs Python files (got '$_script')."; return 1 ;;
    esac
    if ! [[ "$_timeout" =~ ^[0-9]+$ ]] || [ "$_timeout" -lt 1 ]; then
        echo "ERROR: RUN_SCRIPT_TIMEOUT must be a positive whole number of seconds (got '$_timeout')."
        return 1
    fi
    local _base
    _base=$(basename "$_script")
    _run_script_safe_arg "$_base" || { echo "ERROR: script file name '$_base' has unsupported characters."; return 1; }

    echo "=== --run-script: running $_base in $RUN_SCRIPT_POD (ml-venv, timeout ${_timeout}s) ==="

    # Fail early with the known fix if the ml-venv was wiped by a pod restart.
    ssh "$EC2_HOST" "kubectl exec $RUN_SCRIPT_POD -n $RUN_SCRIPT_NS -c $RUN_SCRIPT_CONTAINER -- \
        test -x /opt/ml-venv/bin/python" \
        || { echo "ERROR: /opt/ml-venv is missing in $RUN_SCRIPT_POD. Run ./scripts/deploy.sh --fix-ml-venv first."; return 1; }

    ssh "$EC2_HOST" "kubectl exec $RUN_SCRIPT_POD -n $RUN_SCRIPT_NS -c $RUN_SCRIPT_CONTAINER -- \
        mkdir -p $RUN_SCRIPT_POD_DIR" \
        || { echo "ERROR: --run-script could not create $RUN_SCRIPT_POD_DIR in $RUN_SCRIPT_POD."; return 1; }

    _run_script_copy_in "$_script" "$RUN_SCRIPT_POD_DIR/$_base" || { _run_script_cleanup; return 1; }

    # Copy file arguments into the pod and swap in their pod paths; reject anything unsafe to forward.
    local _remote_args="" _arg _arg_base
    for _arg in "$@"; do
        if [ -f "$_arg" ]; then
            _arg_base=$(basename "$_arg")
            _run_script_safe_arg "$_arg_base" \
                || { echo "ERROR: file name '$_arg_base' has unsupported characters."; _run_script_cleanup; return 1; }
            _run_script_copy_in "$_arg" "$RUN_SCRIPT_POD_DIR/$_arg_base" || { _run_script_cleanup; return 1; }
            _arg="$RUN_SCRIPT_POD_DIR/$_arg_base"
        elif ! _run_script_safe_arg "$_arg"; then
            echo "ERROR: argument '$_arg' has unsupported characters (allowed: letters, digits, . _ / = : , @ + -)."
            _run_script_cleanup
            return 1
        fi
        _remote_args="$_remote_args $_arg"
    done

    mkdir -p "$RUN_SCRIPT_LOCAL_DIR" \
        || { echo "ERROR: could not create local output folder $RUN_SCRIPT_LOCAL_DIR."; _run_script_cleanup; return 1; }
    local _out="$RUN_SCRIPT_LOCAL_DIR/${_base%.py}-$(date -u +%Y%m%dT%H%M%SZ).out"

    # Run it; stdout goes to the local file, stderr streams here, and the exit code is kept for the caller.
    # PYTHONPATH=/opt/airflow is needed because Python puts the SCRIPT's folder (not the working dir) on the
    # import path, so `cd` alone never made project packages like genai/ importable.
    local _rc=0
    ssh "$EC2_HOST" "kubectl exec $RUN_SCRIPT_POD -n $RUN_SCRIPT_NS -c $RUN_SCRIPT_CONTAINER -- \
        sh -c 'cd /opt/airflow && PYTHONPATH=/opt/airflow timeout $_timeout /opt/ml-venv/bin/python $RUN_SCRIPT_POD_DIR/$_base$_remote_args'" \
        > "$_out" || _rc=$?

    _run_script_cleanup
    if [ "$_rc" -eq 124 ]; then
        echo "ERROR: $_base hit the ${_timeout}s timeout (raise RUN_SCRIPT_TIMEOUT if it legitimately needs longer)."
    elif [ "$_rc" -ne 0 ]; then
        echo "ERROR: $_base exited with code $_rc. Partial stdout (if any): $_out"
    else
        echo "=== --run-script: $_base finished. Output saved to $_out ==="
    fi
    return "$_rc"
}
