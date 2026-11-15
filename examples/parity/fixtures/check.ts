// Compiles positive.ts and negative.ts with the parity tsconfig (pinned TypeScript, vendored
// Effect source). positive.ts must have no diagnostics; every "// N. NAME" block in negative.ts
// must have at least one diagnostic of its own, and none may fall outside a block.
import ts from "typescript"

const fileURLToPath = (url: URL) => decodeURIComponent(url.pathname)

const config = fileURLToPath(new URL("../tsconfig.json", import.meta.url))
const positive = fileURLToPath(new URL("positive.ts", import.meta.url))
const negative = fileURLToPath(new URL("negative.ts", import.meta.url))

const parsed = ts.getParsedCommandLineOfConfigFile(config, {}, {
  ...ts.sys,
  onUnRecoverableConfigFileDiagnostic: (d) => {
    throw new Error(ts.flattenDiagnosticMessageText(d.messageText, "\n"))
  }
})
if (parsed === undefined) throw new Error("tsconfig did not parse")
const program = ts.createProgram([positive, negative], parsed.options)
const lineOf = (d: ts.Diagnostic) =>
  d.file === undefined || d.start === undefined ? -1 : d.file.getLineAndCharacterOfPosition(d.start).line + 1
const diagnosticsIn = (path: string) => {
  const file = program.getSourceFile(path)
  if (file === undefined) throw new Error(`not in program: ${path}`)
  return [...program.getSyntacticDiagnostics(file), ...program.getSemanticDiagnostics(file)]
}

const failures: Array<string> = []
for (const d of diagnosticsIn(positive)) {
  failures.push(`positive.ts:${lineOf(d)} unexpected: ${ts.flattenDiagnosticMessageText(d.messageText, " ")}`)
}

const lines = ts.sys.readFile(negative)?.split("\n") ?? []
const starts = lines.flatMap((text, i) => (/^\/\/ \d+\. [A-Z_]+/.test(text) ? [i + 1] : []))
const ends = [...starts.slice(1), lines.length + 1]
const negatives = diagnosticsIn(negative)
const report = starts.map((start, i) => {
  const end = ends[i] ?? lines.length + 1
  const own = negatives.filter((d) => lineOf(d) > start && lineOf(d) < end)
  if (own.length === 0) failures.push(`negative.ts:${start} accepted: ${lines[start - 1]}`)
  return { block: lines[start - 1], codes: [...new Set(own.map((d) => `TS${d.code}`))] }
})
for (const d of negatives) {
  if (lineOf(d) < (starts[0] ?? Infinity)) failures.push(`negative.ts:${lineOf(d)} outside any block`)
}

console.log(JSON.stringify({ typescript: ts.version, positive_diagnostics: diagnosticsIn(positive).length, negative: report }, null, 2))
if (failures.length > 0) {
  console.error(failures.join("\n"))
  ts.sys.exit(1)
}
