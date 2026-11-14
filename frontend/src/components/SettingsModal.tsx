import { useEffect, useState } from "react";
import { ExternalLink, KeyRound, LogOut, SlidersHorizontal, UserRound, Wallet } from "lucide-react";
import { api } from "../api";
import type { SettingsTab } from "../appContext";
import { money } from "../lib/format";
import { useSession } from "../session";
import type { Allowance, Preferences, User } from "../types";
import { Modal, Segmented, Spinner, useToast } from "./ui";

interface Props {
  open: boolean;
  tab: SettingsTab;
  onTab: (t: SettingsTab) => void;
  onClose: () => void;
}

const TABS: { key: SettingsTab; label: string; icon: typeof UserRound }[] = [
  { key: "profile", label: "Profile", icon: UserRound },
  { key: "preferences", label: "Preferences", icon: SlidersHorizontal },
  { key: "keys", label: "API keys", icon: KeyRound },
  { key: "usage", label: "Usage", icon: Wallet },
];

type SettingsResponse = { user: User; allowance: Allowance };

export default function SettingsModal({ open, tab, onTab, onClose }: Props) {
  const { signOut } = useSession();
  return (
    <Modal open={open} onClose={onClose} title="Settings" wide>
      <div className="flex min-h-[420px] flex-col sm:flex-row">
        <nav className="flex shrink-0 gap-1 overflow-x-auto border-b border-line p-2 sm:w-48 sm:flex-col sm:border-b-0 sm:border-r">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => onTab(t.key)} className={`flex items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm ${tab === t.key ? "bg-raised text-fg" : "text-muted hover:bg-card hover:text-fg"}`}>
              <t.icon className="h-4 w-4" /> {t.label}
            </button>
          ))}
          <button onClick={signOut} className="flex items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm text-muted hover:bg-card hover:text-disputed sm:mt-auto">
            <LogOut className="h-4 w-4" /> Sign out
          </button>
        </nav>
        <div className="min-w-0 flex-1 p-5 sm:p-6">
          {tab === "profile" && <ProfileTab />}
          {tab === "preferences" && <PreferencesTab />}
          {tab === "keys" && <KeysTab />}
          {tab === "usage" && <UsageTab />}
        </div>
      </div>
    </Modal>
  );
}

