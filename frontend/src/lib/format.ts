import type { ThreadSummary } from "../types";

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function duration(start: string | null, end: string | null): string {
  if (!start) return "";
  const ms = (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function money(usd: number | undefined): string {
  if (!usd) return "$0.00";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export function pct(x: number | undefined | null): string {
  return x == null ? "–" : `${Math.round(x * 100)}%`;
}

export function compact(n: number | undefined): string {
  if (!n) return "0";
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

export function groupThreads(threads: ThreadSummary[]): { label: string; items: ThreadSummary[] }[] {
  const startOfDay = new Date();
  startOfDay.setHours(0, 0, 0, 0);
  const day = 86400000;
  const buckets: [string, (t: number) => boolean][] = [
    ["Today", (t) => t >= startOfDay.getTime()],
    ["Yesterday", (t) => t >= startOfDay.getTime() - day],
    ["Previous 7 days", (t) => t >= startOfDay.getTime() - 7 * day],
    ["Previous 30 days", (t) => t >= startOfDay.getTime() - 30 * day],
    ["Older", () => true],
  ];
  const groups = buckets.map(([label]) => ({ label, items: [] as ThreadSummary[] }));
  for (const th of threads) {
    const t = new Date(th.updated_at).getTime();
    const i = buckets.findIndex(([, test]) => test(t));
    groups[i].items.push(th);
  }
  return groups.filter((g) => g.items.length);
}

export const STATUS_LABEL: Record<string, string> = {
  agreed: "Agreed",
  disputed: "Disputed",
  single_source: "Single source",
  stale: "Stale",
};

export function statusChip(status: string): string {
  return status === "agreed" ? "chip-agreed" : status === "disputed" ? "chip-disputed" : status === "single_source" ? "chip-single" : "chip-neutral";
}
