#!/bin/sh
# Build SF19-NN (RPSNNUE1 net) without the Makefile (no net, no tablebases). OPT overridable.
cd "$(dirname "$0")/src" || exit 1
OPT=${OPT:--O2}
SDK=$(xcrun --show-sdk-path)
FLAGS="-std=c++17 $OPT -isysroot $SDK -nostdinc++ -isystem $SDK/usr/include/c++/v1 -fno-exceptions -DNDEBUG -DIS_64BIT -DUSE_POPCNT -mcpu=apple-m1 -ffp-contract=off -pthread -Wall"
mkdir -p ../build
rm -f ../build/*.o ../build/*.log
for f in *.cpp; do
  ( clang++ $FLAGS -c $f -o ../build/$f.o > ../build/$f.log 2>&1 || echo "FAILED $f" ) &
  while [ $(jobs -r | wc -l) -ge 4 ]; do sleep 0.2; done
done
wait
grep -h -E "error|warning" ../build/*.log | sort | uniq | head -60
if clang++ $OPT -isysroot $SDK -pthread -o ../stockfish ../build/*.o > ../build/link.log 2>&1; then echo LINKED; else head -30 ../build/link.log; fi
