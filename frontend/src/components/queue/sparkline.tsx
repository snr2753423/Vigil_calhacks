type Props = { values: (number | null)[]; color: string; label: string; width?: number; height?: number };

/** 2 h HR sparkline (24 × 5-min averages) as a tiny inline SVG: cheap enough for every card. */
export function Sparkline({ values, color, label, width = 132, height = 40 }: Props) {
  const nums = values.filter((v): v is number => v !== null);
  if (nums.length < 2) {
    return (
      <span className="inline-flex items-center text-sm text-muted-foreground" style={{ width, height }}>
        — no HR data
      </span>
    );
  }
  const min = Math.min(...nums) - 2;
  const max = Math.max(...nums) + 2;
  const step = width / Math.max(values.length - 1, 1);
  const y = (v: number) => height - 3 - ((v - min) / (max - min)) * (height - 6);
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${(i * step).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  });
  const lastIdx = values.length - 1 - [...values].reverse().findIndex((v) => v !== null);
  const last = values[lastIdx] as number;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lastIdx * step} cy={y(last)} r={3} fill={color} />
    </svg>
  );
}
