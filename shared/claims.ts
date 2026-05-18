// shared/claims.ts
import { z } from "zod";

export interface ClaimRule {
  code: `HC-${string}`;
  forbidden: string;
  forbiddenPatterns: RegExp[];
  say: string;
  severity: "block" | "warn";
}

export const HARD_CLAIMS: readonly ClaimRule[] = [
  {
    code: "HC-01",
    forbidden: "Proven to reduce errors",
    forbiddenPatterns: [/\bproven to\b/i, /\bclinically proven\b/i],
    say: "Formulated for energy + hydration; trial decides fit",
    severity: "block",
  },
  {
    code: "HC-02",
    forbidden: "Clinically tested",
    forbiddenPatterns: [/\bclinically tested\b/i, /\bclinical trial\b/i],
    say: "Ingredients with published research",
    severity: "block",
  },
  {
    code: "HC-03",
    forbidden: "Safe for everyone",
    forbiddenPatterns: [/\bsafe for everyone\b/i, /\b100% safe\b/i],
    say: "Review the label with your doctor",
    severity: "block",
  },
  {
    code: "HC-04",
    forbidden: "Better than [competitor]",
    forbiddenPatterns: [/\bbetter than\s+(celsius|monster|red bull|ghost)\b/i],
    say: "Different formula — here's the label",
    severity: "warn",
  },
  {
    code: "HC-05",
    forbidden: "Guaranteed energy",
    forbiddenPatterns: [/\bguarantee(d|s)?\s+energy\b/i, /\bguarantee(d|s)?\s+focus\b/i],
    say: "Designed for a steadier use-window test",
    severity: "block",
  },
] as const;

export interface ClaimHit {
  code: ClaimRule["code"];
  start: number;
  end: number;
  matched: string;
  severity: ClaimRule["severity"];
  suggestion: string;
}

export function scanForClaims(text: string): ClaimHit[] {
  const hits: ClaimHit[] = [];
  for (const rule of HARD_CLAIMS) {
    for (const pattern of rule.forbiddenPatterns) {
      const re = new RegExp(pattern.source, pattern.flags + (pattern.global ? "" : "g"));
      let m: RegExpExecArray | null;
      while ((m = re.exec(text)) !== null) {
        hits.push({
          code: rule.code,
          start: m.index,
          end: m.index + m[0].length,
          matched: m[0],
          severity: rule.severity,
          suggestion: rule.say,
        });
        if (m[0].length === 0) re.lastIndex++;
      }
    }
  }
  return hits.sort((a, b) => a.start - b.start);
}

export const ClaimHitSchema = z.object({
  code: z.string().regex(/^HC-\d+$/),
  start: z.number().int().nonnegative(),
  end: z.number().int().nonnegative(),
  matched: z.string(),
  severity: z.enum(["block", "warn"]),
  suggestion: z.string(),
});
