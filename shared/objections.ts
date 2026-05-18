// shared/objections.ts
import { z } from "zod";

export type ObjectionCode = `OBJ-${string}`;

export interface ObjectionRow {
  code: ObjectionCode;
  buyerSays: string;
  decode: string;
  safeRoute: string;
  bridge: string;
}

export const OBJECTIONS: readonly ObjectionRow[] = [
  {
    code: "OBJ-01",
    buyerSays: "We already have coffee.",
    decode: "Habit defense · functional claim · clean no",
    safeRoute: "When does coffee work well, and when does it stop being enough?",
    bridge: "That's the window I'd test, not argue about.",
  },
  {
    code: "OBJ-02",
    buyerSays: "The crew won't care.",
    decode: "Social risk · no champion · prior failure",
    safeRoute: "Has a coach or alpha tried it visibly?",
    bridge: "We protect the champion's credibility. Start with the alpha.",
  },
  {
    code: "OBJ-03",
    buyerSays: "Send me some info.",
    decode: "Stall · not interested · needs social proof",
    safeRoute: "What info would change a trial to yes?",
    bridge: "I'll bring the label, a sample, and a case study. 10 minutes.",
  },
  {
    code: "OBJ-04",
    buyerSays: "Is it OPSS-approved?",
    decode: "Military / procurement check · risk aversion",
    safeRoute: "What does approval mean for your process?",
    bridge: "I won't claim certification I can't verify. Here's the label and current status.",
  },
  {
    code: "OBJ-05",
    buyerSays: "It's too expensive.",
    decode: "Price vs. value · budget · comparison",
    safeRoute: "Cost per serving vs. what the crew buys now?",
    bridge: "One stick replaces the coffee + hydration + pre-workout stack.",
  },
  {
    code: "OBJ-06",
    buyerSays: "120mg isn't enough.",
    decode: "High tolerance · comparison to 300mg products",
    safeRoute: "What does the crew need at hour 6 — a spike or a window?",
    bridge: "Designed for a steadier use-window test, not a spike.",
  },
  {
    code: "OBJ-07",
    buyerSays: "We use Celsius / Monster.",
    decode: "Brand loyalty · habit · social norm",
    safeRoute: "What does the crew do at hour 3?",
    bridge: "Different format: one stick in water, with the label visible.",
  },
  {
    code: "OBJ-08",
    buyerSays: "Never heard of you.",
    decode: "Trust gap · credibility · risk aversion",
    safeRoute: "Founder story + evidence pack + retime if needed",
    bridge: "Fair. We were built for this market, not the grocery aisle.",
  },
] as const;

export const EXIT_REASONS = [
  {
    code: "NO-OWNER",
    trigger: "I'll ask around",
    reengageWhen: "They name a human.",
  },
  {
    code: "NO-LOOP",
    trigger: "Let me know how it goes",
    reengageWhen: "They commit to a check-in date.",
  },
  {
    code: "OVERSIZE",
    trigger: "50-person trial, zero prior use",
    reengageWhen: "Counter: 'Protect your credibility. Start with 5.'",
  },
  {
    code: "HIGH-RISK",
    trigger: "Champion untested, crew skeptical",
    reengageWhen: "Counter: 'Start with the alpha. Visible win first.'",
  },
] as const;

export type ExitReasonCode = (typeof EXIT_REASONS)[number]["code"];
export const ExitReasonSchema = z.enum(
  EXIT_REASONS.map((e) => e.code) as [ExitReasonCode, ...ExitReasonCode[]]
);

export interface RoleplayPrefill {
  scenarioCode: ObjectionCode;
  buyerLine: string;
  hint: string;
  manualPageId: "p19";
}

export const buildRoleplayPrefill = (code: ObjectionCode): RoleplayPrefill => {
  const row = OBJECTIONS.find((o) => o.code === code);
  if (!row) throw new Error(`Unknown objection code: ${code}`);
  return {
    scenarioCode: code,
    buyerLine: row.buyerSays,
    hint: `Safe route: ${row.safeRoute}`,
    manualPageId: "p19",
  };
};
