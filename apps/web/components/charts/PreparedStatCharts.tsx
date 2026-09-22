
"use client";

export type BoxSummary = {
  group: string;
  n: number;
  q1: number;
  median: number;
  q3: number;
  whisker_low: number;
  whisker_high: number;
  outliers: number[];
};

export type HistogramBin = { left: number; right: number; count: number };

export function PreparedHistogram({ bins, xLabel, title, caption }: {
  bins: HistogramBin[];
  xLabel: string;
  title?: string;
  caption?: string;
}) {
  const width = 680, height = 340;
  const margin = { left: 58, right: 18, top: 28, bottom: 54 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  const xMin = Math.min(...bins.map((b) => b.left));
  const xMax = Math.max(...bins.map((b) => b.right));
  const yMax = Math.max(1, ...bins.map((b) => b.count));
  const sx = (v: number) => margin.left + ((v - xMin) / (xMax - xMin || 1)) * innerW;
  const sy = (v: number) => margin.top + innerH - (v / yMax) * innerH;

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}
      <svg className="chart-svg" viewBox={"0 0 " + width + " " + height}
           width="100%" role="img"
           aria-label={(title ?? "Histogram") + ". " + bins.length + " prepared bins."}>
        <line className="chart-axis" x1={margin.left} x2={margin.left + innerW}
              y1={margin.top + innerH} y2={margin.top + innerH} />
        <line className="chart-axis" x1={margin.left} x2={margin.left}
              y1={margin.top} y2={margin.top + innerH} />
        {bins.map((bin, index) => {
          const left = sx(bin.left), right = sx(bin.right), top = sy(bin.count);
          return (
            <rect key={index} className="chart-rect"
                  x={left} y={top} width={Math.max(1, right - left)}
                  height={margin.top + innerH - top}>
              <title>
                {bin.left.toPrecision(4) + " to " + bin.right.toPrecision(4)
                  + ": " + bin.count}
              </title>
            </rect>
          );
        })}
        <text className="chart-axis-label" x={margin.left + innerW / 2}
              y={height - 12} textAnchor="middle">{xLabel}</text>
        <text className="chart-axis-label"
              transform={"translate(16," + (margin.top + innerH / 2) + ") rotate(-90)"}
              textAnchor="middle">count</text>
      </svg>
      {caption && <figcaption className="chart-caption">{caption}</figcaption>}
    </figure>
  );
}

export function PreparedBoxPlot({ summaries, xLabel, yLabel, title, caption }: {
  summaries: BoxSummary[];
  xLabel: string;
  yLabel: string;
  title?: string;
  caption?: string;
}) {
  const width = 680, height = 360;
  const margin = { left: 70, right: 18, top: 28, bottom: 66 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  const all = summaries.flatMap((s) => [
    s.whisker_low, s.q1, s.median, s.q3, s.whisker_high, ...s.outliers,
  ]);
  const yMin = Math.min(...all), yMax = Math.max(...all);
  const sy = (v: number) =>
    margin.top + innerH - ((v - yMin) / (yMax - yMin || 1)) * innerH;
  const step = innerW / Math.max(1, summaries.length);
  const boxWidth = Math.min(48, step * 0.55);

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}
      <svg className="chart-svg" viewBox={"0 0 " + width + " " + height}
           width="100%" role="img"
           aria-label={(title ?? "Box plot") + ". " + summaries.length + " groups."}>
        <line className="chart-axis" x1={margin.left} x2={margin.left + innerW}
              y1={margin.top + innerH} y2={margin.top + innerH} />
        <line className="chart-axis" x1={margin.left} x2={margin.left}
              y1={margin.top} y2={margin.top + innerH} />
        {summaries.map((s, index) => {
          const cx = margin.left + step * (index + 0.5);
          return (
            <g key={s.group}>
              <line className="chart-ci" x1={cx} x2={cx}
                    y1={sy(s.whisker_low)} y2={sy(s.whisker_high)} />
              <line className="chart-cap" x1={cx - 8} x2={cx + 8}
                    y1={sy(s.whisker_low)} y2={sy(s.whisker_low)} />
              <line className="chart-cap" x1={cx - 8} x2={cx + 8}
                    y1={sy(s.whisker_high)} y2={sy(s.whisker_high)} />
              <rect className="chart-box"
                    x={cx - boxWidth / 2} y={sy(s.q3)} width={boxWidth}
                    height={Math.max(1, sy(s.q1) - sy(s.q3))} />
              <line className="chart-median" x1={cx - boxWidth / 2}
                    x2={cx + boxWidth / 2} y1={sy(s.median)} y2={sy(s.median)} />
              {s.outliers.map((value, outlier) => (
                <circle key={outlier} className="chart-point"
                        cx={cx} cy={sy(value)} r={2.5} />
              ))}
              <text className="chart-tick" x={cx} y={margin.top + innerH + 18}
                    textAnchor="middle">{s.group}</text>
            </g>
          );
        })}
        <text className="chart-axis-label" x={margin.left + innerW / 2}
              y={height - 12} textAnchor="middle">{xLabel}</text>
        <text className="chart-axis-label"
              transform={"translate(16," + (margin.top + innerH / 2) + ") rotate(-90)"}
              textAnchor="middle">{yLabel}</text>
      </svg>
      {caption && <figcaption className="chart-caption">{caption}</figcaption>}
    </figure>
  );
}
