export interface MarkdownSection {
  heading: string;
  level: number;
  start: number;
  end: number;
  full: string;
}

const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;

/** Split markdown into sections at level-1/level-2 headings. Text before the first heading becomes an untitled section. */
export function splitSections(md: string): MarkdownSection[] {
  const lines = md.split("\n");
  const sections: MarkdownSection[] = [];
  let current: MarkdownSection | null = null;
  let offset = 0;
  let inFence = false;

  for (const line of lines) {
    if (/^\s*```/.test(line)) inFence = !inFence;
    const m = !inFence ? HEADING.exec(line) : null;
    if (m && m[1].length <= 2) {
      if (current) {
        current.end = offset;
        current.full = md.slice(current.start, current.end);
        sections.push(current);
      } else if (offset > 0) {
        sections.push({ heading: "", level: 0, start: 0, end: offset, full: md.slice(0, offset) });
      }
      current = { heading: m[2].trim(), level: m[1].length, start: offset, end: md.length, full: "" };
    }
    offset += line.length + 1;
  }
  if (current) {
    current.end = md.length;
    current.full = md.slice(current.start);
    sections.push(current);
  } else if (md.trim()) {
    sections.push({ heading: "", level: 0, start: 0, end: md.length, full: md });
  }
  return sections;
}

/** Replace one section (heading included) with new markdown. */
export function replaceSectionBody(md: string, section: MarkdownSection, replacement: string): string {
  const body = replacement.endsWith("\n") ? replacement : replacement + "\n";
  return md.slice(0, section.start) + body + (section.end < md.length ? "\n" : "") + md.slice(section.end).replace(/^\n+/, "");
}
