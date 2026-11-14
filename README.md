# twap-oracle-sim

Time-weighted average price (TWAP) oracle simulator in pure Python, modelled on the Uniswap v2 oracle design.

A pair contract records the cumulative price at every block; the average price over any window is then just `(cumulative_end - cumulative_start) / (end - start)`. A single-block price spike barely moves a long-window TWAP — which is why lending protocols trust TWAPs over spot prices.

## What it does

- `TWAPOracle.update(price, timestamp)` — record a new market price (timestamps must increase)
- `TWAPOracle.average_price(start, end)` — exact TWAP over any window, with linear interpolation between observations
- `TWAPOracle.consult(amount_in, end, seconds_ago)` — Uniswap v2 `OracleLibrary.consult` style: how much token1 you'd get for `amount_in` token0 at the TWAP
- `TWAPOracle.manipulation_resistance(spike_price, spike_seconds, window_seconds)` — how much a flash spike moves the TWAP

All arithmetic is exact (`fractions.Fraction`), zero dependencies.

## Quick start

```bash
python3 twap_oracle.py            # demo
python3 twap_oracle.py --test     # run the doctests
```

```python
from fractions import Fraction
from twap_oracle import TWAPOracle

o = TWAPOracle()
o.update(Fraction(2), 0)       # price 2 at t=0
o.update(Fraction(4), 100)     # price jumps to 4 at t=100
o.update(Fraction(4), 300)     # still 4 at t=300

o.average_price(0, 300)        # Fraction(10, 3) — 100s at 2, 200s at 4
o.consult(10, 300, 300)        # Fraction(100, 3) token1 for 10 token0
```

## Demo output

```
TWAP over 1h at price 2:       2
10 token0 -> token1 at TWAP:   20
TWAP if price spiked to 200 for 12s: 133/50
A 100x flash spike moves the 1h TWAP from 2 to only 133/50 -- the oracle barely notices it.
```
