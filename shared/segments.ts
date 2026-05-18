// shared/segments.ts
import { z } from "zod";

export const SEGMENTS = [
  {
    code: "healthcare",
    label: "Healthcare",
    manualPageId: "p29",
    requiresEvidencePack: true,
    leadWith: "label-first",
  },
  {
    code: "military",
    label: "Military / Tactical",
    manualPageId: "p19",
    requiresEvidencePack: false,
    leadWith: "certification-first",
  },
  {
    code: "first-responder",
    label: "First Responder (Fire / EMS)",
    manualPageId: null,
    requiresEvidencePack: false,
    leadWith: "use-window",
  },
  {
    code: "endurance",
    label: "Endurance Athletes",
    manualPageId: null,
    requiresEvidencePack: false,
    leadWith: "use-window",
  },
  {
    code: "procurement",
    label: "Procurement / Institutional",
    manualPageId: null,
    requiresEvidencePack: false,
    leadWith: "label-first",
  },
] as const;

export type SegmentCode = (typeof SEGMENTS)[number]["code"];

export const SegmentCodeSchema = z.enum(
  SEGMENTS.map((s) => s.code) as [SegmentCode, ...SegmentCode[]]
);

export const segmentByCode = (code: SegmentCode) =>
  SEGMENTS.find((s) => s.code === code)!;
