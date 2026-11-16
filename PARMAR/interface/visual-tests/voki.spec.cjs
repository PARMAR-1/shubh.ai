const fs = require('node:fs');
const path = require('node:path');
const { test, expect } = require('@playwright/test');

const viewports = [
  { name: 'desktop-1440x1000', width: 1440, height: 1000 },
  { name: 'tablet-768x1024', width: 768, height: 1024 },
  { name: 'mobile-390x844', width: 390, height: 844 },
];

function cssTimeInSeconds(value) {
  const parsed = Number.parseFloat(value);
  return value.trim().endsWith('ms') ? parsed / 1000 : parsed;
}

async function inspectLayout(page) {
  return page.evaluate(() => {
    const root = document.querySelector('#section-voki');
    const surface = root?.querySelector('[data-voki-interface]');
    const selectors = [
      '.voki-presence-stage',
      '.voki-speech-controls',
      '.voki-interface-form',
      '.voki-identity-onboarding',
      '.voki-interface-approval',
    ];
    const elements = selectors.flatMap((selector) => {
      const element = surface?.querySelector(selector);
      if (!element || element.hidden || getComputedStyle(element).display === 'none') return [];
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return [{
        selector,
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        right: Math.round(rect.right),
        bottom: Math.round(rect.bottom),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        horizontalContentOverflow: element.scrollWidth > element.clientWidth + 1,
        clipped: selector !== '.voki-presence-stage' && (
          (['hidden', 'clip'].includes(style.overflowX)
            && element.scrollWidth > element.clientWidth + 1)
          || (['hidden', 'clip'].includes(style.overflowY)
            && element.scrollHeight > element.clientHeight + 1)
        ),
      }];
    });
    const overlaps = [];
    for (let first = 0; first < elements.length; first += 1) {
      for (let second = first + 1; second < elements.length; second += 1) {
        const a = elements[first];
        const b = elements[second];
        if (a.selector === '.voki-presence-stage' || b.selector === '.voki-presence-stage') continue;
        if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) {
          overlaps.push(`${a.selector} overlaps ${b.selector}`);
        }
      }
    }
    const portrait = surface?.querySelector('.voki-avatar-default-image');
    const stage = surface?.querySelector('.voki-presence-stage');
    const lifecycle = surface?.querySelector('[data-voki-lifecycle]');
    const text = surface?.innerText || '';
    const visibleText = [];
    const textWalker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (textWalker.nextNode()) {
      const node = textWalker.currentNode;
      const parent = node.parentElement;
      if (!parent || !node.textContent?.trim() || parent.closest('.sr-only, [aria-hidden="true"]')) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const rect = range.getBoundingClientRect();
      if (rect.width > 1 && rect.height > 1) visibleText.push(node.textContent.trim());
    }
    const portraitRect = portrait?.getBoundingClientRect();
    const stageRect = stage?.getBoundingClientRect();

    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      documentWidth: document.documentElement.scrollWidth,
      sectionWidth: root?.scrollWidth ?? 0,
      sectionClientWidth: root?.clientWidth ?? 0,
      horizontalOverflow: (
        document.documentElement.scrollWidth > window.innerWidth
        || (root?.scrollWidth ?? 0) > (root?.clientWidth ?? 0) + 1
      ),
      elements,
      clippedElements: elements.filter((element) => element.clipped).map((element) => element.selector),
      overlaps,
      portrait: {
        source: portrait?.getAttribute('src') ?? '',
        loaded: Boolean(portrait?.complete && portrait.naturalWidth > 0),
        width: portrait?.naturalWidth ?? 0,
        height: portrait?.naturalHeight ?? 0,
        proportional: Boolean(
          portraitRect
          && Math.abs(
            (portraitRect.width / portraitRect.height)
            - (portrait?.naturalWidth / portrait?.naturalHeight),
          ) < 0.01,
        ),
      },
      stage: {
        width: Math.round(stageRect?.width ?? 0),
        height: Math.round(stageRect?.height ?? 0),
      },
      referenceComposition: getComputedStyle(stage).getPropertyValue('--voki-reference-composition').trim(),
      visibleText,
      lifecycle: lifecycle?.textContent?.trim() ?? '',
      misleadingPresenceClaim: /\b(?:VOKKI\s+(?:is\s+)?(?:online|connected|processing)|connected\s+to\s+(?:PARMAR|core))\b/i.test(text),
      visible: {
        presence: Boolean(surface?.querySelector('.voki-presence-stage')?.getClientRects().length),
        conversation: Boolean(surface?.querySelector('.voki-interface-output')?.getClientRects().length),
        composer: Boolean(surface?.querySelector('.voki-interface-form')?.getClientRects().length),
      },
    };
  });
}

