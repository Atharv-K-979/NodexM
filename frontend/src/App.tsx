import { useCallback, useEffect, useMemo, useState } from "react";
import { Menu as MenuIcon, PanelLeftOpen, Plus } from "lucide-react";
import { api } from "./api";
import { AppContext, type SettingsTab } from "./appContext";
import AuthScreen from "./components/AuthScreen";
import HomeView from "./components/HomeView";
import SettingsModal from "./components/SettingsModal";
import Sidebar from "./components/Sidebar";
import ThreadView from "./components/thread/ThreadView";
import { Logo, Spinner, ToastProvider } from "./components/ui";
import { WatchesView, WatchView } from "./components/watches/WatchesView";
import { navigate, useRoute } from "./lib/route";
import { SessionProvider, useSession } from "./session";
import type { ThreadSummary } from "./types";

export default function App() {
  return (
    <ToastProvider>
      <SessionProvider>
        <Gate />
      </SessionProvider>
    </ToastProvider>
  );
}

function Gate() {
  const { user, loading } = useSession();
  if (loading)
    return (
      <div className="flex h-full items-center justify-center text-faint">
        <Spinner className="h-5 w-5" />
      </div>
    );
  return user ? <Shell /> : <AuthScreen />;
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem("nodexm.sidebar") === "collapsed";
  } catch {
    return false;
  }
}

function Shell() {
  const route = useRoute();
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [settings, setSettings] = useState<{ open: boolean; tab: SettingsTab }>({ open: false, tab: "profile" });

  const refreshThreads = useCallback(async () => {
    try {
      setThreads(await api<ThreadSummary[]>("/api/threads"));
    } catch {
      /* handled by 401 handler */
    }
  }, []);

  useEffect(() => {
    refreshThreads();
  }, [refreshThreads]);

  // Keep the sidebar's running indicators fresh while any thread is working.
  useEffect(() => {
    if (!threads.some((t) => t.last_status === "running" || t.last_status === "queued")) return;
    const t = setInterval(refreshThreads, 5000);
    return () => clearInterval(t);
  }, [threads, refreshThreads]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        navigate("/");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const toggleCollapsed = (value: boolean) => {
    setCollapsed(value);
    try {
      localStorage.setItem("nodexm.sidebar", value ? "collapsed" : "open");
    } catch {
      /* ignore */
    }
  };

  const ctx = useMemo(
    () => ({ threads, refreshThreads, openSettings: (tab: SettingsTab = "profile") => setSettings({ open: true, tab }) }),
    [threads, refreshThreads],
  );

  return (
    <AppContext.Provider value={ctx}>
      <div className="flex h-full overflow-hidden">
        {/* Desktop sidebar */}
        {!collapsed && (
          <div className="hidden lg:block">
            <Sidebar route={route} onClose={() => {}} onCollapse={() => toggleCollapsed(true)} />
          </div>
        )}
        {collapsed && (
          <div className="hidden w-14 flex-col items-center gap-2 border-r border-line bg-side py-3.5 lg:flex">
            <button onClick={() => navigate("/")} aria-label="Home"><Logo size={26} /></button>
            <button className="btn-ghost mt-2 p-2" onClick={() => toggleCollapsed(false)} title="Open sidebar" aria-label="Open sidebar"><PanelLeftOpen className="h-4 w-4" /></button>
            <button className="btn-ghost p-2" onClick={() => navigate("/")} title="New thread" aria-label="New thread"><Plus className="h-4 w-4" /></button>
          </div>
        )}

        {/* Mobile drawer */}
        {mobileOpen && (
          <div className="fixed inset-0 z-40 lg:hidden">
            <div className="absolute inset-0 bg-black/60" onClick={() => setMobileOpen(false)} />
            <div className="absolute inset-y-0 left-0 animate-rise">
              <Sidebar route={route} onClose={() => setMobileOpen(false)} onCollapse={() => {}} />
            </div>
          </div>
        )}

        <main className="relative min-w-0 flex-1 overflow-y-auto">
          <div className="sticky top-0 z-30 flex items-center justify-between border-b border-line/60 bg-bg/90 px-3 py-2 backdrop-blur lg:hidden">
            <button className="btn-ghost p-2" onClick={() => setMobileOpen(true)} aria-label="Open menu"><MenuIcon className="h-5 w-5" /></button>
            <button onClick={() => navigate("/")} className="flex items-center gap-2"><Logo size={22} /><span className="font-semibold">NodexM</span></button>
            <button className="btn-ghost p-2" onClick={() => navigate("/")} aria-label="New thread"><Plus className="h-5 w-5" /></button>
          </div>
          {route.name === "home" && <HomeView />}
          {route.name === "thread" && <ThreadView key={route.id} id={route.id} />}
          {route.name === "watches" && <WatchesView />}
          {route.name === "watch" && <WatchView key={route.id} id={route.id} />}
        </main>
      </div>
      <SettingsModal open={settings.open} tab={settings.tab} onTab={(tab) => setSettings({ open: true, tab })} onClose={() => setSettings((s) => ({ ...s, open: false }))} />
    </AppContext.Provider>
  );
}
