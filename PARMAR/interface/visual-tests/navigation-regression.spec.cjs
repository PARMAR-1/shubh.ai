const { test, expect } = require('@playwright/test');

const viewports = [
  { name: 'desktop-1440x1000', width: 1440, height: 1000 },
  { name: 'tablet-768x1024', width: 768, height: 1024 },
  { name: 'mobile-390x844', width: 390, height: 844 },
];

const destinations = [
  'home',
  'chat',
  'research',
  'voki',
  'aether',
  'core',
  'safety',
  'simulator',
  'memory',
  'approvals',
  'tools',
  'knowledge',
  'learning',
  'workflow',
  'agents',
  'integrations',
  'analytics',
  'system',
  'settings',
  'phone',
  'audit',
];

async function openNavigation(page) {
  const toggle = page.getByRole('button', { name: 'Open navigation' });
  if (await toggle.isVisible()) {
    await toggle.click();
    await expect(page.locator('#sidebar-close')).toBeFocused();
  }
}

async function expectNoBottomNavigation(page) {
  await expect(page.locator(
    '.mobile-bottom-nav, .mobile-bottom-item, .voki-ecosystem-launcher, [data-bottom-navigation], [data-navigation-dock], [class*="bottom-dock"], [class*="destination-tabs"]',
  )).toHaveCount(0);

  const bottomAnchors = await page.evaluate(() => {
    const candidates = document.querySelectorAll(
      'nav, [role="navigation"], [role="toolbar"], [data-section], a[href^="#section-"]',
    );
    return Array.from(candidates).filter((element) => {
      const style = window.getComputedStyle(element);
      if (!['fixed', 'sticky', 'absolute'].includes(style.position)) return false;
      const rect = element.getBoundingClientRect();
      const anchoredAtBottom = window.innerHeight - rect.bottom <= 40;
      const occupiesBottomArea = rect.top >= window.innerHeight * 0.55;
      return anchoredAtBottom && occupiesBottomArea && rect.width > 0 && rect.height > 0;
    }).map((element) => ({
      tag: element.tagName,
      className: typeof element.className === 'string' ? element.className : '',
      label: element.getAttribute('aria-label') || element.textContent.trim().slice(0, 80),
    }));
  });
  expect(bottomAnchors, 'No fixed, sticky, or bottom-positioned destination controls').toEqual([]);
}

for (const viewport of viewports) {
  test(`all PARMAR destinations use non-bottom navigation at ${viewport.name}`, async ({ page }) => {
    const browserErrors = [];
    const failedRequests = [];
    page.on('pageerror', (error) => browserErrors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') browserErrors.push(message.text());
    });
    page.on('requestfailed', (request) => failedRequests.push(request.url()));
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto('/');

    for (const destination of destinations) {
      await openNavigation(page);
      const link = page.locator(`#sidebar .nav-item[data-section="${destination}"]:not([data-action])`);
      await expect(link, `${destination} must remain reachable through the sidebar`).toHaveCount(1);
      await expect(link).toHaveAttribute('aria-label', /\S/);
      await link.click();
      await expect(page.locator(`#section-${destination}`)).toHaveClass(/\bactive\b/);
      await expectNoBottomNavigation(page);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      expect(overflow, `${destination} must not create horizontal overflow at ${viewport.name}`).toBe(false);

      if (destination === 'voki') {
        await page.getByRole('button', { name: 'Return to PARMAR workspace' }).click();
        await expect(page.locator('#section-home')).toHaveClass(/\bactive\b/);
      }
    }

    if (viewport.width <= 920) {
      await openNavigation(page);
      await page.keyboard.press('Escape');
      await expect(page.locator('#sidebar-toggle')).toBeFocused();
      await expect(page.locator('#sidebar')).toHaveAttribute('aria-hidden', 'true');
    } else {
      const home = page.locator('#sidebar .nav-item[data-section="home"]');
      await home.focus();
      await page.keyboard.press('Enter');
      await expect(page.locator('#section-home')).toHaveClass(/\bactive\b/);
    }

    await openNavigation(page);
    await page.locator('#sidebar [data-section="settings"]').click();
    await page.locator('[data-settings-category="privacy"]').click();
    await expect(page.locator('#settings-panel-privacy')).toBeVisible();
    await expectNoBottomNavigation(page);
    expect(browserErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
  });
}

