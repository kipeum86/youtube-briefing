/**
 * Astro content collection definition for briefings.
 *
 * Schema kept in sync with pipeline/models.py via scripts/export-schema.py.
 * The Zod schema below is a hand-translation of the Pydantic Briefing model —
 * they MUST match. When adding a field to Briefing, update both sides + run
 * scripts/export-schema.py to regenerate briefing.schema.json.
 *
 * The `src/content/briefings/` directory is a symlink to `../../data/briefings/`
 * so the data lives canonically in `data/` (where it's version-controlled) while
 * Astro treats it as a native content collection.
 */
import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

const briefingStatus = z.enum(["ok", "failed"]);

const failureReason = z.enum([
  "session_expired",
  "video_removed",
  "members_only",
  "age_restricted",
  "empty_transcript",
  "transcripts_disabled",
  "summarizer_refused",
  "wrong_language",
]);

const discoverySource = z.enum(["rss", "ytdlp_catchup"]);

const briefingSchema = z.object({
  video_id: z.string().min(5).max(20),
  channel_slug: z.string(),
  channel_name: z.string(),
  title: z.string(),
  published_at: z.coerce.date(),
  video_url: z.string().url(),
  thumbnail_url: z.string().url(),
  duration_seconds: z.number().int(),
  discovery_source: discoverySource,
  status: briefingStatus,
  summary: z.string().nullable(),
  failure_reason: failureReason.nullable(),
  generated_at: z.coerce.date(),
  provider: z.string(),
  model: z.string(),
  prompt_version: z.string(),
});

// Cross-field invariants (mirrors the Pydantic model validators)
const briefing = defineCollection({
  loader: glob({
    pattern: "*.json",
    base: "./src/content/briefings",
  }),
  schema: briefingSchema.superRefine((data, ctx) => {
    if (data.status === "ok") {
      if (!data.summary || data.summary.length < 50) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "status=ok requires a non-empty summary (>=50 chars)",
          path: ["summary"],
        });
      }
      if (data.failure_reason !== null) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "status=ok must have failure_reason=null",
          path: ["failure_reason"],
        });
      }
    } else if (data.status === "failed") {
      if (data.failure_reason === null) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "status=failed requires failure_reason to be set",
          path: ["failure_reason"],
        });
      }
      if (data.summary !== null) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "status=failed must have summary=null",
          path: ["summary"],
        });
      }
    }
  }),
});

export const collections = { briefing };
