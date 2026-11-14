"""Time-weighted average price (TWAP) oracle simulator in pure Python.

Modelled on the Uniswap v2 oracle design: a pair contract records the
cumulative price at every block, and anyone can later derive the average
price over any window from two cumulative snapshots.  A single-block price
spike barely moves a long-window TWAP, which is exactly why protocols use
TWAPs as manipulation-resistant price feeds.

All arithmetic is exact (``fractions.Fraction``) so the doctests below are
bit-for-bit reproducible.  Prices are quoted as "token1 per token0"
(e.g. 2 means 1 token0 buys 2 token1).

>>> o = TWAPOracle()
>>> o.update(Fraction(2), 0)          # price 2 at t=0
>>> o.update(Fraction(4), 100)        # price jumps to 4 at t=100
>>> o.update(Fraction(4), 300)        # still 4 at t=300
>>> o.average_price(0, 300)
Fraction(10, 3)
>>> o.average_price(100, 300)
Fraction(4, 1)
>>> o.consult(10, 300, 300)
Fraction(100, 3)
"""

from __future__ import annotations

from fractions import Fraction

__all__ = ["TWAPOracle", "Observation", "PriceSpike"]


class PriceSpike(Exception):
    """Raised when a queried window has no observations to cover it."""


class Observation:
    """One cumulative-price snapshot: ``(timestamp, cumulative)``."""

    __slots__ = ("timestamp", "cumulative")

    def __init__(self, timestamp: int, cumulative: Fraction) -> None:
        self.timestamp = timestamp
        self.cumulative = cumulative

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Observation({self.timestamp}, {self.cumulative})"


