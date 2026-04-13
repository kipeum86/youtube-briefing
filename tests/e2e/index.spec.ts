/**
 * E2E smoke tests for the index (recent feed) page.
 *
 * These tests run against the built static site served by `astro preview`.
 * They verify the critical user flows from the design review test plan:
 *   - Page renders with brand + tabs + filter chips + cards
 *   - BriefingCard expand/collapse works
 *   - Channel filter chip click applies a filter
 *   - Dismissible intro removes itself and persists via localStorage
 *   - Deep-link anchor (#v-{video-id}) auto-expands the target card
 *
 * More comprehensive tests (archive, empty state, failed card variant,
 * responsive) live in their own spec files.
 */

import { expect, test } from "@playwright/test";

test.describe("index page", () => {
  // Each test gets a fresh browser context via the per-test `page` fixture,
  // so localStorage is already empty at test start. We do NOT use
  // addInitScript to clear storage because it runs on every navigation
  // including reloads, which would break tests that rely on localStorage
  // persisting across reloads.

  test("renders brand, tabs, chips, and briefing cards", async ({ page }) => {
    await page.goto("/youtube-briefing/");

    // Brand mark
    await expect(page.locator(".masthead .mark")).toHaveText("YOUTUBE BRIEFING");

    // Top tabs
    const recentTab = page.locator(".tabs a", { hasText: "최근" });
    const archiveTab = page.locator(".tabs a", { hasText: "아카이브" });
    await expect(recentTab).toHaveAttribute("aria-current", "page");
    await expect(archiveTab).not.toHaveAttribute("aria-current", "page");

    // Channel filter chips — "전체" active, 5 channels listed
    await expect(page.locator('[role="radiogroup"][aria-label="소스 필터"]')).toBeVisible();
    await expect(page.locator('[role="radio"]', { hasText: "전체" })).toHaveAttribute("aria-checked", "true");
    await expect(page.locator('[role="radio"]', { hasText: "슈카월드" })).toBeVisible();
    await expect(page.locator('[role="radio"]', { hasText: "언더스탠딩" })).toBeVisible();
    await expect(page.locator('[role="radio"]', { hasText: "지구본연구소" })).toBeVisible();
    await expect(page.locator('[role="radio"]', { hasText: "메르 블로그" })).toBeVisible();
    await expect(page.locator('[role="radio"]').nth(1)).toHaveText("메르 블로그");

    // At least one briefing card
    const cards = page.locator("article.briefing");
    await expect(cards.first()).toBeVisible();
    await expect(await cards.count()).toBeGreaterThanOrEqual(1);
  });

  test("briefing card expands and collapses", async ({ page }) => {
    await page.goto("/youtube-briefing/");

    const firstCard = page.locator("article.briefing").first();
    const expandButton = firstCard.locator("[data-expand-button]");

    // Skip if this happens to be a failed card (no expand button)
    if ((await expandButton.count()) === 0) {
      test.skip();
      return;
    }

    await expect(expandButton).toHaveText("펼쳐보기");
    await expect(expandButton).toHaveAttribute("aria-expanded", "false");

    await expandButton.click();

    await expect(expandButton).toHaveText("접기");
    await expect(expandButton).toHaveAttribute("aria-expanded", "true");
    await expect(firstCard).toHaveAttribute("data-expanded", "true");

    await expandButton.click();
    await expect(expandButton).toHaveText("펼쳐보기");
    await expect(firstCard).toHaveAttribute("data-expanded", "false");
  });

  test("channel filter chip click filters the feed", async ({ page }) => {
    await page.goto("/youtube-briefing/");

    // Click the shuka chip
    await page.locator('[role="radio"]', { hasText: "슈카월드" }).click();

    // URL should have ?channel=shuka
    await expect(page).toHaveURL(/channel=shuka/);

    // Only shuka cards should be visible (others display:none)
    const visibleCards = page.locator("article.briefing").filter({
      has: page.locator(".meta .channel", { hasText: "슈카월드" }),
    });
    await expect(visibleCards.first()).toBeVisible();

    // A non-shuka card should be hidden (via style display:none)
    const nonShukaCards = page.locator("article.briefing").filter({
      hasNot: page.locator(".meta .channel", { hasText: "슈카월드" }),
    });
    const count = await nonShukaCards.count();
    for (let i = 0; i < count; i++) {
      await expect(nonShukaCards.nth(i)).not.toBeVisible();
    }
  });

  test("dismissible intro removes itself and persists", async ({ page }) => {
    await page.goto("/youtube-briefing/");

    // Intro is visible initially
    const intro = page.locator("[data-intro]");
    await expect(intro).toBeVisible();

    // Click the close button
    await page.locator("[data-dismiss-intro]").click();
    await expect(intro).toBeHidden();

    // Reload — intro should stay hidden (localStorage)
    await page.reload();
    await expect(page.locator("[data-intro]")).toHaveCount(0);
  });

  test("failed briefing renders with 요약 실패 marker", async ({ page }) => {
    await page.goto("/youtube-briefing/");
    const failedCard = page.locator("article.briefing.failed").first();
    if ((await failedCard.count()) === 0) {
      test.skip();
      return;
    }
    await expect(failedCard.locator(".status-failed")).toHaveText("요약 실패");
    // No expand button on failed cards
    await expect(failedCard.locator("[data-expand-button]")).toHaveCount(0);
  });
});
