"""Question-time-only session and slide-range scoping in headless Chromium against tests/serve_fake.py."""
import re, sys, time
from playwright.sync_api import sync_playwright
port, sid = sys.argv[1], sys.argv[2]
URL = f"http://127.0.0.1:{port}"
MOCK = re.search(r'MOCK = """(.*?)"""', open(__file__.replace("browser_qa_test", "browser_test")).read(), re.S).group(1)
launch = dict(executable_path=sys.argv[3], args=["--no-sandbox"]) if len(sys.argv) > 3 else {}
with sync_playwright() as p:
    b = p.chromium.launch(**launch); pg = b.new_page(viewport={"width": 1280, "height": 900}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.add_init_script(MOCK)
    pg.goto(f"{URL}/rehearse?session={sid}"); pg.wait_for_selector("#checks .chk")
    pg.fill("#rTo", "2"); pg.press("#rTo", "Tab")                                   # slides 1-2 only
    pg.wait_for_function("document.querySelector('#rangeMsg').innerText.includes('Saved: slides 1-2')")
    assert pg.eval_on_selector_all("#startSlide option", "o => o.length") == 2 and "slides 1-2 only" in pg.inner_text("#pitchName")
    pg.check("input[name=stype][value=qa]"); assert pg.is_visible("#qaN") and not pg.is_visible("#startWrap") and pg.inner_text("#start") == "Start question time"
    pg.select_option("#qaCount", "3"); pg.click("#start")
    pg.wait_for_selector("#liveWrap:not(.hidden)")
    pg.wait_for_function("document.querySelector('#feed').innerText.includes('Graham') || window.__spoken.length>=1", timeout=15000)
    assert pg.evaluate("document.body.classList.contains('qaonly')")
    for i in range(6):                                     # answer each question, then finish
        if pg.is_visible("#results:not(.hidden)"): break
        pg.wait_for_function("window.__srs.some(s=>s.running) || !document.querySelector('#doneAns').classList.contains('hidden')", timeout=15000)
        time.sleep(0.9)
        if pg.evaluate("!!document.querySelector('#results:not(.hidden)')"): break
        pg.evaluate("window.__say('we would handle that with a bench of backups and a weekly report to the client team')"); time.sleep(0.5)
        if pg.is_visible("#doneAns"): pg.click("#doneAns")
    pg.wait_for_selector("#results:not(.hidden)", timeout=30000)
    txt = pg.inner_text("#results").lower()
    assert "question time results" in txt and "judged on slides 1-2 only" in txt, txt[:300]
    assert "brief coverage" not in txt and "slide-by-slide" not in txt and "question handling" in txt and "0.6 questions + 0.4 gut" in txt
    print("page errors:", errs); assert not errs
    b.close()
print("QA BROWSER OK")
