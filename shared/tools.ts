// shared/tools.ts
export interface ToolEntry {
  num: number;
  slug: string;
  title: string;
  manualPageId: `p${string}`;
  category: "prepare" | "qualify" | "activate" | "follow-up" | "report";
  level: "L0" | "L1" | "L2";
}

export const TOOLS: readonly ToolEntry[] = [
  { num: 1,  slug: "sale-to-be-made",  title: "Sale to Be Made",           manualPageId: "p03", category: "prepare",  level: "L0" },
  { num: 2,  slug: "call-router",       title: "Call Router",               manualPageId: "p05", category: "prepare",  level: "L0" },
  { num: 3,  slug: "walk-and-talk",     title: "Walk & Talk",               manualPageId: "p06", category: "activate", level: "L0" },
  { num: 4,  slug: "network-discipline",title: "Network Discipline",        manualPageId: "p07", category: "prepare",  level: "L1" },
  { num: 5,  slug: "bullseye",          title: "Bullseye & First 5",        manualPageId: "p08", category: "prepare",  level: "L0" },
  { num: 6,  slug: "competitive-map",   title: "Competitive Map",           manualPageId: "p09", category: "prepare",  level: "L1" },
  { num: 7,  slug: "personas",          title: "Personas",                  manualPageId: "p10", category: "qualify",  level: "L1" },
  { num: 8,  slug: "prospecting",       title: "Prospecting",               manualPageId: "p11", category: "prepare",  level: "L0" },
  { num: 9,  slug: "written-outreach",  title: "Written Outreach",          manualPageId: "p12", category: "prepare",  level: "L1" },
  { num: 10, slug: "story-vault",       title: "Story Vault",               manualPageId: "p16", category: "activate", level: "L0" },
  { num: 11, slug: "ask-and-listen",    title: "Ask & Listen",              manualPageId: "p18", category: "qualify",  level: "L1" },
  { num: 12, slug: "objections",        title: "Skepticism & Objections",   manualPageId: "p19", category: "qualify",  level: "L2" },
  { num: 13, slug: "visual-impact",     title: "Visual Impact",             manualPageId: "p20", category: "activate", level: "L0" },
  { num: 14, slug: "progress-report",   title: "Progress Report",           manualPageId: "p21", category: "report",   level: "L1" },
  { num: 15, slug: "weekly-cadence",    title: "Weekly Cadence",            manualPageId: "p22", category: "follow-up",level: "L0" },
] as const;

export const toolBySlug = (slug: string) =>
  TOOLS.find((t) => t.slug === slug);

export const toolByPageId = (pageId: string) =>
  TOOLS.find((t) => t.manualPageId === pageId);
