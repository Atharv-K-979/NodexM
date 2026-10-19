import { useState } from "react";

export interface TopicFormValues {
  topic: string;
  region: string;
  depth: string;
}

interface Props {
  onSubmit: (values: TopicFormValues) => void;
  loading?: boolean;
}

export default function TopicForm({ onSubmit, loading }: Props) {
  const [topic, setTopic] = useState(
    "EU AI Act Q3 2025 compliance costs: large vs SME burden estimates"
  );
  const [region, setRegion] = useState("Global");
  const [depth, setDepth] = useState("analyst");

  return (
    <form
      className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!topic.trim() || loading) return;
        onSubmit({ topic, region, depth });
      }}
    >
      <div>
        <label className="block text-sm font-semibold text-slate-700 mb-1.5">
          Research topic
        </label>
        <textarea
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          rows={3}
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
          placeholder="What do you want to research? Be specific about timeframes and comparisons."
          required
        />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-semibold text-slate-700 mb-1.5">
            Region / scope
          </label>
          <select
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
          >
            <option>Global</option>
            <option>EU</option>
            <option>US</option>
            <option>UK</option>
            <option>APAC</option>
          </select>
        </div>
        <div>
          <label className="block text-sm font-semibold text-slate-700 mb-1.5">
            Depth
          </label>
          <select
            value={depth}
            onChange={(e) => setDepth(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
          >
            <option value="summary">Executive summary</option>
            <option value="analyst">Analyst (recommended)</option>
            <option value="technical">Technical deep-dive</option>
          </select>
        </div>
      </div>
      <button
        type="submit"
        disabled={loading || !topic.trim()}
        className="w-full rounded-lg bg-brand-600 text-white py-2.5 text-sm font-semibold hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {loading ? "Running research…" : "Run research now"}
      </button>
    </form>
  );
}