class TWAPOracle:
    """A minimal Uniswap-v2-style TWAP oracle.

    Call :meth:`update` whenever the market price changes (once per block
    in a real pair).  The oracle accumulates ``price * elapsed_seconds``
    into a running cumulative price; the average price over any window is
    then just ``(cumulative_end - cumulative_start) / (end - start)``.
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self._cumulative = Fraction(0)
        self._last_timestamp: int | None = None
        self._last_price: Fraction | None = None

    @property
    def latest_timestamp(self) -> int | None:
        """Timestamp of the most recent observation, or ``None``."""
        return self._last_timestamp

    def update(self, price: Fraction, timestamp: int) -> None:
        """Record a new price at ``timestamp`` (must move forward).

        >>> o = TWAPOracle()
        >>> o.update(Fraction(3), 10)
        >>> o.update(Fraction(3), 5)
        Traceback (most recent call last):
        ...
        ValueError: timestamps must increase: 5 <= 10
        >>> o.update(Fraction(0), 20)
        Traceback (most recent call last):
        ...
        ValueError: price must be positive
        """
        if price <= 0:
            raise ValueError("price must be positive")
        if self._last_timestamp is not None:
            if timestamp <= self._last_timestamp:
                raise ValueError(
                    f"timestamps must increase: {timestamp} <= {self._last_timestamp}"
                )
            elapsed = timestamp - self._last_timestamp
            self._cumulative += self._last_price * elapsed  # type: ignore[operator]
        self._last_price = price
        self._last_timestamp = timestamp
        self.observations.append(Observation(timestamp, self._cumulative))

    def _cumulative_at(self, timestamp: int) -> Fraction:
        """Cumulative price at ``timestamp`` (exact snapshot or interpolated).

        Interpolates linearly between the two surrounding observations,
        exactly how an on-chain oracle would read a price at an arbitrary
        past timestamp.
        """
        if not self.observations:
            raise PriceSpike("no observations recorded yet")
        first = self.observations[0]
        if timestamp < first.timestamp:
            raise PriceSpike(
                f"timestamp {timestamp} predates first observation at {first.timestamp}"
            )
        last = self.observations[-1]
        if timestamp > last.timestamp:
            raise PriceSpike(
                f"timestamp {timestamp} is after latest observation at {last.timestamp}"
            )
        for prev, nxt in zip(self.observations, self.observations[1:]):
            if prev.timestamp <= timestamp <= nxt.timestamp:
                if timestamp == prev.timestamp:
                    return prev.cumulative
                if timestamp == nxt.timestamp:
                    return nxt.cumulative
                span = nxt.timestamp - prev.timestamp
                frac = Fraction(timestamp - prev.timestamp, span)
                return prev.cumulative + (nxt.cumulative - prev.cumulative) * frac
        return last.cumulative

    def average_price(self, start: int, end: int) -> Fraction:
        """TWAP of token1-per-token0 over ``[start, end]``.

        >>> o = TWAPOracle()
        >>> o.update(Fraction(1), 0)
        >>> o.update(Fraction(3), 50)
        >>> o.update(Fraction(3), 150)
        >>> o.average_price(0, 150)   # 50s at 1, then 100s at 3
        Fraction(7, 3)
        >>> o.average_price(25, 75)   # interpolated window edges
        Fraction(2, 1)
        >>> o.average_price(100, 100)
        Traceback (most recent call last):
        ...
        ValueError: window must have positive length
        """
        if end <= start:
            raise ValueError("window must have positive length")
        return (self._cumulative_at(end) - self._cumulative_at(start)) / (end - start)

    def consult(self, amount_in: int, end: int, seconds_ago: int) -> Fraction:
        """Amount of token1 you'd get for ``amount_in`` token0 at the TWAP.

        Mirrors Uniswap v2's ``OracleLibrary.consult``: average price over
        the ``seconds_ago`` seconds ending at ``end``.

        >>> o = TWAPOracle()
        >>> o.update(Fraction(2), 0)
        >>> o.update(Fraction(2), 600)
        >>> o.consult(5, 600, 600)
        Fraction(10, 1)
        >>> o.consult(5, 600, 0)
        Traceback (most recent call last):
        ...
        ValueError: seconds_ago must be positive
        """
        if seconds_ago <= 0:
            raise ValueError("seconds_ago must be positive")
        avg = self.average_price(end - seconds_ago, end)
        return avg * amount_in

    def manipulation_resistance(self, spike_price: Fraction, spike_seconds: int,
                                window_seconds: int) -> Fraction:
        """How much a flash spike moves the TWAP over ``window_seconds``.

        A one-block price spike barely dents a long TWAP -- the reason
        lending protocols trust TWAPs over spot prices.

        >>> o = TWAPOracle()
        >>> o.update(Fraction(2), 0)
        >>> o.update(Fraction(2), 3600)
        >>> o.manipulation_resistance(Fraction(200), 12, 3600)
        Fraction(133, 50)
        """
        if spike_seconds <= 0 or window_seconds <= spike_seconds:
            raise ValueError("need 0 < spike_seconds < window_seconds")
        base = self.average_price(0, window_seconds)
        spike_cum = base * (window_seconds - spike_seconds) + spike_price * spike_seconds
        return spike_cum / window_seconds


def demo() -> None:
    """Run a short demo: steady price, a flash spike, and the TWAP verdict."""
    oracle = TWAPOracle()
    oracle.update(Fraction(2), 0)
    oracle.update(Fraction(2), 3600)
    print("TWAP over 1h at price 2:      ", oracle.average_price(0, 3600))
    print("10 token0 -> token1 at TWAP:  ", oracle.consult(10, 3600, 3600))
    print("TWAP if price spiked to 200 for 12s:", oracle.manipulation_resistance(
        Fraction(200), 12, 3600))
    print("A 100x flash spike moves the 1h TWAP from 2 to only",
          oracle.manipulation_resistance(Fraction(200), 12, 3600),
          "-- the oracle barely notices it.")


if __name__ == "__main__":
    import doctest
    import sys

    if "--test" in sys.argv:
        failures, _ = doctest.testmod(verbose=False)
        raise SystemExit(1 if failures else 0)
    demo()
