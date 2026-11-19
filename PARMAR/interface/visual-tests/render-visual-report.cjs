const fs = require('node:fs');
const path = require('node:path');

module.exports = async function renderVisualReport() {
  const artifactDirectory = path.join(__dirname, 'artifacts');
  const testResultDirectory = path.join(artifactDirectory, 'test-results');
  const evidencePaths = [];
  const visit = (directory) => {
    if (!fs.existsSync(directory)) return;
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(entryPath);
      else if (entry.name.endsWith('-evidence.json')) evidencePaths.push(entryPath);
    }
  };
  visit(testResultDirectory);
  const evidence = evidencePaths.sort().map((file) => JSON.parse(fs.readFileSync(file, 'utf8')));
  const failures = evidence.some((result) => result.status !== 'PASS');
  const overall = evidence.length === 0 ? 'BLOCKED' : failures ? 'FAIL' : 'PARTIAL';
  const lines = [
    '# VOKKI automated visual acceptance',
    '',
    `**Overall: ${overall}.** Automated status only; human visual review is required before marking acceptance PASS.`,
    '',
    'Automated checks cover viewport overflow, primary-area clipping/overlap, canonical portrait loading and rendered screenshot pixels, lifecycle copy, control state, keyboard focus, reduced motion, browser errors, and remote requests.',
    '',
    '| Viewport | Screenshot | State | Automated result | Findings |',
    '|---|---|---|---|---|',
  ];

  for (const result of evidence) {
    if (result.screenshots.length === 0) {
      lines.push(`| ${result.viewport.width}x${result.viewport.height} | — | no capture | BLOCKED | ${result.findings.join('; ') || 'No screenshot evidence produced.'} |`);
      continue;
    }
    for (const screenshot of result.screenshots) {
      const status = result.status === 'PASS' ? 'PASS' : 'FAIL';
      const findings = [
        ...screenshot.findings,
        ...(result.findings || []),
        ...(result.externalRequests || []).map((url) => `Unexpected remote request: ${url}`),
        ...(result.browserErrors || []).map((error) => `Browser error: ${error}`),
      ];
      lines.push(`| ${screenshot.dimensions} | \`${screenshot.path}\` | ${screenshot.state} | ${status} | ${findings.join('; ') || 'None detected by automated checks; manual image review pending.'} |`);
    }
  }

  if (evidence.length === 0) {
    lines.push('| 1440x1000, 768x1024, 390x844 | — | CI browser run not available | BLOCKED | Run the pinned Playwright workflow to generate application screenshots. |');
  }
  lines.push('', 'A `PASS` means only that the listed automated assertions passed. The overall status stays `PARTIAL` until a human inspects the actual screenshot artifacts.');
  fs.mkdirSync(artifactDirectory, { recursive: true });
  fs.writeFileSync(path.join(artifactDirectory, 'visual-acceptance.md'), `${lines.join('\n')}\n`);
};
