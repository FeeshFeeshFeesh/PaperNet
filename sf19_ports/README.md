# Stockfish 19 ports for Intransitive (rps-v6 rules)

Backup of the session scratchpad work from 2026-09-17 (the scratchpad lives under /private/tmp
and can be wiped). Kept OUTSIDE `sep2026` on purpose: everything here is a derivative of
Stockfish and is GPL-3.0 (see `*/Copying.txt`). Run the engines as separate processes; do not
copy their code into `sep2026`.

- `sf19rps/`: Stockfish 19 with the game rules hand-ported and a 4-line handcrafted eval.
  Perft matches Fairy-Stockfish rps-v6. Lost to sploppy [9,1,0,0,0] pairs (HANDOFF §231bq).
- `sf19nn/`: sf19rps plus `src/rpsnet.h/.cpp`, a C++ re-implementation of sploppy's RPSNNUE1
  inference (MIT, see `LICENSE-sploppy-MIT`) with NEON kernels. Its evaluation equals
  sploppy's Rust code exactly on 5,333 positions. Beat sploppy 20-0 with the same net at
  500 ms/move (HANDOFF §231br).
  UCI options: `EvalFile <path to .nnue>`, `NetScale` (100 = 1:1), `NetVerify` (debug).
- `build.sh`: clang without the Makefile. Its `cd` line points at the old scratchpad path;
  change it to this folder before building. Use `OPT="-O3 -flto" sh build.sh`.
- `tools/`: `nn_parity.py` (parity gates; needs `fen_oracle.rs` built inside sploppy's repo as
  `models/nnue/examples/`), `rps_diff.py` (rules oracle), the RPSI seat bridges used by
  `bot eval`, and the SPRT tally and watcher scripts. All paths inside are the old scratchpad
  paths and need editing.
- `live/`: what the live PaperNet0 bot runs since 2026-09-17 ~23:00 (connect_bot.py
  `ENGINE = "sf19nn"`; rollback `"sploppy"`). `stockfish` is the fixed sf19nn binary,
  `sf19nn_live_seat.py` the RPSI bridge the site client starts per game slot. It reads
  THREADS / MIN_MOVE_MS / MAX_MOVE_MS from `SF19NN_*` environment variables set by
  connect_bot.py and spends sploppy's clock rule (~clock/30 + 0.8 x increment, clamped).
  One log per seat process in `live/logs/`. See HANDOFF §231bt.
- `codex_review/`: the brief and FINDINGS.md of the independent Codex review (09-17). One bug
  (a root where the game is already over still returned a move) was fixed in both ports, along
  with a square-range guard in pseudo_legal. All gates were re-run and passed. See HANDOFF
  "231br/bs CODEX REVIEW RESULT".
