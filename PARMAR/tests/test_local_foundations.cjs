const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const staticDir = path.join(__dirname, '..', 'interface', 'static');
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');

test('local ecosystem foundations use real saved-state workflows', () => {
  for (const [section, title] of [
    ['knowledge', 'Knowledge Hub'],
    ['learning', 'Learning & JEE'],
    ['workflow', 'Visual Workflow Builder'],
    ['agents', 'Agent Studio'],
  ]) {
    assert.match(html, new RegExp(`data-section="${section}"`));
    assert.match(html, new RegExp(`id="section-${section}"`));
  }
  assert.match(html, /No dashboard metrics are inferred or simulated/);
  assert.match(app, /STORAGE_KEYS\.knowledge/);
  assert.match(app, /STORAGE_KEYS\.learning/);
  assert.match(app, /STORAGE_KEYS\.workflows/);
  assert.match(app, /STORAGE_KEYS\.agents/);
  assert.match(app, /STORAGE_KEYS\.integrations/);
  assert.match(app, /STORAGE_KEYS\.marketplace/);
  assert.match(html, /data-customize-category="integrations"/);
  assert.doesNotMatch(html, /data-section="integrations"|id="section-integrations"/);
});

test('workflow drafts validate node connections before saving', () => {
  assert.match(app, /nodes\.length < 2/);
  assert.match(app, /WORKFLOW_NODE_TYPES/);
  assert.match(app, /refers to a node that does not exist/);
  assert.match(app, /Validated draft saved locally/);
  assert.match(app, /NOT EXECUTABLE/);
});

test('plugin metadata remains unverified and non-installable', () => {
  assert.match(app, /UNVERIFIED/);
  assert.match(app, /INSTALLATION DISABLED/);
  assert.match(html, /No plugin metadata submitted/);
});
