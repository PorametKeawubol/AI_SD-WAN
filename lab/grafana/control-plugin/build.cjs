const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// Grafana supplies these external modules; no credentials or vendor JS is bundled.
for (const folder of ['', 'panel']) {
  const source = path.join(__dirname, 'src', folder);
  const target = path.join(__dirname, 'dist', folder);
  fs.mkdirSync(target, { recursive: true });
  const filename = folder ? 'module.tsx' : 'module.ts';
  const result = ts.transpileModule(fs.readFileSync(path.join(source, filename), 'utf8'), {
    fileName: filename,
    reportDiagnostics: true,
    compilerOptions: {
      module: ts.ModuleKind.AMD,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.React,
      strict: true,
    },
  });
  if (result.diagnostics?.some(item => item.category === ts.DiagnosticCategory.Error)) {
    throw new Error(ts.formatDiagnosticsWithColorAndContext(result.diagnostics, {
      getCanonicalFileName: name => name, getCurrentDirectory: () => __dirname,
      getNewLine: () => '\n',
    }));
  }
  fs.writeFileSync(path.join(target, 'module.js'), result.outputText);
  for (const asset of ['plugin.json', 'logo.svg']) {
    fs.copyFileSync(path.join(source, asset), path.join(target, asset));
  }
}
console.log('Built app proxy and Control and approvals panel.');
