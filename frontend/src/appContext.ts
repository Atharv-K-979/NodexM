import { createContext, useContext } from "react";
import type { ThreadSummary } from "./types";

export type SettingsTab = "profile" | "preferences" | "keys" | "usage";

export interface AppValue {
  threads: ThreadSummary[];
  refreshThreads: () => Promise<void>;
  openSettings: (tab?: SettingsTab) => void;
}

export const AppContext = createContext<AppValue | null>(null);

export function useApp(): AppValue {
  const v = useContext(AppContext);
  if (!v) throw new Error("useApp outside AppContext");
  return v;
}
