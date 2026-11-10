// Browser workflow checks for AutoProof. Run against a live backend and frontend:
//   AUTOPROOF_URL=http://localhost:3000 AUTOPROOF_API=http://localhost:8000 node e2e/workflow.mjs
// Set AUTOPROOF_EXPECT_VERIFICATION=on|off to match how the backend was started.
import { chromium } from "playwright";

const WEB = process.env.AUTOPROOF_URL ?? "http://localhost:3000";
const expectVerification = process.env.AUTOPROOF_EXPECT_VERIFICATION ?? "on";
const failures = [];
const passes = [];

// The verdict is shown in the result panel and echoed in the status line; target the panel only.
const verdict = (page, label) => page.locator(".verdict", { hasText: label });

function check(name, condition, detail = "") {
  (condition ? passes : failures).push(`${condition ? "PASS" : "FAIL"} ${name}${detail && !condition ? ` — ${detail}` : ""}`);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto(WEB, { waitUntil: "networkidle" });
  await page.getByLabel("Fixed statement").getByText("theorem autoproof_goal (n : Nat) : n + 0 = n := by").waitFor({ timeout: 15000 });
  check("statement is shown read-only from the server", true);

  const verificationLabel = (await page.locator(".progress-row strong").textContent())?.trim();
  check(
    `verification state shown as ${expectVerification}`,
    verificationLabel === (expectVerification === "on" ? "On" : "Off"),
    `saw ${verificationLabel}`,
  );

  const editor = page.getByLabel("Tactic proof");
  await editor.fill("simp");
  await page.getByRole("button", { name: /Run proof/ }).click();

  if (expectVerification === "on") {
    await verdict(page, "Verified.").waitFor({ timeout: 60000 });
    check("correct proof is reported Verified", true);
    const treeText = await page.locator(".tree-list").textContent();
    check("proof tree records the attempt as Verified", treeText?.includes("simp") && treeText.includes("Verified"), treeText ?? "");

    await editor.fill("sorry");
    await page.getByRole("button", { name: /Run proof/ }).click();
    await verdict(page, "Incomplete.").waitFor({ timeout: 60000 });
    check("sorry is reported Incomplete, not Verified", true);
    check("diagnostics explain the sorry", (await page.locator(".diagnostics").textContent())?.includes("sorry") ?? false);

    await editor.fill("exact absurd");
    await page.getByRole("button", { name: /Run proof/ }).click();
    await verdict(page, "Rejected.").waitFor({ timeout: 60000 });
    check("wrong tactic is reported Rejected", true);

    await editor.fill("simp");
    await page.getByRole("button", { name: /Run proof/ }).click();
    await verdict(page, "Verified.").waitFor({ timeout: 60000 });
    await editor.fill("simp\n  rfl");
    check("editing after a check marks the verdict as stale", await page.getByText("has changed since this check").isVisible());
    await editor.fill("simp");
  } else {
    await verdict(page, "Checking turned off.").waitFor({ timeout: 30000 });
    check("disabled checking is reported without claiming verification", true);
    check("verification-off banner is shown", await page.getByText("Lean checking is turned off").isVisible());
    await editor.fill("simp");
  }

  await page.getByRole("button", { name: /Run proof/ }).click();
  await verdict(page, expectVerification === "on" ? "Verified." : "Checking turned off.").waitFor({ timeout: 60000 });
  await editor.fill("");
  await editor.press("Control+Enter");
  check("empty proof is refused locally with guidance", ((await page.locator(".editor-status").textContent()) ?? "").includes("Write a tactic proof first"));
  await editor.fill("simp");
  await editor.press("Control+Enter");
  await verdict(page, expectVerification === "on" ? "Verified." : "Checking turned off.").waitFor({ timeout: 60000 });
  check("Ctrl+Enter runs the proof", true);

  const suggestion = page.locator(".suggestion").first();
  const tactic = (await suggestion.locator(".tactic").textContent())?.trim() ?? "";
  await suggestion.click();
  check("suggestion is inserted into the proof, not auto-verified", (await editor.inputValue()).includes(tactic));

  await editor.fill("simp");
  await page.getByRole("button", { name: "Search values 0–12" }).click();
  await page.getByText("evidence, not a proof", { exact: false }).waitFor({ timeout: 15000 });
  check("counterexample search reports no witness honestly", true);

  await page.getByRole("button", { name: "✓ Approve" }).click();
  await page.getByText("Feedback recorded", { exact: false }).waitFor({ timeout: 15000 });
  check("feedback is sent to the API and confirmed", true);

  check("no uncaught page errors", pageErrors.length === 0, pageErrors.join(" | "));

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await mobile.goto(WEB, { waitUntil: "networkidle" });
  await mobile.getByLabel("Fixed statement").waitFor({ timeout: 15000 });
  const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check("no horizontal scrolling at phone width", overflow <= 0, `overflow ${overflow}px`);
  await mobile.close();
} catch (error) {
  failures.push(`FAIL unexpected error — ${error instanceof Error ? error.message : String(error)}`);
} finally {
  await browser.close();
}

for (const line of [...passes, ...failures]) console.log(line);
console.log(`\n${passes.length} passed, ${failures.length} failed`);
process.exit(failures.length ? 1 : 0);
