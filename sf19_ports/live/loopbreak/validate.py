import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # for live_boards
sys.path.insert(0, HERE)

import json, io
from collections import Counter
rows = json.loads(io.open(os.path.join(HERE, "breaks.json")).read())
us = [r for r in rows if r["who"] == "us"]
full = [r for r in us if r["their_mv"] and r["their_cyc"]]
print("attributed to US: %d" % len(us))
print("  of those, THEIR move was known on both visits (so 'them' was truly ruled out): %d" % len(full))
print("  their move missing -> attribution could be wrong: %d" % (len(us) - len(full)))
them = [r for r in rows if r["who"] == "them"]
print("attributed to THEM: %d   (all have both their tokens by construction)" % len(them))
print()
print("STRICT recount, only breaks where all four move tokens are known:")
strict = [r for r in rows if r["their_mv"] and r["their_cyc"] and r["our_mv"] and r["our_cyc"]]
print("  n = %d   %s" % (len(strict), dict(Counter(r["who"] for r in strict))))
print()
print("sanity: on OUR breaks, did their move really match the cycle?")
print("  their_mv == their_cyc :", sum(1 for r in full if r["their_mv"] == r["their_cyc"]), "of", len(full))
print("confirm")
