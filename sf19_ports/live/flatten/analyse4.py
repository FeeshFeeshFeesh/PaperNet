import json, os, statistics as st
HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "sweep.jsonl"))]
N = sorted({r["ask_nodes"] for r in rows})
idx = {(r["game"], r["ply"], r["arm"], r["ask_nodes"]): r for r in rows}
POS = sorted({(r["game"], r["ply"]) for r in rows})
def med(xs):
    xs=[x for x in xs if x is not None]; return st.median(xs) if xs else float("nan")

print("=== J. with the clock ZEROED, how good are these positions really? (@800k) ===")
for lab, pred in (("clock>=180", lambda c: c>=180), ("clock 119-179", lambda c: c<180)):
    sel=[(g,p) for g,p in POS if idx.get((g,p,"high",N[-1])) and pred(idx[(g,p,"high",N[-1])]["real_clock"])]
    L=[idx[(g,p,"low",N[-1])] for g,p in sel if idx.get((g,p,"low",N[-1]))]
    H=[idx[(g,p,"high",N[-1])] for g,p in sel if idx.get((g,p,"high",N[-1]))]
    for nm, A in (("clock zeroed", L), ("real clock ", H)):
        big=sum(1 for r in A if r["best"] is not None and r["best"]>=1000)
        won=sum(1 for r in A if r["best"] is not None and r["best"]>=200)
        print("  %-13s %-12s n=%2d  median best %+5.0f  |  best>=200 (a stone up) %2d  best>=1000 (winning) %2d"
              % (lab, nm, len(A), med([r["best"] for r in A]), won, big))

print("\n=== K. so WHERE does the real-clock score fall off a cliff? paired high-low, by clock ===")
for lo,hi in ((119,129),(150,159),(160,169),(180,199)):
    sel=[(g,p) for g,p in POS if idx.get((g,p,"high",N[-1])) and lo<=idx[(g,p,"high",N[-1])]["real_clock"]<=hi]
    d=[idx[(g,p,"high",N[-1])]["best"]-idx[(g,p,"low",N[-1])]["best"] for g,p in sel
       if idx.get((g,p,"low",N[-1])) and idx[(g,p,"high",N[-1])]["best"] is not None and idx[(g,p,"low",N[-1])]["best"] is not None]
    print("  clock %3d-%3d  n=%2d   median (real - zeroed) = %+6.0f cp" % (lo,hi,len(d),med(d)))

print("\n=== L. plies of search available vs plies of clock left (high arm, 800k) ===")
print("  clock>=180: plies to the draw <=20, median search depth %.0f -> the draw IS inside the horizon"
      % med([idx[(g,p,"high",N[-1])]["depth"] for g,p in POS
             if idx.get((g,p,"high",N[-1])) and idx[(g,p,"high",N[-1])]["real_clock"]>=180]))
print("  clock<180 : plies to the draw >=21, median search depth %.0f"
      % med([idx[(g,p,"high",N[-1])]["depth"] for g,p in POS
             if idx.get((g,p,"high",N[-1])) and idx[(g,p,"high",N[-1])]["real_clock"]<180]))
for lo,hi in ((119,129),(150,159),(160,169),(195,199)):
    sel=[idx[(g,p,"high",N[-1])] for g,p in POS if idx.get((g,p,"high",N[-1])) and lo<=idx[(g,p,"high",N[-1])]["real_clock"]<=hi]
    if not sel: continue
    print("    clock %3d-%3d : %3.0f plies left, median depth %2.0f, depth>=plies_left in %d of %d  -> spread %3.0f"
          % (lo,hi,200-med([r["real_clock"] for r in sel]), med([r["depth"] for r in sel]),
             sum(1 for r in sel if r["depth"]>=200-r["real_clock"]), len(sel), med([r["spread"] for r in sel])))
