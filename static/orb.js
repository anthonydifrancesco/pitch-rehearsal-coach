/* PitchOrb: animated AI "presence" orb for the panel. Canvas 2D, no libraries.
   The orb reads its state from the .member element it sits in:
     .speaking  -> voice-reactive: core swells, spectrum ring jumps
     .thinking  -> rings spin up, a scanner sweeps the rim
     (listening) -> window.__orbListening() returns true while the presenter is being heard
     otherwise  -> calm idle: slow breathing, drifting particles
   Each orb draws its own dark glass disc, so the glow works on light and dark pages. */
(function () {
  'use strict';
  const TAU = Math.PI * 2;
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  function hex(c) {
    const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(c || '').trim());
    if (!m) return [74, 144, 217];
    let h = m[1]; if (h.length === 3) h = h.split('').map(x => x + x).join('');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
  const angDiff = (a, b) => Math.atan2(Math.sin(a - b), Math.cos(a - b));
  const TARGET = {            // energy (brightness/size of everything), spin (how fast the rings turn)
    idle:      { e: 0.14, spin: 0.55 },
    listening: { e: 0.30, spin: 0.95 },
    thinking:  { e: 0.55, spin: 3.4 },
    speaking:  { e: 0.85, spin: 1.7 },
  };

  class Orb {
    constructor(canvas, colour) {
      this.canvas = canvas; this.ctx = canvas.getContext('2d');
      this.base = hex(colour); this.hi = mix(this.base, [255, 255, 255], 0.6); this.mid = mix(this.base, [255, 255, 255], 0.25);
      this.deep = mix(this.base, [6, 8, 18], 0.9);
      this.p = Array.from({ length: 6 }, () => Math.random() * TAU);
      this.e = TARGET.idle.e; this.spin = TARGET.idle.spin; this.level = 0;
      this.gate = 0; this.talk = false; this.peak = 0.6; this.rot = Math.random() * TAU; this.scan = 0;
      this.stateName = 'idle'; this.size = 0; this.dpr = 1;
      this.parts = Array.from({ length: 28 }, () => ({
        r: 28 + Math.random() * 56, a: Math.random() * TAU, w: (Math.random() < 0.5 ? -1 : 1) * (0.05 + Math.random() * 0.28),
        s: 0.5 + Math.random() * 1.3, tw: Math.random() * TAU, ts: 0.8 + Math.random() * 2.2,
      }));
      this.resize();
    }
    resize() {
      const css = this.canvas.clientWidth || 120;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      if (css === this.size && dpr === this.dpr) return;
      this.size = css; this.dpr = dpr;
      this.canvas.width = Math.round(css * dpr); this.canvas.height = Math.round(css * dpr);
    }
    state() {
      const host = this.canvas.closest('.member, .focus');
      if (!host) return this.forced || 'idle';
      if (host.classList.contains('speaking')) return 'speaking';
      if (host.classList.contains('thinking')) return 'thinking';
      try { if (window.__orbListening && window.__orbListening()) return 'listening'; } catch (e) { /* ignore */ }
      return 'idle';
    }
    noise(th, t) {
      const p = this.p;
      return Math.sin(3 * th + t * 1.3 + p[0]) * 0.5 + Math.sin(5 * th - t * 1.9 + p[1]) * 0.3 + Math.sin(2 * th + t * 0.7 + p[2]) * 0.2;
    }
    update(t, dt) {
      const s = this.stateName = this.state(), tg = TARGET[s];
      const k = Math.min(1, dt * 3);
      this.e += (tg.e - this.e) * k;
      this.spin += (tg.spin * (reduce ? 0.2 : 1) - this.spin) * k;
      // The browser's speech voice exposes no audio levels, so a natural-sounding envelope is simulated:
      // short phrases of syllable-rate pulses with small gaps between them.
      let target = 0;
      if (s === 'speaking') {
        this.gate -= dt;
        if (this.gate <= 0) { this.talk = Math.random() > 0.16; this.gate = 0.1 + Math.random() * 0.45; this.peak = 0.4 + Math.random() * 0.6; }
        target = this.talk ? this.peak * (0.55 + 0.45 * Math.abs(Math.sin(t * TAU * 3.3 + this.p[3]))) : 0.04;
      }
      this.level += (target - this.level) * Math.min(1, dt * (target > this.level ? 18 : 7));
      this.rot += dt * 0.12 * this.spin;
      this.scan += dt * (s === 'thinking' ? 3.4 : 0.6);
    }
    draw(t) {
      this.resize();
      const c = this.ctx, e = this.e, L = this.level, sp = this.spin, hi = this.hi, base = this.base, mid = this.mid;
      const k = (this.size * this.dpr) / 200;
      c.setTransform(k, 0, 0, k, 0, 0);
      c.clearRect(0, 0, 200, 200);
      c.globalCompositeOperation = 'source-over';
      c.save(); c.translate(100, 100);

      // glass disc
      c.save(); c.beginPath(); c.arc(0, 0, 88, 0, TAU); c.clip();
      let g = c.createRadialGradient(0, -10, 6, 0, 0, 88);
      g.addColorStop(0, rgba(mix(base, [10, 12, 24], 0.82), 1)); g.addColorStop(1, '#04050b');
      c.fillStyle = g; c.fillRect(-90, -90, 180, 180);

      c.globalCompositeOperation = 'lighter';
      // inner haze
      g = c.createRadialGradient(0, 0, 0, 0, 0, 80);
      g.addColorStop(0, rgba(base, 0.12 + 0.2 * e + 0.25 * L)); g.addColorStop(1, rgba(base, 0));
      c.fillStyle = g; c.fillRect(-90, -90, 180, 180);

      // radial voice spectrum
      const N = 72, r0 = 56 + L * 2;
      c.lineCap = 'round';
      for (let i = 0; i < N; i++) {
        const a = (i / N) * TAU + this.rot * 0.6;
        const wob = 0.5 + 0.5 * Math.sin(i * 2.7 + t * 3.1 + this.p[4]) * Math.sin(i * 1.3 - t * 2.2 + this.p[5]);
        const jit = 0.55 + 0.45 * Math.abs(Math.sin(i * 0.5 + t * 5.2));
        let len = 2 + (e * 7 + L * 24) * wob * jit;
        let boost = 0;
        if (this.stateName === 'thinking') { boost = Math.exp(-Math.pow(angDiff(a, this.scan), 2) * 9); len += boost * 8; }
        c.strokeStyle = rgba(boost > 0.2 ? hi : mid, 0.16 + 0.55 * wob * (0.35 + e) + boost * 0.4);
        c.lineWidth = 1.5;
        c.beginPath(); c.moveTo(Math.cos(a) * r0, Math.sin(a) * r0); c.lineTo(Math.cos(a) * (r0 + len), Math.sin(a) * (r0 + len)); c.stroke();
      }

      // outer tick ring
      c.save(); c.rotate(this.rot * 0.7);
      c.strokeStyle = rgba(hi, 0.22 + 0.2 * e); c.lineWidth = 0.8;
      for (let i = 0; i < 96; i++) {
        const a = (i / 96) * TAU, long = i % 8 === 0, r1 = 83, r2 = long ? 77 : 80.5;
        c.beginPath(); c.moveTo(Math.cos(a) * r1, Math.sin(a) * r1); c.lineTo(Math.cos(a) * r2, Math.sin(a) * r2); c.stroke();
      }
      c.restore();

      // bright arc segments (drawn twice: wide faint = glow, narrow = line)
      const arcs = [[0, 1.25], [2.2, 0.7], [3.9, 0.42], [5.0, 0.95]];
      for (const pass of [[6, 0.12], [1.8, 0.85]]) {
        c.lineWidth = pass[0]; c.strokeStyle = rgba(base, pass[1] * (0.45 + e * 0.7));
        c.save(); c.rotate(-this.rot * 2.6);
        for (const [a0, len] of arcs) { c.beginPath(); c.arc(0, 0, 72, a0, a0 + len); c.stroke(); }
        c.restore();
      }
      // thin full ring + dashed inner ring
      c.lineWidth = 0.7; c.strokeStyle = rgba(hi, 0.18 + 0.1 * e);
      c.beginPath(); c.arc(0, 0, 66, 0, TAU); c.stroke();
      c.save(); c.rotate(this.rot * 4.2); c.setLineDash([1.5, 6]); c.lineWidth = 1.1; c.strokeStyle = rgba(hi, 0.4 + 0.3 * e);
      c.beginPath(); c.arc(0, 0, 47, 0, TAU); c.stroke(); c.restore(); c.setLineDash([]);

      // thinking: scanner sweep on the rim
      if (this.stateName === 'thinking') {
        c.lineWidth = 2.4; c.strokeStyle = rgba(hi, 0.85); c.beginPath(); c.arc(0, 0, 85, this.scan - 0.9, this.scan); c.stroke();
      }

      // particles
      for (const q of this.parts) {
        const a = q.a + t * q.w * sp, rr = q.r + Math.sin(t * q.ts + q.tw) * 2.5 + L * 7 * Math.sin(q.tw + t * 4);
        const al = (0.25 + 0.75 * (0.5 + 0.5 * Math.sin(t * q.ts * 1.7 + q.tw))) * (0.35 + 0.65 * (e + L * 0.5));
        c.fillStyle = rgba(hi, al); c.beginPath(); c.arc(Math.cos(a) * rr, Math.sin(a) * rr, q.s * (1 + L * 0.6), 0, TAU); c.fill();
      }

      // core bloom
      const R = 25 + e * 4 + L * 11;
      g = c.createRadialGradient(0, 0, R * 0.2, 0, 0, R * 2.9);
      g.addColorStop(0, rgba(base, 0.34 + 0.3 * e + 0.3 * L)); g.addColorStop(1, rgba(base, 0));
      c.fillStyle = g; c.beginPath(); c.arc(0, 0, R * 2.9, 0, TAU); c.fill();

      // morphing core
      const amp = 0.05 + 0.09 * e + 0.2 * L, tt = t * (1 + sp * 0.35);
      c.beginPath();
      for (let i = 0; i <= 72; i++) {
        const th = (i / 72) * TAU, rad = R * (1 + amp * this.noise(th, tt));
        const x = Math.cos(th) * rad, y = Math.sin(th) * rad;
        i ? c.lineTo(x, y) : c.moveTo(x, y);
      }
      c.closePath();
      g = c.createRadialGradient(-R * 0.18, -R * 0.2, 1, 0, 0, R * 1.25);
      g.addColorStop(0, 'rgba(255,255,255,0.97)'); g.addColorStop(0.28, rgba(hi, 0.92));
      g.addColorStop(0.72, rgba(base, 0.78)); g.addColorStop(1, rgba(base, 0.05));
      c.fillStyle = g; c.fill();

      // gyroscope loops inside the core
      c.save(); c.rotate(t * 0.55 * sp); c.lineWidth = 0.9;
      for (let i = 0; i < 3; i++) {
        c.save(); c.rotate((i * Math.PI) / 3 + t * 0.35 * i);
        c.strokeStyle = `rgba(255,255,255,${(0.32 + 0.3 * e).toFixed(3)})`;
        c.beginPath(); c.ellipse(0, 0, R * 0.78, R * (0.26 + 0.08 * Math.sin(t * 1.4 + i)), 0, 0, TAU); c.stroke(); c.restore();
      }
      c.restore();
      c.restore(); // end clip

      // soft rim light + outer glow
      c.globalCompositeOperation = 'lighter';
      g = c.createRadialGradient(0, 0, 84, 0, 0, 100);
      g.addColorStop(0, rgba(base, 0.28 * e + 0.25 * L + 0.06)); g.addColorStop(1, rgba(base, 0));
      c.fillStyle = g; c.beginPath(); c.arc(0, 0, 100, 0, TAU); c.fill();
      c.strokeStyle = rgba(hi, 0.3 + 0.3 * e); c.lineWidth = 1; c.beginPath(); c.arc(0, 0, 88, 0, TAU); c.stroke();
      c.restore();
      c.globalCompositeOperation = 'source-over';
    }
  }

  // One shared animation loop for every orb on the page.
  const orbs = new Set();
  let running = false, last = 0, frame = 0;
  function tick(now) {
    requestAnimationFrame(tick);
    if (document.hidden) { last = now; return; }
    const dt = Math.min(0.05, (now - last) / 1000 || 0.016); last = now; frame++;
    if (reduce && frame % 5) return;
    const t = now / 1000;
    for (const o of orbs) {
      if (!o.canvas.isConnected) { orbs.delete(o); continue; }
      o.update(t, dt);
      if (o.stateName === 'idle' && (frame & 1)) continue;   // calm orbs run at half rate to save battery
      o.draw(t);
    }
  }
  function start() { if (!running) { running = true; last = performance.now(); requestAnimationFrame(tick); } }

  window.PitchOrb = {
    /** Turn every <canvas data-orb="#colour"> inside root into an orb. */
    attachAll(root) {
      (root || document).querySelectorAll('canvas[data-orb]').forEach(cv => {
        if (cv._orb) return;
        cv._orb = new Orb(cv, cv.dataset.orb); orbs.add(cv._orb); cv._orb.draw(performance.now() / 1000);
      });
      start();
    },
    /** For previews: force a state when the canvas is not inside a .member. */
    force(cv, state) { if (cv._orb) cv._orb.forced = state; },
    /** Change an orb's colour (used by the focus orb to take on the speaker's colour). */
    recolour(cv, colour) {
      const o = cv._orb; if (!o || o.colourKey === colour) return; o.colourKey = colour;
      o.base = hex(colour); o.hi = mix(o.base, [255, 255, 255], 0.6); o.mid = mix(o.base, [255, 255, 255], 0.25); o.deep = mix(o.base, [6, 8, 18], 0.9);
    },
  };
})();
