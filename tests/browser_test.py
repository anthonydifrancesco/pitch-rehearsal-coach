"""Drive the rehearsal page in headless Chromium with MOCKED speech APIs against tests/serve_fake.py."""
import sys, time
from playwright.sync_api import sync_playwright
port, sid = sys.argv[1], sys.argv[2]
URL = f"http://127.0.0.1:{port}"
MOCK = """
(() => {
  window.__srs = [];
  class FakeSR { constructor(){ this.running=false; window.__srs.push(this); }
    start(){ this.running=true; } stop(){ this.running=false; this.onend&&this.onend(); } abort(){ this.running=false; } }
  window.SpeechRecognition = window.webkitSpeechRecognition = FakeSR;
  window.__say = text => { const sr = window.__srs.filter(s => s.running).pop(); if(!sr) return false;
    const res=[{transcript:text}]; res.isFinal=true; sr.onresult({resultIndex:0, results:[res]}); return true; };
  const voices=[['Microsoft James - English (Australia)','en-AU'],['Microsoft Catherine - English (Australia)','en-AU'],['Microsoft George - English (United Kingdom)','en-GB'],['Microsoft Hazel - English (United Kingdom)','en-GB'],['Microsoft Zira - English (United States)','en-US'],['Microsoft David - English (United States)','en-US']]
    .map(([name,lang])=>({name,lang,voiceURI:name,localService:true,default:false}));
  window.__spoken=[];
  const synth={getVoices:()=>voices,speak:u=>{window.__spoken.push({text:u.text,voice:u.voice&&u.voice.name,pitch:u.pitch}); setTimeout(()=>u.onend&&u.onend(),300);},cancel(){},onvoiceschanged:null};
  Object.defineProperty(window,'speechSynthesis',{value:synth,configurable:true});
  window.SpeechSynthesisUtterance = class { constructor(t){ this.text=t; } };
})();
"""
LONG = "so we have built a national program for AEG that recruits trains and manages fifteen chefs across seven states and we have done this before for other brands with great results"
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1280, "height": 900}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.add_init_script(MOCK)
    pg.goto(f"{URL}/rehearse?session={sid}"); pg.wait_for_selector("#checks .chk", timeout=8000)
    checks = pg.inner_text("#checks"); print("checks:", checks.replace("\n", " | "))
    assert "Speech recognition is available" in checks and "English voices found" in checks and "slide detection is off" in checks
    voices = pg.eval_on_selector_all("#voices select", "els => els.map(e => e.value)")
    assert len(voices) == 5 and len(set(voices)) >= 4, voices          # distinct voices per persona
    pg.click("[data-hear=finance]"); pg.wait_for_function("window.__spoken.length>=1"); print("hear:", pg.evaluate("window.__spoken[0]"))
    pg.fill("#presenter", "Anthony"); pg.click("#start")
    pg.wait_for_selector("#liveWrap:not(.hidden)"); assert pg.inner_text("#sNum") == "1"
    pg.wait_for_function("window.__srs.some(s=>s.running)")
    assert pg.evaluate("window.__say('hello and welcome everyone')")
    pg.click("#next"); assert pg.inner_text("#sNum") == "2"
    # speak enough words, go quiet; after the 10s settle the panel should interject
    assert pg.evaluate("t=>window.__say(t)", LONG)
    pg.wait_for_function("document.querySelector('#feed').innerText.includes('What does that cost per demo?')", timeout=25000)
    print("interjection seen; speaking classes:", pg.evaluate("document.querySelectorAll('.member.speaking').length"))
    spoken = pg.evaluate("window.__spoken"); assert any("cost per demo" in s["text"] for s in spoken)
    assert any(s["voice"] for s in spoken)
    # the presenter answers (mic re-armed after the panel finished speaking)
    pg.wait_for_function("window.__srs.some(s=>s.running)", timeout=5000); time.sleep(1.0)
    assert pg.evaluate("window.__say('good question we price per demo and we will cover that on the commercials slide')")
    # clarifying question back: the panel answers before the presenter answers
    assert pg.evaluate("window.__say('sorry do you mean per demo or for the whole contract')")
    pg.wait_for_function("document.querySelector('#feed').innerText.includes('whole contract term')", timeout=8000)
    pg.wait_for_function("window.__srs.some(s=>s.running)", timeout=5000); time.sleep(1.0)
    pg.click("#next"); pg.fill("#typed", "typed fallback works"); pg.press("#typed", "Enter")
    assert "typed fallback works" in pg.inner_text("#feed")
    # pause/resume
    pg.click("#pause"); assert pg.inner_text("#pause") == "Resume"; pg.click("#pause"); assert pg.inner_text("#pause") == "Pause"
    # Q&A
    pg.click("#finish"); pg.wait_for_function("!document.querySelector('#doneAns').classList.contains('hidden')", timeout=10000)
    pg.wait_for_function("document.querySelector('#feed').innerText.includes('ten week timeline')", timeout=10000)
    time.sleep(0.8); pg.evaluate("window.__say('we plan the weeks around the Christmas break and the contract date')"); pg.click("#doneAns")
    pg.wait_for_function("document.querySelector('#feed').innerText.includes('sick chef')", timeout=10000)
    time.sleep(0.8); pg.click("#doneAns")
    pg.wait_for_selector("#results:not(.hidden)", timeout=15000)
    txt = pg.inner_text("#results"); low = txt.lower(); print(txt[:260].replace("\n", " | "))
    for needle in ["Brief coverage", "Win likelihood", "58%", "Add a cost per demo", "waffled", "Per presenter", "Download report"]:
        assert needle.lower() in low, needle
    href = pg.get_attribute("a:has-text('Download report')", "href")
    r = pg.request.get(URL + href); assert r.status == 200 and r.body()[:2] == b"PK"
    # pre-read page: export button, rehearse button, past rehearsals
    pg.goto(URL + "/"); pg.evaluate(f"openSession('{sid}')"); pg.wait_for_selector("#exportBtn")
    assert pg.get_attribute("#exportBtn", "href").endswith("export.docx") and "/rehearse?session=" in pg.get_attribute("#rehBtn", "href")
    pg.wait_for_selector("#pastReh a", timeout=5000); print("past:", pg.inner_text("#pastReh"))
    pg.click("#pastReh a"); pg.wait_for_selector("#results:not(.hidden)"); assert "win likelihood" in pg.inner_text("#results").lower()
    # review a past pitch from the front page
    pg.goto(URL + "/"); pg.wait_for_selector("#go")
    assert not pg.is_visible("#trText") and pg.is_visible("#personas")
    pg.check("input[name=mode][value=review]")
    assert pg.is_visible("#trText") and not pg.is_visible("#personas") and pg.inner_text("#go") == "Review this pitch"
    pg.fill("#title", "Review test")
    pg.set_input_files("#deckFile", sys.argv[4]); pg.set_input_files("#rfpFile", sys.argv[5])
    pg.fill("#trText", "Anthony: " + "We recruit and train fifteen chefs across seven states. " * 8 + "\nSam: How do you cover a dropout in Perth? " + "\nAnthony: " + "We hold a bench of backups ready to cover within two days. " * 8)
    pg.select_option("#trOutcome", "Lost"); pg.click("#go")
    pg.wait_for_url("**/review?session=*", timeout=30000)
    pg.wait_for_function("document.querySelector('#main').innerText.includes('How do you cover a dropout in Perth?')", timeout=15000)
    txt = pg.inner_text("#main"); assert "outcome: Lost" in txt and "Requirements: deck and conversation" in txt and "By theme" in txt and "Follow-up owed" in txt and "Addressed in the conversation" in txt, txt[:400]
    pg.select_option("#fPri", "should"); assert pg.locator("td:has-text('Weekly report')").count() == 0
    pg.select_option("#fPri", "all"); assert pg.locator("td:has-text('Weekly report')").count() == 1
    pg.check("#fCon"); assert pg.locator("td:has-text('Weekly report')").count() == 0 and pg.locator("td:has-text('Recruit 15 chefs')").count() == 1
    pg.uncheck("#fCon")
    href = pg.get_attribute("a:has-text('Download report')", "href"); assert pg.request.get(URL + href).body()[:2] == b"PK"
    print("review UI ok")
    pg.screenshot(path=sys.argv[3] if len(sys.argv) > 3 else "/tmp/results.png", full_page=True)
    print("page errors:", errs); assert not errs
    b.close()
print("BROWSER OK")
