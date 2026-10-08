# Wide-arm match: running unattended on cloud (started 2026-09-24 22:23 UTC)

## What is running
`rps-w1` (c4a-standard-4, asia-southeast1-b) is playing the PRE-REGISTERED SPRT:
hidden 768 (`wide768.nnue`) vs hidden 512 (`cont512.nnue`), 500 ms, 1 thread/seat, 3 pairs in
flight, H0 +0 / H1 +20 Elo, alpha=beta=0.05, cap 400 pairs, seed 20260994, opening-plies 24.

- driver: `/var/tmp/rps/wide_cloud.py`, log `/var/tmp/rps/wide_cloud.out`
- journal: `/var/tmp/rps/seq/seq_wide_768_512_500ms.0.jsonl`
- throughput ~23 pairs/hr, so the cap is ~17 h; the SPRT may stop far earlier.

## It stops itself two ways
1. On completion the wrapper runs `sudo shutdown -h +5`, so the meter stops when the match ends.
2. Deadman backstop: `shutdown -h` at **2026-09-24 18:23 UTC** (cancel with `sudo shutdown -c`).

Cost while running: A$0.3081/hr, so a full 17 h cap is about A$5.4.

## Check it
    gcloud compute ssh rps-w1 --zone asia-southeast1-b \
      --command='tail -5 /var/tmp/rps/wide_cloud.out'

The final line is `RESULT wide_768_512_500ms: score ... verdict H0|H1|cap`.

## PRE-REGISTRATION AMENDMENT (made before any real-match game)
`--opening-plies` 8 -> 24. The pilot drew every finished game; `oc_probe.py` measured decisive
rate vs opening depth at the real 500 ms: plies 8 -> 1/6, plies 24 -> 3/6, plies 48 -> 4/6.
At ~10% decisive the SPRT returns H0 at the first look regardless of the truth, i.e. no power.
Nets, clock, threads, hypotheses, cap and seed are all unchanged. 24 over 48 because it clears
the power bar while staying nearer real positions, and paired openings cancel opening bias.
Caveat: 6 games per setting; the 24-vs-48 gap is inside noise.

## NOTHING SHIPS
A human reads the verdict. H1 = width pays, 768 becomes the next shape. H0 = width does not pay,
the capacity axis closes. cap = not separable, stay at 512.
