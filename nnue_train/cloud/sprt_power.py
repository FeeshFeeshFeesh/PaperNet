"""How many PAIRS the pre-registered SPRT needs. Written before any game of the WDL match.

Normal approximation to the pentanomial SPRT: with bounds +-A = +-log((1-beta)/alpha),
the log-likelihood ratio gains E[dLLR] = (mu1-mu0)^2 / (2 sigma^2) per pair under either
simple hypothesis, so E[pairs to a bound] ~ A / that.  Pairing (the same opening both ways)
removes part of the opening variance; RHO is that reduction and is the one guess here, so
the table brackets it.
"""
import math

ALPHA = BETA = 0.05
H0_ELO, H1_ELO = 0.0, 20.0
DECISIVE = 0.50          # measured at --opening-plies 24, 500 ms (oc_probe.py, 3/6)
PAIRS_PER_HOUR = 23.0    # the wide match's measured throughput, 3 pairs in flight on one c4a-4
AUD_PER_HOUR = 0.3081


def score_of(elo):
    return 1.0 / (1.0 + 10.0 ** (-elo / 400.0))


def game_variance(decisive):
    """Score variance of ONE game: win/loss each decisive/2, draw otherwise, at a score of 0.5."""
    return 0.25 * decisive + 0.0 * (1 - decisive)   # E[s^2]-E[s]^2 = d/2*1 + (1-d)*0.25 - 0.25


A = math.log((1 - BETA) / ALPHA)
d_mu_pair = 2.0 * (score_of(H1_ELO) - score_of(H0_ELO))
print("bound A = %.3f   H1 score %.4f   pair-mean gap %.4f   decisive %.0f%%"
      % (A, score_of(H1_ELO), d_mu_pair, 100 * DECISIVE))
print()
print(" pairing rho | pair var | E[pairs to a bound] | hours 1 VM | A$ 1 VM | hours 4 VMs | A$ 4 VMs")
for rho in (1.0, 0.8, 0.7, 0.6):
    var_pair = 2.0 * game_variance(DECISIVE) * rho
    llr = d_mu_pair ** 2 / (2.0 * var_pair)
    pairs = A / llr
    h1 = pairs / PAIRS_PER_HOUR
    print("   %.2f       |  %.4f  |        %5.0f        |   %5.1f    |  %5.2f  |    %5.1f    |  %5.2f"
          % (rho, var_pair, pairs, h1, h1 * AUD_PER_HOUR, h1 / 4, h1 * AUD_PER_HOUR))
print()
print("A cap must sit WELL above E[pairs]: the stopping time is skewed, so a cap at the mean")
print("ends a large share of runs in 'not separable', which is the one useless outcome.")
