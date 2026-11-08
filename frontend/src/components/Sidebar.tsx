import { useMemo, useState } from "react";
import {
  Eye,
  LogOut,
  MoreHorizontal,
  PanelLeftClose,
  Pencil,
  Plus,
  Search,
  Settings,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../api";
import { useApp } from "../appContext";
import { groupThreads } from "../lib/format";
import { navigate, type Route } from "../lib/route";
import { useSession } from "../session";
import { Logo, Menu, MenuItem, useToast } from "./ui";

interface Props {
  route: Route;
  onClose: () => void;
  onCollapse: () => void;
}

export default function Sidebar({ route, onClose, onCollapse }: Props) {
  const { threads, refreshThreads, openSettings } = useApp();
  const { user, allowance, signOut } = useSession();
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [title, setTitle] = useState("");

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    return groupThreads(q ? threads.filter((t) => t.title.toLowerCase().includes(q)) : threads);
  }, [threads, query]);

  const go = (path: string) => {
    navigate(path);
    onClose();
  };

  async function rename(id: string) {
    if (!title.trim()) return setRenaming(null);
    try {
      await api(`/api/threads/${id}`, { method: "PATCH", body: { title } });
      await refreshThreads();
    } catch (e) {
      toast({ kind: "error", text: (e as Error).message });
    }
    setRenaming(null);
  }

  async function remove(id: string) {
    try {
      await api(`/api/threads/${id}`, { method: "DELETE" });
      if (route.name === "thread" && route.id === id) navigate("/");
      await refreshThreads();
      toast({ kind: "success", text: "Thread deleted" });
    } catch (e) {
      toast({ kind: "error", text: (e as Error).message });
    }
  }

  const initials = (user?.name || user?.email || "?").slice(0, 2).toUpperCase();
  const quota = allowance
    ? allowance.unlimited
      ? allowance.own_keys ? "Your own key" : "Admin"
      : `${allowance.quick_left} Quick · ${allowance.deep_left} Deep left`
    : "";

  return (
    <aside className="flex h-full w-[272px] flex-col border-r border-line bg-side">
      <div className="flex items-center justify-between px-3.5 pb-2 pt-3.5">
        <button className="flex items-center gap-2 rounded-lg px-1 py-0.5" onClick={() => go("/")}>
          <Logo size={26} />
          <span className="text-[15px] font-semibold tracking-tight">NodexM</span>
        </button>
        <button className="btn-ghost hidden p-1.5 lg:inline-flex" onClick={onCollapse} title="Collapse sidebar" aria-label="Collapse sidebar">
          <PanelLeftClose className="h-4 w-4" />
        </button>
        <button className="btn-ghost p-1.5 lg:hidden" onClick={onClose} aria-label="Close menu">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="px-3 pt-1">
        <button onClick={() => go("/")} className="flex w-full items-center justify-between rounded-xl border border-line bg-card px-3 py-2.5 text-sm font-medium hover:border-accent/40 hover:bg-raised">
          <span className="flex items-center gap-2"><Plus className="h-4 w-4 text-accent" /> New thread</span>
          <kbd className="rounded border border-line px-1.5 font-mono text-[10px] text-faint">Ctrl K</kbd>
        </button>
      </div>

      <nav className="mt-3 space-y-0.5 px-3">
        <NavItem icon={Search} label="Ask" active={route.name === "home"} onClick={() => go("/")} />
        <NavItem icon={Eye} label="Watches" active={route.name === "watches" || route.name === "watch"} onClick={() => go("/watches")} />
      </nav>

      <div className="mt-4 px-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search threads" aria-label="Search threads" className="w-full rounded-lg bg-transparent py-1.5 pl-8 pr-2 text-sm placeholder:text-faint hover:bg-card focus:bg-card focus:outline-none" />
        </div>
      </div>

      <div className="mt-1 min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {groups.length === 0 && (
          <p className="px-3 py-6 text-center text-xs leading-relaxed text-faint">
            {query ? "No threads match." : "Your research threads will appear here."}
          </p>
        )}
        {groups.map((g) => (
          <div key={g.label} className="mt-3">
            <p className="px-2.5 pb-1 text-[11px] font-medium text-faint">{g.label}</p>
            {g.items.map((t) => {
              const active = route.name === "thread" && route.id === t.id;
              const running = t.last_status === "running" || t.last_status === "queued";
              return (
                <div key={t.id} className={`group relative flex items-center rounded-lg ${active ? "bg-raised" : "hover:bg-card"}`}>
                  {renaming === t.id ? (
                    <input
                      autoFocus
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      onBlur={() => rename(t.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") rename(t.id);
                        if (e.key === "Escape") setRenaming(null);
                      }}
                      className="m-0.5 w-full rounded-md border border-accent/50 bg-bg px-2 py-1.5 text-sm focus:outline-none"
                      aria-label="Thread title"
                    />
                  ) : (
                    <button onClick={() => go(`/t/${t.id}`)} className={`flex min-w-0 flex-1 items-center gap-2 px-2.5 py-2 text-left text-[13.5px] ${active ? "text-fg" : "text-muted group-hover:text-fg"}`}>
                      {running && <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent" />}
                      {t.watch_id && <Eye className="h-3.5 w-3.5 shrink-0 text-faint" />}
                      <span className="truncate">{t.title}</span>
                    </button>
                  )}
                  {renaming !== t.id && (
                    <div className={`absolute right-1 ${active ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-within:opacity-100"}`}>
                      <Menu
                        trigger={(toggle) => (
                          <button onClick={toggle} className="rounded-md p-1 text-faint hover:bg-bg hover:text-fg" aria-label="Thread options">
                            <MoreHorizontal className="h-4 w-4" />
                          </button>
                        )}
                      >
                        {(close) => (
                          <>
                            <MenuItem icon={Pencil} onClick={() => { close(); setTitle(t.title); setRenaming(t.id); }}>Rename</MenuItem>
                            <MenuItem icon={Trash2} danger onClick={() => { close(); remove(t.id); }}>Delete</MenuItem>
                          </>
                        )}
                      </Menu>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <div className="border-t border-line p-2.5">
        {allowance && !allowance.unlimited && (
          <button onClick={() => openSettings("keys")} className="mb-2 w-full rounded-lg border border-line bg-card px-3 py-2 text-left hover:border-accent/40">
            <span className="block text-[11px] font-medium uppercase tracking-wider text-faint">Free runs</span>
            <span className="text-sm">{quota}</span>
          </button>
        )}
        <Menu
          align="left"
          up
          trigger={(toggle) => (
            <button onClick={toggle} className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-card">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">{initials}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{user?.name}</span>
                <span className="block truncate text-xs text-faint">{allowance?.unlimited ? quota : user?.email}</span>
              </span>
              <Settings className="h-4 w-4 text-faint" />
            </button>
          )}
        >
          {(close) => (
            <>
              <MenuItem icon={Settings} onClick={() => { close(); openSettings("profile"); }}>Settings</MenuItem>
              <MenuItem icon={LogOut} onClick={() => { close(); signOut(); }}>Sign out</MenuItem>
            </>
          )}
        </Menu>
      </div>
    </aside>
  );
}

function NavItem({ icon: Icon, label, active, onClick }: { icon: typeof Search; label: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm ${active ? "bg-raised text-fg" : "text-muted hover:bg-card hover:text-fg"}`}>
      <Icon className="h-4 w-4" />
      {label}
    </button>
  );
}
