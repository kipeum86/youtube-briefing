import type { CollectionEntry } from "astro:content";

type BriefingEntry = CollectionEntry<"briefing">;

export interface SourceOption {
  slug: string;
  name: string;
}

const SOURCE_ORDER = [
  "mer",
  "parkjonghoon",
  "shuka",
  "understanding",
  "jisik-inside",
  "globelab",
];

const DISPLAY_NAME_OVERRIDES: Record<string, string> = {
  mer: "메르(블로그)",
  parkjonghoon: "박종훈",
  shuka: "슈카월드",
  understanding: "언더스탠딩",
  "jisik-inside": "지식인사이드",
  globelab: "지구본연구소",
};

export function buildSourceOptions(briefings: BriefingEntry[]): SourceOption[] {
  const bySlug = new Map<string, SourceOption>();

  for (const briefing of briefings) {
    const slug = briefing.data.channel_slug;
    if (bySlug.has(slug)) continue;

    bySlug.set(slug, {
      slug,
      name: sourceDisplayName(briefing),
    });
  }

  return Array.from(bySlug.values()).sort(compareSources);
}

export function latestGeneratedAt(briefings: BriefingEntry[]): Date | null {
  if (briefings.length === 0) return null;
  return new Date(
    Math.max(...briefings.map((briefing) => briefing.data.generated_at.getTime())),
  );
}

export function formatKstDateTime(date: Date): string {
  const kstMs = date.getTime() + 9 * 60 * 60 * 1000;
  const k = new Date(kstMs);
  const y = k.getUTCFullYear();
  const m = String(k.getUTCMonth() + 1).padStart(2, "0");
  const d = String(k.getUTCDate()).padStart(2, "0");
  const hh = String(k.getUTCHours()).padStart(2, "0");
  const mm = String(k.getUTCMinutes()).padStart(2, "0");
  return `${y}.${m}.${d} ${hh}:${mm} KST`;
}

export function sourceMixLabel(briefings: BriefingEntry[]): string {
  const bySlug = new Map<string, string>();
  for (const briefing of briefings) {
    bySlug.set(
      briefing.data.channel_slug,
      briefing.data.source_type ?? "youtube",
    );
  }

  const counts = Array.from(bySlug.values()).reduce(
    (acc, sourceType) => {
      if (sourceType === "naver_blog") acc.blogs += 1;
      else acc.youtube += 1;
      return acc;
    },
    { youtube: 0, blogs: 0 },
  );

  if (counts.youtube > 0 && counts.blogs > 0) {
    return `${counts.youtube}개 한국 경제·시사 유튜브 채널 + ${counts.blogs}개 네이버 블로그`;
  }
  if (counts.youtube > 0) return `${counts.youtube}개 한국 경제·시사 유튜브 채널`;
  if (counts.blogs > 0) return `${counts.blogs}개 네이버 블로그`;
  return "한국 경제·시사 소스";
}

function sourceDisplayName(briefing: BriefingEntry): string {
  const slug = briefing.data.channel_slug;
  if (DISPLAY_NAME_OVERRIDES[slug]) return DISPLAY_NAME_OVERRIDES[slug];

  const name = briefing.data.channel_name.trim();
  if ((briefing.data.source_type ?? "youtube") === "naver_blog") {
    return `${name.replace(/\s*의?\s*블로그$/, "")}(블로그)`;
  }
  return name;
}

function compareSources(a: SourceOption, b: SourceOption): number {
  const aIndex = SOURCE_ORDER.indexOf(a.slug);
  const bIndex = SOURCE_ORDER.indexOf(b.slug);
  if (aIndex !== -1 || bIndex !== -1) {
    return (aIndex === -1 ? Number.MAX_SAFE_INTEGER : aIndex)
      - (bIndex === -1 ? Number.MAX_SAFE_INTEGER : bIndex);
  }
  return a.name.localeCompare(b.name, "ko");
}