test('Knowledge Hub edits, searches, and reopens browser-local documents', async ({ page }) => {
  await page.goto('/');
  await page.locator('#sidebar [data-section="knowledge"]').click();

  await page.locator('#knowledge-title-input').fill('   ');
  await page.locator('#knowledge-category-input').fill('Study');
  await page.locator('#knowledge-body-input').fill('A private body phrase for searching.');
  await page.locator('#knowledge-form button[type="submit"]').click();
  await expect(page.locator('#knowledge-status')).toContainText('Enter a document title.');
  await expect(page.locator('#knowledge-title-input')).toHaveAttribute('aria-invalid', 'true');

  await page.locator('#knowledge-title-input').fill('Browser-local note');
  await page.locator('#knowledge-form button[type="submit"]').click();
  await expect(page.locator('#knowledge-status')).toContainText('Document saved locally.');
  await expect(page.locator('#knowledge-list')).toContainText('Browser-local note');

  await page.locator('#knowledge-search').fill('body phrase');
  await expect(page.locator('#knowledge-list')).toContainText('Browser-local note');
  await page.locator('#knowledge-search').fill('Browser-local');
  await expect(page.locator('#knowledge-list')).toContainText('A private body phrase for searching.');

  await page.locator('#knowledge-search').fill('');
  await page.getByRole('button', { name: 'Edit Browser-local note' }).click();
  await expect(page.locator('#knowledge-title-input')).toHaveValue('Browser-local note');
  await page.locator('#knowledge-title-input').fill('Edited note');
  await page.locator('#knowledge-category-input').fill('Reference');
  await page.locator('#knowledge-body-input').fill('Saved edits survive a reload.');
  await page.locator('#knowledge-form button[type="submit"]').click();
  await expect(page.locator('#knowledge-status')).toContainText('Document saved locally.');
  await expect(page.locator('#knowledge-list')).toContainText('Reference');

  await page.reload();
  await page.locator('#sidebar [data-section="knowledge"]').click();
  await page.getByRole('button', { name: 'Edit Edited note' }).click();
  await expect(page.locator('#knowledge-body-input')).toHaveValue('Saved edits survive a reload.');
  await expect(page.locator('#knowledge-category-input')).toHaveValue('Reference');
});

test('Knowledge Hub reports storage failures without showing success', async ({ page }) => {
  await page.addInitScript(() => {
    const originalSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function setItem(key, value) {
      if (key === 'parmar-knowledge-v1') throw new DOMException('Storage quota exceeded', 'QuotaExceededError');
      return originalSetItem.call(this, key, value);
    };
  });
  await page.goto('/');
  await page.locator('#sidebar [data-section="knowledge"]').click();
  await page.locator('#knowledge-title-input').fill('Unsaved note');
  await page.locator('#knowledge-body-input').fill('This must not appear as saved.');
  await page.locator('#knowledge-form button[type="submit"]').click();
  await expect(page.locator('#knowledge-status')).toContainText('Could not save');
  await expect(page.locator('#knowledge-status')).not.toContainText('Document saved');
  await expect(page.locator('#knowledge-title-input')).toHaveValue('Unsaved note');
  await expect(page.locator('#knowledge-list')).not.toContainText('Unsaved note');
});

