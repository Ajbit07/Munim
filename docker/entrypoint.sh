#!/bin/sh
# The container generates its own dataset on first start, then runs the command asked for.
set -e

SEED="${MFP_SEED:-42}"
DATA="/app/data/generated/seed-${SEED}"

generate_if_missing() {
    # The story, the Baseline tab and the evaluation all need the clean baseline too.
    if [ ! -f "${DATA}/manifest.json" ]; then
        echo "munim: generating dataset seed ${SEED} (first run, about 35 seconds)"
        # shellcheck disable=SC2086  -- MFP_GENERATE_ARGS is a deliberate argument list
        python generate.py --seed "${SEED}" ${MFP_GENERATE_ARGS}
    fi
    if [ ! -f "${DATA}-baseline/manifest.json" ]; then
        echo "munim: generating the clean baseline for seed ${SEED}"
        # shellcheck disable=SC2086
        python generate.py --seed "${SEED}" --no-leakage ${MFP_GENERATE_ARGS}
    fi
}

case "${1:-serve}" in
    serve)
        generate_if_missing
        echo "munim: ops console on http://localhost:${MFP_PORT:-8000}, merchant chat on /chat"
        exec python serve.py --host 0.0.0.0 --port "${MFP_PORT:-8000}"
        ;;
    demo)
        generate_if_missing
        exec python demo.py --seed "${SEED}"
        ;;
    evaluate)
        generate_if_missing
        exec python evaluate.py --seed "${SEED}"
        ;;
    generate)
        shift
        exec python generate.py --seed "${SEED}" "$@"
        ;;
    test)
        exec python -m pytest
        ;;
    firewall)
        exec python tools/check_firewall.py
        ;;
    *)
        exec "$@"
        ;;
esac
