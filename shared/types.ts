// shared/types.ts
export type Brand<T, B> = T & { readonly __brand: B };

export type CaptureId   = Brand<string, "CaptureId">;
export type TrialId     = Brand<string, "TrialId">;
export type StoryId     = Brand<string, "StoryId">;
export type DebriefId   = Brand<string, "DebriefId">;

export const newId = <T extends string>(): Brand<string, T> =>
  crypto.randomUUID() as Brand<string, T>;

export type IsoDate = Brand<string, "IsoDate">;
export const now = (): IsoDate => new Date().toISOString() as IsoDate;