test('Knowledge Hub reports unreadable saved data without replacing it', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('parmar-knowledge-v1', '{invalid'));
  await page.goto('/');
  await page.locator('#sidebar [data-section="knowledge"]').click();
  await expect(page.locator('#knowledge-status')).toContainText('Could not read saved knowledge data');
  await expect(page.locator('#knowledge-empty')).toContainText('could not be read');
  await page.locator('#knowledge-title-input').fill('Do not replace saved data');
  await page.locator('#knowledge-body-input').fill('The malformed saved content remains untouched.');
  await page.locator('#knowledge-form button[type="submit"]').click();
  await expect(page.locator('#knowledge-status')).toContainText('No changes were saved');
  expect(await page.evaluate(() => localStorage.getItem('parmar-knowledge-v1'))).toBe('{invalid');
});

test('Learning sessions are editable and totals derive from saved session minutes', async ({ page }) => {
  await page.goto('/');
  await page.locator('#sidebar [data-section="learning"]').click();
  await page.locator('#learning-subject-input').fill('Physics');
  await page.locator('#learning-topic-input').fill('Kinematics');
  await page.locator('#learning-notes-input').fill('Review motion equations.');
  await page.locator('#learning-minutes-input').fill('0');
  expect(await page.locator('#learning-minutes-input').evaluate((input) => input.checkValidity())).toBe(false);
  await expect(page.locator('#learning-list')).not.toContainText('Physics · Kinematics');
  await page.locator('#learning-minutes-input').fill('40');
  await page.locator('#learning-form button[type="submit"]').click();
  await expect(page.locator('#learning-total')).toHaveText('40');
  await page.reload();
  await page.locator('#sidebar [data-section="learning"]').click();
  await page.getByRole('button', { name: 'Edit Physics · Kinematics' }).click();
  await page.locator('#learning-subject-input').fill('Mathematics');
  await page.locator('#learning-topic-input').fill('Limits');
  await page.locator('#learning-minutes-input').fill('65');
  await page.locator('#learning-form button[type="submit"]').click();
  await expect(page.locator('#learning-total')).toHaveText('65');
  await page.reload();
  await page.locator('#sidebar [data-section="learning"]').click();
  await expect(page.locator('#learning-total')).toHaveText('65');
  await expect(page.locator('#learning-list')).toContainText('Mathematics · Limits');
});

test('workflow drafts reject missing configuration and invalid connections', async ({ page }) => {
  await page.goto('/');
  await page.locator('#sidebar [data-section="workflow"]').click();
  await page.locator('#workflow-name-input').fill('Review flow');
  await page.locator('#workflow-nodes-input').fill('Review input\nCheck policy');
  await page.locator('#workflow-connections-input').fill('Review input -> Check policy');
  await page.locator('#workflow-form button[type="submit"]').click();
  await expect(page.locator('#workflow-status')).toContainText('Configure each node');

  await page.locator('#workflow-nodes-input').fill('input: Request | User request\npolicy-review: Review | Apply policy');
  await page.locator('#workflow-connections-input').fill('Request -> Missing');
  await page.locator('#workflow-form button[type="submit"]').click();
  await expect(page.locator('#workflow-status')).toContainText('refers to a node that does not exist');

  await page.locator('#workflow-connections-input').fill('Request -> Review');
  await page.locator('#workflow-form button[type="submit"]').click();
  await expect(page.locator('#workflow-list')).toContainText('VALIDATED LOCAL DRAFT');
  await expect(page.locator('#workflow-list')).toContainText('NOT EXECUTABLE');
  await page.reload();
  await page.locator('#sidebar [data-section="workflow"]').click();
  await page.getByRole('button', { name: 'Edit Review flow' }).click();
  await expect(page.locator('#workflow-nodes-input')).toHaveValue('input: Request | User request\npolicy-review: Review | Apply policy');
});

