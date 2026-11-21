const { test, expect } = require('@playwright/test');

const viewports = [
  { name: 'desktop-1440x1000', width: 1440, height: 1000 },
  { name: 'tablet-768x1024', width: 768, height: 1024 },
  { name: 'mobile-390x844', width: 390, height: 844 },
];

for (const viewport of viewports) {
  test(`local foundations remain usable: ${viewport.name}`, async ({ page }, testInfo) => {
    const browserErrors = [];
    const failedRequests = [];
    page.on('pageerror', (error) => browserErrors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') browserErrors.push(message.text());
    });
    page.on('requestfailed', (request) => failedRequests.push(request.url()));

    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto('/');
    await expect(page.locator('#section-home')).toHaveClass(/active/);
    await expect(page.locator('.mobile-bottom-nav, .mobile-bottom-item')).toHaveCount(0);
    await expect(page.locator('.voki-ecosystem-launcher')).toHaveCount(0);

    if (viewport.width <= 920) {
      await page.locator('#sidebar-toggle').click();
    }
    await page.locator('#sidebar-workspaces > summary').click();
    await page.locator('#sidebar [data-section="knowledge"]').click();
    await expect(page.locator('#section-knowledge')).toHaveClass(/active/);
    await expect(page.getByRole('heading', { name: 'Knowledge Hub' })).toBeVisible();
    await expect(page.locator('#knowledge-empty')).toBeVisible();

    await page.locator('#knowledge-title-input').fill('Browser-local note');
    await page.locator('#knowledge-category-input').fill('Study');
    await page.locator('#knowledge-body-input').fill('A real note saved only in this browser.');
    await page.locator('#knowledge-form button[type="submit"]').click();
    await expect(page.locator('#knowledge-status')).toContainText('Document saved locally');
    await expect(page.locator('#knowledge-list')).toContainText('Browser-local note');

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(overflow).toBe(false);
    await expect(page.locator('.mobile-bottom-nav, .mobile-bottom-item')).toHaveCount(0);

    const screenshotPath = testInfo.outputPath(`${viewport.name}-knowledge-foundation.png`);
    await page.screenshot({ path: screenshotPath });
    await testInfo.attach(`${viewport.name}-knowledge-foundation`, {
      path: screenshotPath,
      contentType: 'image/png',
    });

    expect(browserErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
  });
}
