// shared/trials.ts
import { z } from "zod";
import { SegmentCodeSchema, segmentByCode } from "./segments.js";
import { ExitReasonSchema } from "./objections.js";

export const TrialStatus = z.enum([
  "draft",
  "paused-awaiting-review",
  "active",
  "completed",
  "exited",
]);

export const TrialSchema = z.object({
  id: z.string().uuid(),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
  segment: SegmentCodeSchema,
  status: TrialStatus,
  evidencePackConfirmed: z.boolean(),
  exitReason: ExitReasonSchema.optional(),
}).superRefine((data, ctx) => {
  const seg = segmentByCode(data.segment);
  if (seg.requiresEvidencePack && !data.evidencePackConfirmed && data.status === "active") {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: "Evidence pack must be confirmed for this segment before activation.",
      path: ["evidencePackConfirmed"],
    });
  }
});

export type Trial = z.infer<typeof TrialSchema>;