test('Agent Studio and integration drafts remain unexecutable and disconnected', async ({ page }) => {
  await page.goto('/');
  await page.locator('#sidebar [data-section="agents"]').click();
  await page.locator('#agent-name-input').fill('Local reviewer draft');
  await page.locator('#agent-role-input').fill('Summarize locally stored notes only.');
  await page.locator('#agent-form button[type="submit"]').click();
  await expect(page.locator('#agent-status')).toContainText('not an executable agent');
  await page.getByRole('button', { name: 'Edit Local reviewer draft' }).click();
  await expect(page.locator('#agent-role-input')).toHaveValue('Summarize locally stored notes only.');
  await expect(page.locator('#agent-list')).toContainText('CONFIGURATION DRAFT · NOT EXECUTABLE');

  await page.locator('#sidebar [data-section="integrations"]').click();
  await page.locator('#integration-name-input').fill('Repository request');
  await page.locator('#integration-permissions-input').fill('Read repository metadata\nRead repository metadata');
  await page.locator('#integration-form button[type="submit"]').click();
  await expect(page.locator('#integration-status')).toContainText('must be unique');
  await expect(page.locator('#integration-empty')).toContainText('No connection drafts saved');
  await page.locator('#integration-permissions-input').fill('Read repository metadata\nCreate draft issue');
  await page.locator('#integration-form button[type="submit"]').click();
  await expect(page.locator('#integration-status')).toContainText('No provider is connected');
  await page.getByRole('button', { name: 'Edit Repository request' }).click();
  await expect(page.locator('#integration-permissions-input')).toHaveValue('Read repository metadata\nCreate draft issue');
  await expect(page.locator('#integration-list')).toContainText('CONNECTION DRAFT · NOT CONNECTED');
});

test('Marketplace metadata is validated but never represented as verified or installable', async ({ page }) => {
  await page.goto('/');
  await page.locator('#sidebar [data-section="tools"]').click();
  await page.locator('#marketplace-name-input').fill('Example extension');
  await page.locator('#marketplace-source-input').fill('javascript:alert(1)');
  await page.locator('#marketplace-form button[type="submit"]').click();
  await expect(page.locator('#marketplace-status')).toContainText('No source is contacted');

  await page.locator('#marketplace-source-input').fill('example/extension');
  await page.locator('#marketplace-permissions-input').fill('read.workspace\nread.workspace');
  await page.locator('#marketplace-form button[type="submit"]').click();
  await expect(page.locator('#marketplace-status')).toContainText('must be unique');
  await expect(page.locator('#marketplace-empty')).toContainText('No verified plugin listings are available');
  await page.locator('#marketplace-permissions-input').fill('read.workspace\nwrite.draft');
  await page.locator('#marketplace-form button[type="submit"]').click();
  await expect(page.locator('#marketplace-status')).toContainText('remains unverified and non-installable');
  await expect(page.locator('#marketplace-list')).toContainText('UNVERIFIED');
  await expect(page.locator('#marketplace-list')).toContainText('INSTALLATION DISABLED');
  await expect(page.locator('#marketplace-list button')).toHaveCount(1);
});

test('PARMAR Core copies only the current displayed review summary on explicit request', async ({ page }) => {
  await page.goto('/');
  await page.locator('#sidebar [data-section="safety"]').click();
  await expect(page.locator('#copy-decision-summary')).toBeDisabled();
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: async (text) => { window.copiedReviewSummary = text; },
      },
    });
    window.updateState({
      analysis: {
        status: 'SAFE',
        lifecycle: { current_state: 'IDLE' },
        enforcement: { status: 'ALLOWED', execution_allowed: false },
        request: 'Test review request',
        message: 'Test review returned by the local fixture.',
        risk: { risk_level: 'LOW' },
        explanation: {
          what_requested: 'Test review request',
          what_detected: 'No external action requested',
          what_risks_found: 'No risk was reported by the fixture',
        },
      },
    }, false, false);
  });
  await expect(page.locator('#copy-decision-summary')).toBeEnabled();
  await page.locator('#copy-decision-summary').click();
  await expect(page.locator('#copy-decision-status')).toContainText('copied');
  const copied = await page.evaluate(() => window.copiedReviewSummary);
  expect(copied).toContain('PARMAR review summary (as displayed)');
  expect(copied).toContain('Status: READY');
  expect(copied).toContain('Risk: LOW');
  expect(copied).toContain('Request: Test review request');
  expect(copied).not.toContain('APPROVED');
});
