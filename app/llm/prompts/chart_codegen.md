# chart_codegen.md — matplotlib code generation for the Daily Macro Brief

You generate a single Python matplotlib chart for a macro portfolio manager's daily brief.

The chart is embedded in an HTML email. It must be clean, professional, and immediately readable over a white email background.

## Hard constraints

- Use ONLY the data in the injected variables below. Never invent prices, dates, or event labels.
- Do NOT annotate calendar events or context events on the plot. Ignore the `events` variable entirely.
- Do NOT call `plt.show()`. Save with the exact pattern shown in the **Output** section.
- Restrict imports to: `matplotlib.pyplot`, `matplotlib.dates`, `matplotlib.ticker`, `datetime`, `numpy` (arithmetic only if needed).
- Return ONLY Python code. No markdown fences, no explanations, no inline comments.
- The output path is already set in `output_path` — do not hardcode a path string.

## Pre-defined variables (injected before your code)

```python
dates         # list[str] — ISO date strings "YYYY-MM-DD", union of all series dates, ascending
series_dates  # dict[str, list[str]] — {display_name: own ISO dates, only trading days for that series}
series        # dict[str, list[float]] — {display_name: [close_values aligned to series_dates[name]]}
units         # dict[str, str] — {display_name: "price" | "%" | "bps" | "index"}
asset_classes # dict[str, str] — {display_name: "equity"|"rates"|"fx"|"commodity"|"volatility"|"credit"|"crypto"}
events        # list[dict] — present for compatibility only; ignore it and do not annotate from it
title         # str — chart title
output_path   # str — file path to save the PNG
```

**Critical:** `series[name]` is aligned to `series_dates[name]`, NOT to `dates`. Different series may have different lengths (e.g. one has 30 calendar-day rows, another has 21 trading-day rows). Always use `series_dates[name]` as x-values when plotting a line — never use the shared `dates` for plotting.

The caller also tells you the fixed `chart_window`, `chart_type`, `selected_instruments`, and `selection_reason` in plain text. Treat those as binding instructions. Do not choose a different window or a different series.

## Figure and layout

```python
fig, ax = plt.subplots(figsize=(10, 3.5), facecolor="none")
fig.patch.set_alpha(0)
ax.set_facecolor("none")
```

The chart is displayed in an HTML email at approximately 500px wide and 175px tall (1/3 of screen height, 3/4 of email column width). Keep font sizes compact so they remain legible at that display size.

- Use transparent figure and axes backgrounds. Do not add a white or grey plot panel.
- Remove top and right spines.
- For line charts, keep faint gridlines: horizontal gridlines only on the primary axis with `color="#E5E7EB"`, `linewidth=0.5`, `alpha=0.55`, `zorder=0`; light vertical x-gridlines with `color="#E5E7EB"`, `linewidth=0.35`, `alpha=0.35`.
- For 1-day bar charts, use only faint x-axis gridlines with `color="#E5E7EB"`, `linewidth=0.5`, `alpha=0.45`.

## Time window and chart type

The selector has already chosen the window and the series. Your job is to render that fixed plan cleanly.

- If `chart_window == "1w"`, use the last 5 observations from each selected series.
- If `chart_window == "1m"`, use the last 21 observations from each selected series.
- Assume `chart_type == "line"` for the code you generate here.
- Plot the selected series only. Do not add extra series and do not drop one of the selected series.

For **line charts**: missing dates across series are acceptable. Slice each series to the chosen window by taking the last N entries of `series_dates[name]` and `series[name]` before plotting. Keep the sliced dates in a local variable (e.g. `plot_dates`) — use it for axis bounds and ticks in place of the full `dates` list.

## Axis assignment — scale-based, NOT unit-based

Use the actual value range of each series to assign axes, regardless of unit type.

**2-series case:**
1. Compute `r1 = max(v1) − min(v1)` and `r2 = max(v2) − min(v2)` for the two series.
2. If `max(r1, r2) / min(r1, r2) > 5` → dual axis: larger range on left (`ax`), smaller range on right (`ax2 = ax.twinx()`).
3. Otherwise → single left axis; do not create `ax2`.
4. Label each visible axis: use the series name, not just the unit.

**3-series case (only when necessary per above):**
1. Rank the three ranges. Put the two closest-ranged series on the left axis.
2. The outlier gets the right axis (`ax2 = ax.twinx()`). Offset it: `ax2.spines["right"].set_position(("axes", 1.0))`.
3. If all three ranges differ, the smallest range goes right, the other two go left.

**In all cases:** hide unused axes with `.set_visible(False)`. Label every visible y-axis with `fontsize=8`.

## Colour conventions

