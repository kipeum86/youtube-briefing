/**
 * /manifest.json — published for briefing-hub aggregator.
 *
 * Schema follows DESIGN.md §4 of the briefing-hub repo:
 *   { name, category, accent, description, url, updated_at, latest, items[] }
 *
 * items[].url points to the deep-linked briefing card on this site
 * (`#v-<video_id>`), which auto-expands on load. The original YouTube video
 * remains accessible from the "원본 영상" button inside the card.
 */
import type { APIRoute } from "astro";
import type { CollectionEntry } from "astro:content";
import { getCollection } from "astro:content";

type BriefingEntry = CollectionEntry<"briefing">;

const SITE_URL = "https://kipeum86.github.io/youtube-briefing/";
const MAX_ITEMS = 10;

export const GET: APIRoute = async () => {
  const all = await getCollection(
    "briefing",
    (entry: BriefingEntry) => entry.data.status === "ok",
  );

  // Newest first
  all.sort(
    (a: BriefingEntry, b: BriefingEntry) =>
      b.data.published_at.getTime() - a.data.published_at.getTime(),
  );

  const items = all.slice(0, MAX_ITEMS).map((entry: BriefingEntry) => ({
    title: entry.data.title,
    source: entry.data.channel_name,
    url: `${SITE_URL}#v-${entry.data.video_id}`,
    published_at: entry.data.published_at.toISOString(),
  }));

  const latest = items[0];

  const manifest = {
    name: "Youtube Briefing",
    category: "Youtube",
    accent: "#2d4a3e",
    description: "한국 유튜브 채널 + 메르 블로그 · 주 3회 심층 요약",
    url: SITE_URL,
    updated_at: latest?.published_at ?? new Date().toISOString(),
    latest,
    items,
  };

  return new Response(JSON.stringify(manifest, null, 2), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=300",
    },
  });
};