for (const viewport of viewports) {
  test(`VOKKI responsive interaction and evidence: ${viewport.name}`, async ({ page }, testInfo) => {
    const evidence = {
      viewport: { width: viewport.width, height: viewport.height },
      status: 'FAIL',
      findings: [],
      browserErrors: [],
      externalRequests: [],
      screenshots: [],
      checks: {},
    };
    const origin = new URL(testInfo.project.use.baseURL).origin;
    page.on('pageerror', (error) => evidence.browserErrors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') evidence.browserErrors.push(message.text());
    });
    await page.route('**/*', async (route) => {
      const requestUrl = new URL(route.request().url());
      if (requestUrl.origin !== origin) {
        evidence.externalRequests.push(requestUrl.href);
        await route.abort();
        return;
      }
      await route.continue();
    });

    try {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.goto('/');
      const capabilityNavigation = page.locator('.capability-toolbar [data-section="voki"]');
      const mobileNavigation = page.locator('.mobile-bottom-item[data-section="voki"]');
      if (await capabilityNavigation.isVisible()) {
        await capabilityNavigation.click();
      } else if (await mobileNavigation.isVisible()) {
        await mobileNavigation.click();
      } else {
        await page.locator('#sidebar-toggle').click();
        await page.locator('#sidebar [data-section="voki"]').click();
      }

      const workspace = page.locator('#section-voki');
      await expect(workspace).toHaveClass(/active/);
      await expect(workspace.getByRole('heading', { name: 'VOKKI', exact: true })).toBeVisible();
      if (viewport.width <= 760) {
        await expect(page.locator('.mobile-bottom-nav')).toBeVisible();
      }
      await expect(workspace.locator('.voki-presence-stage')).toBeVisible();
      await expect(workspace.locator('.voki-interface-output')).toBeVisible();
      await expect(workspace.locator('.voki-interface-form')).toBeVisible();
      await expect(workspace.locator('[data-voki-lifecycle]')).toHaveText('STATE UNAVAILABLE');

      const portrait = workspace.locator('.voki-avatar-default-image');
      await expect(portrait).toHaveAttribute('src', '/static/assets/voki/Vokki.png');
      await expect.poll(() => portrait.evaluate((image) => image.complete && image.naturalWidth > 0)).toBe(true);

      const initialLayout = await inspectLayout(page);
      evidence.checks.layout = initialLayout;
      expect(initialLayout.horizontalOverflow, 'horizontal overflow at the requested viewport').toBe(false);
      expect(initialLayout.clippedElements, 'primary content clipping').toEqual([]);
      expect(initialLayout.overlaps, 'primary content overlap').toEqual([]);
      expect(initialLayout.elements.every((element) => (
        element.left >= -1 && element.right <= viewport.width + 1
      )), 'primary content remains within the viewport width').toBe(true);
      expect(initialLayout.portrait.loaded, 'canonical VOKKI portrait loads').toBe(true);
      expect(initialLayout.portrait.width).toBe(1672);
      expect(initialLayout.portrait.height).toBe(941);
      expect(initialLayout.portrait.proportional, 'the source image keeps its natural proportions').toBe(true);
      expect(initialLayout.referenceComposition).toBe(viewport.width <= 820 ? 'mobile' : 'desktop');
      expect(
        viewport.width <= 820
          ? initialLayout.stage.height >= viewport.height * 0.7
          : initialLayout.stage.width >= viewport.width * 0.9,
        'the responsive canonical portrait remains prominent',
      ).toBe(true);
      expect(initialLayout.visibleText, 'the VOKKI section has no visible text').toEqual([]);
      expect(initialLayout.misleadingPresenceClaim, 'no fabricated online/connected/processing status').toBe(false);
      expect(initialLayout.visible.presence && initialLayout.visible.conversation, 'presence and conversation areas are visible').toBe(true);

      const talkButton = workspace.locator('[data-voki-talk]');
      const voiceAvailable = await workspace.getAttribute('data-voki-input-available') === 'true';
      const voiceState = await workspace.getAttribute('data-voki-input-state');
      if (await talkButton.isDisabled()) {
        await expect(workspace.locator('[data-voki-input-status]')).toContainText(
          /unsupported|unavailable|checking|denied|error/i,
        );
      } else {
        expect(voiceAvailable, `Talk enabled while input state is ${voiceState}`).toBe(true);
      }
      await expect(workspace.locator('[data-voki-cancel-voice]')).toBeHidden();
      await expect(workspace.locator('[data-voki-stop-speech]')).toBeHidden();

      const reducedMotion = await page.locator('.voki-presence-stage').evaluate((element) => {
        const style = getComputedStyle(element);
        return {
          requested: matchMedia('(prefers-reduced-motion: reduce)').matches,
          animationDuration: style.animationDuration.split(',').map((value) => value.trim()),
          transitionDuration: style.transitionDuration.split(',').map((value) => value.trim()),
        };
      });
      expect(reducedMotion.requested).toBe(true);
      expect([...reducedMotion.animationDuration, ...reducedMotion.transitionDuration]
        .every((value) => cssTimeInSeconds(value) <= 0.00001)).toBe(true);
      evidence.checks.reducedMotion = reducedMotion;

      const topScreenshot = testInfo.outputPath(`${viewport.name}-presence.png`);
      await page.screenshot({ path: topScreenshot });
      await testInfo.attach(`${viewport.name}-presence`, { path: topScreenshot, contentType: 'image/png' });
      evidence.screenshots.push({
        path: path.relative(__dirname, topScreenshot).split(path.sep).join('/'),
        state: 'VOKKI initial, lifecycle unavailable, voice idle',
        dimensions: `${viewport.width}x${viewport.height}`,
        findings: initialLayout.clippedElements.concat(initialLayout.overlaps),
      });

      const composer = workspace.locator('[data-voki-form]');
      await composer.scrollIntoViewIfNeeded();
      const input = workspace.locator('[data-voki-input]');
      const send = workspace.locator('[data-voki-submit]');
      await expect(input).toBeEnabled();
      await expect(send).toBeEnabled();
      await input.fill('Visual acceptance draft; not submitted.');
      await expect(input).toHaveValue('Visual acceptance draft; not submitted.');

      const researchLabel = workspace.locator('.composer-research');
      const researchToggle = workspace.locator('[data-voki-research]');
      await researchLabel.click();
      await expect(researchToggle).toBeChecked();
      await researchLabel.click();
      await expect(researchToggle).not.toBeChecked();

      const speechToggle = workspace.locator('[data-voki-speech-toggle]');
      const speechStatus = await workspace.locator('[data-voki-speech-status]').innerText();
      if (await speechToggle.isEnabled()) {
        await speechToggle.check();
        await expect(speechToggle).toBeChecked();
        await speechToggle.uncheck();
        await expect(speechToggle).not.toBeChecked();
      } else {
        expect(speechStatus).toMatch(/unavailable/i);
      }

      await input.click();
      await page.keyboard.press('Tab');
      await page.keyboard.press('Tab');
      const focus = await page.evaluate(() => {
        const element = document.activeElement;
        const style = element ? getComputedStyle(element) : null;
        return {
          visible: Boolean(element?.matches(':focus-visible')),
          outlineStyle: style?.outlineStyle ?? 'none',
          outlineWidth: style?.outlineWidth ?? '0px',
          label: element?.getAttribute('aria-label') || element?.getAttribute('type') || element?.tagName || '',
        };
      });
      expect(focus.visible, 'keyboard navigation exposes a focus-visible control').toBe(true);
      expect(focus.outlineStyle).not.toBe('none');
      expect(Number.parseFloat(focus.outlineWidth)).toBeGreaterThan(0);
      evidence.checks.keyboardFocus = focus;

      const composerLayout = await inspectLayout(page);
      evidence.checks.composerLayout = composerLayout;
      expect(composerLayout.horizontalOverflow).toBe(false);
      expect(composerLayout.clippedElements).toEqual([]);
      expect(composerLayout.overlaps).toEqual([]);
      expect(composerLayout.visible.composer).toBe(true);
      const composerScreenshot = testInfo.outputPath(`${viewport.name}-composer.png`);
      await page.screenshot({ path: composerScreenshot });
      await testInfo.attach(`${viewport.name}-composer`, { path: composerScreenshot, contentType: 'image/png' });
      evidence.screenshots.push({
        path: path.relative(__dirname, composerScreenshot).split(path.sep).join('/'),
        state: 'VOKKI composer draft, research toggle reset, keyboard focus visible',
        dimensions: `${viewport.width}x${viewport.height}`,
        findings: composerLayout.clippedElements.concat(composerLayout.overlaps),
      });

      expect(evidence.externalRequests, 'the browser must not request remote providers or assets').toEqual([]);
      expect(evidence.browserErrors, 'no uncaught browser or console errors').toEqual([]);
      evidence.status = 'PASS';
    } catch (error) {
      evidence.findings.push(error.message);
      throw error;
    } finally {
      const evidencePath = testInfo.outputPath(`${viewport.name}-evidence.json`);
      fs.mkdirSync(path.dirname(evidencePath), { recursive: true });
      fs.writeFileSync(
        evidencePath,
        `${JSON.stringify(evidence, null, 2)}\n`,
      );
    }
  });
}
