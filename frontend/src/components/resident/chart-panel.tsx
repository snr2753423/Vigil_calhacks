"use client";

import type { EChartsOption } from "echarts";
import ReactEChartsCore from "echarts-for-react/esm/core";
import type { ReactNode } from "react";

import { echarts } from "@/lib/echarts";

type Props = {
  title: string;
  hint?: string;
  legend?: ReactNode;
  option: EChartsOption | null;
  height: number;
  group: string;
  empty?: string;
};

/** One titled chart in the synced stack; `option = null` renders the "not equipped" placeholder. */
export function ChartPanel({ title, hint, legend, option, height, group, empty = "— not equipped" }: Props) {
  return (
    <figure className="space-y-1">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-3 px-1">
        <span className="text-base font-bold">
          {title} {hint && <span className="text-sm font-normal text-muted-foreground">{hint}</span>}
        </span>
        {legend}
      </figcaption>
      {option ? (
        <ReactEChartsCore
          echarts={echarts}
          option={option}
          notMerge
          lazyUpdate
          style={{ height, width: "100%" }}
          opts={{ renderer: "canvas" }}
          onChartReady={(chart) => {
            chart.group = group;
            echarts.connect(group);
          }}
        />
      ) : (
        <div
          className="flex items-center rounded-lg bg-surface-2 px-4 text-base text-muted-foreground"
          style={{ height: Math.min(height, 64) }}
        >
          {empty}
        </div>
      )}
    </figure>
  );
}

export function LegendSwatch({ color, label, band = false }: { color: string; label: string; band?: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
      <span
        aria-hidden
        className={band ? "inline-block h-3 w-5 rounded-sm" : "inline-block h-0.5 w-5 rounded"}
        style={{ background: color }}
      />
      {label}
    </span>
  );
}
