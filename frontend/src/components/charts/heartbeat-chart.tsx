"use client";

import ReactEChartsCore from "echarts-for-react/esm/core";
import { useMemo } from "react";

import { useTheme } from "@/hooks/use-theme";
import { PALETTES } from "@/lib/chart-options";
import { echarts } from "@/lib/echarts";

type Props = { intervalsMs: number[]; expectedMs: number };

/** Gaps between consecutive heartbeats as seen by this browser; the dashed line is the server interval. */
export function HeartbeatChart({ intervalsMs, expectedMs }: Props) {
  const { theme } = useTheme();
  const pal = PALETTES[theme];
  const option = useMemo(
    () => ({
      animation: false,
      grid: { left: 44, right: 12, top: 12, bottom: 24 },
      tooltip: { trigger: "axis", valueFormatter: (v: number) => `${Math.round(v)} ms` },
      xAxis: {
        type: "category",
        data: intervalsMs.map((_, i) => i - intervalsMs.length + 1),
        axisLabel: { color: pal.muted },
        axisLine: { lineStyle: { color: pal.axis } },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: Math.max(expectedMs * 1.5, ...intervalsMs),
        axisLabel: { color: pal.muted, formatter: (v: number) => `${v / 1000}s` },
        splitLine: { lineStyle: { color: pal.grid } },
      },
      series: [
        {
          type: "bar",
          data: intervalsMs,
          itemStyle: { color: pal.series[0], borderRadius: [3, 3, 0, 0] },
          markLine: {
            symbol: "none",
            silent: true,
            label: { show: false },
            lineStyle: { color: pal.faint, type: "dashed" },
            data: [{ yAxis: expectedMs }],
          },
        },
      ],
    }),
    [intervalsMs, expectedMs, pal],
  );

  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      notMerge
      style={{ height: 160, width: "100%" }}
      opts={{ renderer: "canvas" }}
    />
  );
}
