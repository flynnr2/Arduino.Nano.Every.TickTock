"""Display-only limits for time-series plots; never filter analysis populations."""
import numpy as np


def time_series_limits(values, groups=None):
    """Padded central 99% range, taking the union when phases are supplied.

    Small populations retain their full range. Per-phase fences prevent the
    regular impulse band from being mistaken for an isolated extreme.
    """
    values = np.asarray(values, float)
    finite = np.isfinite(values)
    if finite.sum() < 20:
        return None
    labels = np.zeros(len(values), int) if groups is None else np.asarray(groups)
    bounds = []
    for label in np.unique(labels[finite]):
        sample = values[finite & (labels == label)]
        if len(sample) < 20:
            bounds.append((sample.min(), sample.max()))
        else:
            bounds.append(tuple(np.quantile(sample, [.005, .995])))
    low = min(pair[0] for pair in bounds)
    high = max(pair[1] for pair in bounds)
    pad = max((high-low)*.1, 1e-3)
    low, high = low-pad, high+pad
    # Only change axes when at least one observation will actually be off-scale.
    if not np.any(finite & ((values < low) | (values > high))):
        return None
    return low, high


def flag_offscale(ax, times, values, groups=None):
    """Mark every off-scale observation at its time and the corresponding edge."""
    times, values = np.asarray(times, float), np.asarray(values, float)
    bounds = time_series_limits(values, groups)
    side = np.zeros(len(values), int)
    if bounds is None:
        return side, None
    low, high = bounds
    valid = np.isfinite(times) & np.isfinite(values)
    side[valid & (values < low)] = -1
    side[valid & (values > high)] = 1
    if not side.any():
        return side, None
    ax.set_ylim(low, high)
    count_low, count_high = int((side == -1).sum()), int((side == 1).sum())
    ax.scatter(times[side != 0], np.where(side[side != 0] < 0, low, high),
               marker="x", color="black", s=32, linewidths=1.1, zorder=8, clip_on=False,
               label=f"Off-scale: {count_high:,} above, {count_low:,} below (black X)")
    ax.legend(loc="best", fontsize=8)
    return side, bounds