function useSave() {
  const { apply } = useSession();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function save(path: string, body: unknown, message: string) {
    setBusy(true);
    try {
      apply(await api<SettingsResponse>(path, { method: "PUT", body }));
      toast({ kind: "success", text: message });
    } catch (e) {
      toast({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }
  return { save, busy };
}

function ProfileTab() {
  const { user } = useSession();
  const { save, busy } = useSave();
  const [name, setName] = useState(user?.name ?? "");
  return (
    <div className="max-w-md space-y-5">
      <h3 className="text-base font-semibold">Profile</h3>
      <div>
        <label className="label" htmlFor="s-name">Name</label>
        <input id="s-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div>
        <span className="label">Email</span>
        <p className="text-sm text-muted">{user?.email}</p>
      </div>
      <div>
        <span className="label">Sign-in methods</span>
        <p className="text-sm text-muted">
          {[user?.sign_in.google && "Google", user?.sign_in.password && "Email and password"].filter(Boolean).join(" · ") || "–"}
        </p>
      </div>
      <button className="btn-primary" disabled={busy} onClick={() => save("/api/settings", { name }, "Profile saved")}>{busy ? <Spinner /> : "Save"}</button>
    </div>
  );
}

function PreferencesTab() {
  const { user } = useSession();
  const { save, busy } = useSave();
  const [prefs, setPrefs] = useState<Preferences>(user!.preferences);
  const set = <K extends keyof Preferences>(k: K, v: Preferences[K]) => setPrefs((p) => ({ ...p, [k]: v }));
  return (
    <div className="max-w-md space-y-6">
      <h3 className="text-base font-semibold">Research preferences</h3>
      <div>
        <span className="label">Default mode</span>
        <Segmented value={prefs.default_mode} onChange={(v) => set("default_mode", v)} options={[{ value: "auto", label: "Auto" }, { value: "quick", label: "Quick" }, { value: "deep", label: "Deep" }]} />
        <p className="mt-1.5 text-xs text-faint">Auto lets the router choose. Quick takes about a minute; Deep takes 3–6 minutes and reads up to 24 sources.</p>
      </div>
      <div>
        <label className="label" htmlFor="s-region">Region focus</label>
        <select id="s-region" className="input" value={prefs.region} onChange={(e) => set("region", e.target.value as Preferences["region"])}>
          {["Global", "EU", "US", "UK", "APAC"].map((r) => <option key={r}>{r}</option>)}
        </select>
        <p className="mt-1.5 text-xs text-faint">Decides which regulators and official sites the regulatory searcher uses.</p>
      </div>
      <div>
        <span className="label">Writing depth</span>
        <Segmented value={prefs.depth} onChange={(v) => set("depth", v)} options={[{ value: "summary", label: "Brief" }, { value: "analyst", label: "Analyst" }, { value: "technical", label: "Technical" }]} />
      </div>
      <button className="btn-primary" disabled={busy} onClick={() => save("/api/settings", { preferences: prefs }, "Preferences saved")}>{busy ? <Spinner /> : "Save"}</button>
    </div>
  );
}

function KeysTab() {
  const { user, allowance } = useSession();
  const { save, busy } = useSave();
  const [gemini, setGemini] = useState("");
  const [tavily, setTavily] = useState("");
  return (
    <div className="max-w-lg space-y-5">
      <div>
        <h3 className="text-base font-semibold">Use your own API keys</h3>
        <p className="mt-1 text-sm leading-relaxed text-muted">
          With your own Gemini key, runs are billed to your Google account and the free-run limit no longer applies.
          Keys are encrypted before they are stored and are never shown again in full.
        </p>
      </div>
      {allowance && !allowance.unlimited && (
        <p className="rounded-lg border border-line bg-card px-3 py-2 text-sm">
          Free runs left: <b>{allowance.quick_left}</b> Quick, <b>{allowance.deep_left}</b> Deep.
        </p>
      )}
      <KeyField label="Gemini API key" current={user?.keys.gemini ?? ""} value={gemini} onChange={setGemini} href="https://aistudio.google.com/apikey" hint="Required for your own-key mode. Free tier available in Google AI Studio." />
      <KeyField label="Tavily API key" current={user?.keys.tavily ?? ""} value={tavily} onChange={setTavily} href="https://app.tavily.com" hint="Optional. Adds general web search; without it, the free academic, regulatory, news and data APIs are used." />
      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" disabled={busy || (!gemini && !tavily)} onClick={async () => {
          const body: Record<string, string> = {};
          if (gemini) body.gemini_api_key = gemini;
          if (tavily) body.tavily_api_key = tavily;
          await save("/api/settings/keys", body, "Keys saved");
          setGemini("");
          setTavily("");
        }}>
          {busy ? <Spinner /> : "Save keys"}
        </button>
        {(user?.keys.gemini || user?.keys.tavily) && (
          <button className="btn-ghost text-disputed hover:text-disputed" disabled={busy} onClick={() => save("/api/settings/keys", { gemini_api_key: "", tavily_api_key: "" }, "Keys removed")}>
            Remove my keys
          </button>
        )}
      </div>
    </div>
  );
}

function KeyField({ label, current, value, onChange, href, hint }: { label: string; current: string; value: string; onChange: (v: string) => void; href: string; hint: string }) {
  const id = label.replace(/\s+/g, "-").toLowerCase();
  return (
    <div>
      <div className="flex items-center justify-between">
        <label className="label" htmlFor={id}>{label}</label>
        <a href={href} target="_blank" rel="noreferrer" className="mb-1.5 flex items-center gap-1 text-xs text-accent hover:underline">Get a key <ExternalLink className="h-3 w-3" /></a>
      </div>
      <input id={id} type="password" autoComplete="off" className="input font-mono" value={value} onChange={(e) => onChange(e.target.value)} placeholder={current ? `Saved: ${current}` : "Paste key"} />
      <p className="mt-1.5 text-xs text-faint">{hint}</p>
    </div>
  );
}

function UsageTab() {
  const [usage, setUsage] = useState<{ allowance: Allowance; runs_by_status: Record<string, number>; total_cost_usd: number } | null>(null);
  useEffect(() => {
    api<typeof usage>("/api/usage").then(setUsage).catch(() => {});
  }, []);
  if (!usage) return <div className="flex justify-center py-16 text-faint"><Spinner /></div>;
  const a = usage.allowance;
  return (
    <div className="space-y-6">
      <h3 className="text-base font-semibold">Usage</h3>
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4">
        {[
          ["Completed runs", usage.runs_by_status.completed ?? 0],
          ["Runs today", `${a.runs_today}/${a.daily_limit >= 10000 ? "∞" : a.daily_limit}`],
          ["Free left", a.unlimited ? "–" : `${a.quick_left}Q · ${a.deep_left}D`],
          ["Estimated cost", money(usage.total_cost_usd)],
        ].map(([k, v]) => (
          <div key={k as string} className="bg-card px-3.5 py-3">
            <dt className="text-[11px] uppercase tracking-wider text-faint">{k}</dt>
            <dd className="mt-1 font-mono text-lg tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="text-sm text-muted">
        {a.unlimited
          ? a.own_keys ? "You are using your own keys, so runs are billed to your accounts." : "Admin account: no limits."
          : "Free runs use the server's keys. Failed or stopped runs do not count."}
      </p>
      {a.reason && <p className="rounded-lg border border-single/30 bg-single/5 px-3 py-2 text-sm text-single">{a.reason}</p>}
    </div>
  );
}
