"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DailyPoint, FunnelStage } from "@/lib/types";
import { formatDate, formatNumber } from "@/lib/utils";

const AXIS = { fontSize: 12, fill: "hsl(220 9% 44%)" };

export function FunnelChart({ data }: { data: FunnelStage[] }) {
  const top = data[0]?.count ?? 0;
  const rows = data.map((d, i) => ({
    ...d,
    label:
      i === 0 || top === 0
        ? formatNumber(d.count)
        : `${formatNumber(d.count)} · ${((d.count / top) * 100).toFixed(d.count / top < 0.01 ? 2 : 1)}%`,
  }));
  return (
    <ResponsiveContainer width="100%" height={Math.max(200, data.length * 48)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 120, bottom: 4, left: 8 }}>
        <XAxis type="number" hide domain={[0, "dataMax"]} />
        <YAxis type="category" dataKey="stage" width={110} tick={AXIS} axisLine={false} tickLine={false} />
        <Tooltip
          cursor={{ fill: "hsl(220 14% 96%)" }}
          formatter={(v: number) => [formatNumber(v), "Count"]}
          contentStyle={{ borderRadius: 8, fontSize: 12 }}
        />
        <Bar dataKey="count" fill="hsl(221 83% 53%)" radius={[0, 4, 4, 0]} minPointSize={2} isAnimationActive={false}>
          <LabelList dataKey="label" position="right" style={{ fontSize: 12, fill: "hsl(222 22% 11%)" }} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function DailyChart({ data }: { data: DailyPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
        <CartesianGrid stroke="hsl(220 13% 92%)" vertical={false} />
        <XAxis dataKey="date" tickFormatter={(d: string) => formatDate(d)} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
        <YAxis yAxisId="sent" tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
        <YAxis yAxisId="replies" orientation="right" tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
        <Tooltip
          labelFormatter={(d: string) => formatDate(d)}
          formatter={(v: number, name: string) => [formatNumber(v), name]}
          contentStyle={{ borderRadius: 8, fontSize: 12 }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line yAxisId="sent" type="monotone" dataKey="sent" name="Forms sent" stroke="hsl(221 83% 53%)" strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line yAxisId="replies" type="monotone" dataKey="replies" name="Replies" stroke="hsl(220 9% 55%)" strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line yAxisId="replies" type="monotone" dataKey="positive" name="Positive" stroke="hsl(152 69% 36%)" strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
