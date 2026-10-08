# Does a board snapshot appear BETWEEN two redraws of the same Result line?
# If so, parse() closes a phantom game carrying a duplicate label.
import re
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
LOG=_ROOT + "/project_files/papernet-status.log"
ANSI=re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
HEADER=re.compile(r"^\s*a\s+b\s+c\s+d\s+e\s+f\s+g\s+h\s+i\s*$")
prev_result=None; rows_since=0; phantom=0; ex=[]
for raw in open(LOG, errors="replace"):
    line=ANSI.sub("", raw.rstrip("\n"))
    if "Result:" in line:
        s=line[line.index("Result:"):].strip()
        if s==prev_result:
            if rows_since>0:
                phantom+=1
                if len(ex)<4: ex.append((rows_since, s[:60]))
        prev_result=s; rows_since=0
        continue
    if HEADER.match(line): rows_since+=1
print("redraws of an IDENTICAL result line that had >=1 board header after the previous copy: %d" % phantom)
for n,s in ex: print("   %2d board(s) in between | %s" % (n,s))
