// shared/captures.ts
import { z } from "zod";
import { ClaimHitSchema } from "./claims.js";

export const CaptureSchema = z.object({
  id: z.string().uuid(),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
  content: z.string(),
  claimHits: z.array(ClaimHitSchema),
});

export type Capture = z.infer<typeof CaptureSchema>;
