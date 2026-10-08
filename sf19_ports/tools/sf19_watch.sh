#!/bin/sh

# --- paths, anchored on this script so a fresh checkout runs unchanged
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
WORK="$ROOT/work"
# SPRT watcher for sf19_eval.jsonl: one line per completed pair (tally), trouble lines,
# stops the eval at the first verdict. The eval PID is read from sf19_eval.pid.
S="$WORK"
PY=$ROOT/.venv/bin/python
last=-1; notes=0
while true; do
  pid=$(cat $S/sf19_eval.pid 2>/dev/null)
  ends=$(grep -c '"event":"end"' $S/sf19_eval.jsonl 2>/dev/null || echo 0)
  n=$(cat $S/sf19_seat_logs/*.log 2>/dev/null | grep -c NOTE)
  if [ "$n" != "$notes" ]; then echo "BRIDGE NOTE lines now $n"; grep -h NOTE $S/sf19_seat_logs/*.log | tail -2; notes=$n; fi
  if grep -q -i "forfeit\|panic\|error" $S/sf19_eval.err 2>/dev/null; then echo "STDERR TROUBLE:"; tail -3 $S/sf19_eval.err; fi
  if [ "$ends" != "$last" ]; then
    last=$ends
    out=$($PY $S/sf19_sprt_tally.py 2>&1)
    echo "ends=$ends $(echo "$out" | tr '\n' ' ')"
    if echo "$out" | grep -q "SPRT VERDICT"; then
      [ -n "$pid" ] && pkill -TERM -P $pid; kill $pid 2>/dev/null; echo "stopped eval (caffeinate pid $pid and its bot child) at verdict"
      exit 0
    fi
  fi
  if [ -n "$pid" ] && ! kill -0 $pid 2>/dev/null; then echo "EVAL PROCESS EXITED (ends=$ends)"; tail -2 $S/sf19_eval.err; exit 0; fi
  sleep 20
done
