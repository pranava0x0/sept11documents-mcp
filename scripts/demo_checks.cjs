/* Browser acceptance for the static demos. Start make site first.
 * NODE_PATH=<directory containing playwright> node scripts/demo_checks.cjs
 * Optional: DEMO_URL, CHROME_PATH. Nothing calls the City or a model.
 */
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const base = process.env.DEMO_URL || "http://127.0.0.1:8000";
const EXPECTED_DEMO_PANELS = 10;
if (!["localhost", "127.0.0.1"].includes(new URL(base).hostname)) {
  throw new Error("demo checks require a local preview");
}
(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {})
  });
  let checks = 0;
  try {
    for (const width of [320, 390, 768, 1024, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [], external = [];
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
      await page.route("**/*", (route) => {
        if (new URL(route.request().url()).origin !== new URL(base).origin) {
          external.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      await page.goto(base + "/examples.html");
      assert.equal(await page.locator("nav.top a:visible").count(), 5, "examples keeps every page link");
      for (const id of ["records", "help", "budget", "releases", "context", "paths", "footprint", "tracker", "collections", "timeline"]) {
        await page.locator("#tab-" + id).click();
        assert.equal(await page.locator(".demo-panel:visible").count(), 1);
        assert(await page.locator("#demo-" + id).isVisible());
        const size = await page.evaluate(() => ({
          width: innerWidth, content: document.documentElement.scrollWidth
        }));
        assert(size.content <= size.width + 1, "horizontal overflow at " + width);
        checks += 3;
      }
      // Find a record: saved examples, then the folder-label search.
      await page.locator("#tab-records").click();
      await page.selectOption("#record-choice", "clean-up-initiative");
      assert.equal(await page.locator(".record-result:visible").count(), 1);
      assert.match(await page.locator(".record-result:visible .btn").getAttribute("href"),
        /NYC-WTC_000153130\.pdf#page=1$/);
      const bounds = await page.locator(".record-result:visible .btn").boundingBox();
      if (width >= 768) {
        assert(bounds.y + bounds.height <= 900, "primary action should fit the first screen on tablet and desktop");
      }
      await page.fill("#folder-query", "john street");
      await page.locator("#folder-search .btn").click();
      await page.locator("#folder-results li").first().waitFor();
      assert.match(await page.locator("#folder-status").innerText(), /folder rows .* match/);
      assert.match(await page.locator("#folder-results li b").first().innerText(), /JOHN STREET/i);
      await page.fill("#folder-query", "zzqx nothing");
      await page.locator("#folder-search .btn").click();
      await page.locator("#folder-status", { hasText: "No folder label" }).waitFor();
      assert(await page.locator("#folder-results").isHidden());
      // Get official help: the program select, cited quotes, the issuer table.
      await page.locator("#tab-help").click();
      assert.equal(await page.locator(".help-result:visible").count(), 1);
      await page.selectOption("#help-choice", "vcf");
      assert.match(await page.locator(".help-result:visible h3").innerText(), /Victim Compensation Fund/);
      assert((await page.locator(".help-result:visible q").count()) >= 8, "the VCF entry quotes its rules");
      assert.match(await page.locator("#demo-help table").innerText(), /DCAS/);
      // Follow the money: three commitments, five stages each, notes from the ledger.
      await page.locator("#tab-budget").click();
      await page.selectOption("#budget-choice", "education");
      assert.equal(await page.locator(".budget-result:visible .amount").innerText(), "$1 million");
      assert.equal(await page.locator(".budget-result:visible .evidence-stages button").count(), 5);
      await page.locator('.budget-result:visible .evidence-stages [data-stage="payment"]').click();
      assert.match(await page.locator('.budget-result:visible .stage-notes [data-stage="payment"]').innerText(), /unknown/);
      assert(await page.locator('.budget-result:visible .stage-notes [data-stage="announcement"]').isHidden());
      await page.selectOption("#budget-choice", "doi");
      assert.equal(await page.locator('.budget-result:visible .evidence-stages [data-stage="announcement"]').getAttribute("aria-pressed"), "true");
      assert.match(await page.locator(".budget-result:visible .amount").innerText(), /\$4 million/);
      await page.selectOption("#budget-choice", "portal");
      // Check releases: the capture and the dated obligations.
      await page.locator("#tab-releases").click();
      assert((await page.locator(".due-list li").count()) >= 3);
      await page.locator("#tab-context").click();
      assert.match(await page.locator("#demo-context").innerText(), /INDEPENDENT PROJECT/);
      await page.locator("#tab-paths").click();
      await page.selectOption("#path-choice", "source-page");
      assert.match(await page.locator(".research-path:visible").innerText(), /Bates page/);
      await page.locator("#tab-footprint").click();
      assert.match(await page.locator("#demo-footprint").innerText(), /24,441/);
      await page.locator("#tab-tracker").click();
      await page.selectOption("#release-choice", "privilege-log");
      assert.match(await page.locator(".release-result:visible").innerText(), /privilege log/i);
      await page.locator("#tab-collections").click();
      await page.selectOption("#collection-choice", "WTC 7");
      assert.match(await page.locator(".collection-result:visible").innerText(), /WTC 7/);
      await page.locator("#tab-timeline").click();
      assert.equal(await page.locator(".release-timeline li").count(), 4);
      await page.locator("#tab-budget").focus();
      await page.keyboard.press("ArrowRight");
      assert.equal(await page.locator("#tab-releases").getAttribute("aria-selected"), "true");
      await page.locator("#tab-records").click();
      await page.goBack();
      assert(await page.locator("#demo-releases").isVisible());
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
      checks += 22;
      // Detail pages retain direct navigation and stay inside the viewport.
      for (const file of ["index.html", "records.html", "toolkit.html", "community.html"]) {
        await page.goto(base + "/" + file);
        assert.equal(await page.locator('nav.top a[aria-current="page"]').count(), 1);
        assert.equal(await page.locator("nav.top a:visible").count(), 5);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        checks += 3;
      }
      // Recorded examples: one shown at a time, a hash link selects the right one.
      await page.goto(base + "/toolkit.html#example-budget_lookup");
      assert.equal(await page.locator(".example:visible").count(), 1);
      assert(await page.locator("#example-budget_lookup").isVisible());
      await page.selectOption("#example-choice", "portal_search");
      assert.match(await page.locator("#example-portal_search").innerText(), /refused/);
      checks += 3;
      await page.close();
    }
    const direct = await browser.newPage();
    await direct.goto(base + "/examples.html#demo-budget");
    assert(await direct.locator("#demo-budget").isVisible());
    await direct.route("**/data/watchdog/latest.json", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: '{"documents":null}' }));
    await direct.goto(base + "/examples.html#demo-releases");
    await direct.reload();
    await direct.locator("#watchdog-live").getByText("Unavailable", { exact: true }).waitFor();
    assert(!/NaN/.test(await direct.locator("#watchdog-live").innerText()));
    await direct.close();
    const nojs = await browser.newPage({ javaScriptEnabled: false });
    await nojs.goto(base + "/examples.html");
    assert.equal(await nojs.locator(".demo-panel:visible").count(), EXPECTED_DEMO_PANELS);
    await nojs.goto(base + "/index.html");
    assert((await nojs.locator("#money .ledger div").count()) === 3, "three announced commitments on the landing page");
    await nojs.goto(base + "/examples.html");
    assert.equal(await nojs.locator(".demo-panel:visible").count(), EXPECTED_DEMO_PANELS);
    assert.equal(await nojs.locator(".record-result:visible").count(), 3);
    assert.equal(await nojs.locator(".help-result:visible").count(), 2);
    assert.equal(await nojs.locator(".budget-result:visible").count(), 3);
    assert.equal(await nojs.locator(".budget-result:visible .stage-notes > div:visible").count(), 15);
    await nojs.close();
    checks += 9;
    console.log("demo_checks: " + checks + " assertions passed; five widths, keyboard/history, no-JS and bad-data fallback");
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