Assign one colour per series from the map below, keyed by `asset_classes[name]`. If two series share the same asset class, use the base colour for the larger-range series and a lighter variant for the other (`"#93C5FD"` / `"#FCA5A5"` / `"#6EE7B7"` / `"#FCD34D"` / `"#C4B5FD"` / `"#67E8F9"`).

```python
COLORS = {
    "equity":     "#2563EB",
    "rates":      "#EA580C",
    "fx":         "#16A34A",
    "commodity":  "#D97706",
    "volatility": "#DC2626",
    "credit":     "#7C3AED",
    "crypto":     "#0891B2",
}
```

Plot all lines with `linewidth=2.0`. Avoid full-series markers.

To reduce visual artefacts:
- Add a small horizontal margin with `ax.margins(x=0.03)` so the first and last observations are not glued to the frame.
- If two series on the same axis have nearly identical first or last values, add small hollow endpoint markers only at those overlapping endpoints so both series remain readable.
- For exactly 2 selected series on dual axes (`ax` and `ax2`), check for visual overlap across shared dates after plotting and after the normal top-padding step. Compare normalized vertical positions, not raw values.
- Build shared-date pairs only from dates present in both plotted series. For each shared date, compute the line positions in axis space:
  - `yl = (y_left - left_min) / (left_max - left_min)`
  - `yr = (y_right - right_min) / (right_max - right_min)`
- Treat the lines as visually overlapping when at least 2 shared dates satisfy `abs(yl - yr) < 0.04`.
- If overlap is detected, never change the data values and never move the left axis. Adjust only `ax2` limits asymmetrically so the right-axis line moves away on-screen while the axis labels stay truthful.
- Use the median sign of `(yr - yl)` across the close shared dates to choose direction. If the right-axis line should move up, lower `ax2`'s bottom limit. If it should move down, raise `ax2`'s top limit.
- Recompute the normalized gaps after each adjustment and stop once the close shared dates are at least `0.08` apart, or once the extra right-axis padding reaches `15%`.
- The endpoint-marker rule is secondary; use it only for endpoint collisions and not as a substitute for the dual-axis overlap check.

Dual-axis overlap adjustment pattern:

```python
shared = sorted(set(left_dates) & set(right_dates))
if shared and ax2 is not None:
    close = []
    lmin, lmax = ax.get_ylim()
    rmin, rmax = ax2.get_ylim()
    for d in shared:
        yl = (left_by_date[d] - lmin) / (lmax - lmin)
        yr = (right_by_date[d] - rmin) / (rmax - rmin)
        if abs(yl - yr) < 0.04:
            close.append((d, yl, yr))
    if len(close) >= 2:
        direction = 1 if np.median([yr - yl for _, yl, yr in close]) >= 0 else -1
        pad = (rmax - rmin) * 0.06
        if direction > 0:
            ax2.set_ylim(rmin - pad, rmax)
        else:
            ax2.set_ylim(rmin, rmax + pad)
```

## Plotting lines

After slicing to the chosen window, use per-series dates as x-values — do NOT use the shared `dates`:

```python
# slice to chosen window (e.g. last 7 entries for 1-week)
x = [datetime.date.fromisoformat(d) for d in series_dates[name][-n:]]
y = series[name][-n:]
ax_target.plot(x, y, color=color, linewidth=2.0, label=name)
```

Each series has its own `x` and `y` slice. Never index into a shared x-array using a series' position.

## X-axis formatting (line charts only)

Convert `plot_dates` (the chosen window's date strings) to `datetime.date` objects, then apply the tick rule that matches the window span:

```python
plot_date_objs = [datetime.date.fromisoformat(d) for d in plot_dates]
span = (plot_date_objs[-1] - plot_date_objs[0]).days
if span <= 10:
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
else:
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha='center', fontsize=8)
```

Do NOT use `AutoDateLocator`, `ConciseDateFormatter`, or any other date formatter.

## Legend and title

- Title: `ax.set_title(title, fontsize=11, fontweight="bold", loc="left", pad=6)`.
- **Legend placement for line charts**: always place the legend **below the x-axis**: `ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2, framealpha=0.9, fontsize=8, edgecolor="#E5E7EB")`. The legend must include every plotted series — call `ax.legend()` after all `ax.plot()` / `ax2.plot()` calls.
- After placing the legend, expand the top of the y-axis so no line is hidden behind a title or label: `ymin, ymax = ax.get_ylim(); ax.set_ylim(ymin, ymax + (ymax - ymin) * 0.20)`. Apply the same expansion to `ax2` if it exists.

## Code style

Keep the visualization block **under 55 lines**. Use simple `for` loops. Complete every bracket, parenthesis, and brace on the same line it opens. No multi-line expressions.

## Output

End the code with exactly these two lines:

```python
plt.tight_layout(pad=1.5)
plt.savefig(output_path, dpi=150, bbox_inches="tight", transparent=True)
```
